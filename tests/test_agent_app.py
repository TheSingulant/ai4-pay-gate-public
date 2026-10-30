import json

import httpx
import pytest
from conftest import PAYEE, make_order
from fastapi.testclient import TestClient

from ai4_pay_gate.agent import OpenAICompatibleProvider, ProviderError, ScriptedProvider, ShoppingAgent
from ai4_pay_gate.app import create_app

TOKEN = "test-approver-token"


def test_agent_uses_catalog_prices_and_drops_hallucinated_skus():
    reply = json.dumps({"items": [{"sku": "SKU-MUG", "quantity": 2}, {"sku": "SKU-FAKE", "quantity": 1}, {"sku": "SKU-PEN-3", "quantity": 999}]})
    p = ShoppingAgent(ScriptedProvider(reply), payee=PAYEE).propose("mugs")
    assert p.order["items"] == [{"sku": "SKU-MUG", "name": "Ceramic mug", "quantity": 2, "unit_amount": "18.00"}]
    assert p.order["total"] == "36.00" and p.provider_label == "SCRIPTED"


def test_openai_compatible_provider_request_shape():
    seen = {}

    def h(req):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"items":[{"sku":"SKU-MUG","quantity":1}]}'}}]})

    prov = OpenAICompatibleProvider("https://llm.example/v1", "k", "m", transport=httpx.MockTransport(h))
    assert prov.label == "LIVE:m"  # label for a real provider; this test itself is a STUB
    assert "k" not in repr(prov).replace("llm.example", "")
    out = ShoppingAgent(prov, payee=PAYEE).propose("a mug")
    assert seen["body"]["model"] == "m" and out.order["total"] == "18.00"


def test_provider_requires_https_and_config():
    with pytest.raises(ProviderError):
        OpenAICompatibleProvider("http://llm.example/v1", "k", "m")
    with pytest.raises(ProviderError):
        OpenAICompatibleProvider("", "", "")


@pytest.fixture
def client(gate):
    return TestClient(create_app(gate=gate, provider=ScriptedProvider(), approver_token=TOKEN))


def test_app_flow(client, stub):
    s = client.get("/api/status").json()
    assert s["paypal"]["base"] == "https://api-m.sandbox.paypal.com" and s["model"]["label"] == "SCRIPTED"
    assert "AI4 Pay Gate" in client.get("/").text
    order = client.post("/api/agent/propose", json={"request": "notebooks"}).json()["order"]
    assert client.post("/api/approve", json={"order": order}).status_code == 401
    assert client.post("/api/approve", json={"order": order}, headers={"X-Approver-Token": "wrong"}).status_code == 401
    a = client.post("/api/approve", json={"order": order}, headers={"X-Approver-Token": TOKEN}).json()
    assert a["ok"]
    bad = dict(order, total="999.00")
    assert client.post("/api/agent/pay", json={"permit_id": a["permit_id"], "order": bad}).json()["ok"] is False
    assert stub.calls == []
    recs = client.get("/api/receipts").json()
    assert len(recs["receipts"]) == 2 and recs["public_key"]


def test_capture_requires_approver(client):
    assert client.post("/api/capture", json={"paypal_order_id": "ABCDE"}).status_code == 401


def test_extract_json_tolerates_reasoning_text():
    from ai4_pay_gate.agent import _extract_json
    txt = '<think>maybe {"x": 1} then {"items": []}</think>\nAnswer: ```json\n{"items": [{"sku": "SKU-MUG", "quantity": 1}], "note": "ok"}\n```'
    assert _extract_json(txt)["note"] == "ok"
    with pytest.raises(ProviderError):
        _extract_json("no json here {broken")


def test_inference_call_cap_enforced():
    n = {"c": 0}

    def h(req):
        n["c"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"items":[{"sku":"SKU-MUG","quantity":1}]}'}}]})

    prov = OpenAICompatibleProvider("https://llm.example/v1", "k", "m", transport=httpx.MockTransport(h), max_calls=2)
    prov.complete("s", "u"); prov.complete("s", "u")
    with pytest.raises(ProviderError):
        prov.complete("s", "u")
    assert n["c"] == 2


def test_paypal_return_and_cancel_never_capture(client, gate, stub):
    a = gate.approve(make_order(), "human")
    oid = gate.pay(a["permit_id"], make_order())["paypal_order_id"]
    stub.approve_all = True
    before = len(gate.receipts.all())
    n = len(stub.calls)
    r = client.get(f"/paypal/return?token={oid}&PayerID=ABCDE12345")
    assert r.status_code == 200 and oid in r.text and "never captures" in r.text and "payer id present: yes" in r.text
    c = client.get(f"/paypal/cancel?token={oid}")
    assert c.status_code == 200 and "cancelled" in c.text
    assert len(stub.calls) == n and len(gate.receipts.all()) == before
    assert "capture" not in str(stub.calls)


def test_paypal_return_escapes_untrusted_params(client):
    r = client.get("/paypal/return", params={"token": "<script>alert(1)</script>", "PayerID": "x\"><b>"})
    assert r.status_code == 200 and "<script>" not in r.text and "<b>" not in r.text and "payer id present: no" in r.text
