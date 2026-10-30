import httpx
import pytest
from conftest import make_order

from ai4_pay_gate.canonical import order_hash, parse_order
from ai4_pay_gate.paypal_client import PayPalConfigError, PayPalSandboxClient, approve_link, check_base_url, order_to_paypal


@pytest.mark.parametrize("url", [
    "https://api-m.paypal.com", "https://api.paypal.com", "http://api-m.sandbox.paypal.com",
    "https://api-m.sandbox.paypal.com.evil.example", "https://evil.example/api-m.sandbox.paypal.com",
    "https://api-m.sandbox.paypal.com:8443", "https://user@api-m.sandbox.paypal.com", "https://api-m.sandbox.paypal.com/v2",
])
def test_refuses_non_sandbox_hosts(url):
    with pytest.raises(PayPalConfigError):
        check_base_url(url)


def test_env_live_url_refused(monkeypatch):
    monkeypatch.setenv("PAYPAL_BASE_URL", "https://api-m.paypal.com")
    with pytest.raises(PayPalConfigError):
        PayPalSandboxClient(client_id="a", client_secret="b")


def test_accepts_sandbox():
    assert check_base_url("https://api-m.sandbox.paypal.com/") == "https://api-m.sandbox.paypal.com"


def test_unconfigured_client_refuses_calls(monkeypatch):
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)
    c = PayPalSandboxClient()
    assert not c.configured
    o = parse_order(make_order())
    with pytest.raises(PayPalConfigError):
        c.create_order(o, order_hash(o), "r1")


def test_repr_hides_credentials():
    c = PayPalSandboxClient(client_id="ID-SHOULD-NOT-SHOW", client_secret="SECRET-SHOULD-NOT-SHOW")
    assert "SHOULD-NOT-SHOW" not in repr(c)


def test_create_order_body_and_headers():
    seen = {}

    def h(req: httpx.Request):
        if req.url.path == "/v1/oauth2/token":
            assert req.headers["authorization"].startswith("Basic ")
            assert req.content == b"grant_type=client_credentials"
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        seen["req_id"] = req.headers.get("paypal-request-id")
        seen["body"] = req.read()
        return httpx.Response(201, json={"id": "X1", "status": "CREATED", "links": []})

    c = PayPalSandboxClient(client_id="a", client_secret="b", transport=httpx.MockTransport(h))
    o = parse_order(make_order())
    c.create_order(o, order_hash(o), "req-123")
    assert seen["req_id"] == "req-123"
    body = order_to_paypal(o, order_hash(o))
    pu = body["purchase_units"][0]
    assert body["intent"] == "CAPTURE" and pu["custom_id"] == order_hash(o)
    assert pu["amount"]["value"] == "30.50" and pu["payee"] == {"email_address": o.payee}
    assert sum(float(i["unit_amount"]["value"]) * int(i["quantity"]) for i in pu["items"]) == 30.5


def test_approve_link_only_sandbox_host():
    assert approve_link({"links": [{"rel": "approve", "href": "https://www.paypal.com/x"}]}) is None
    assert approve_link({"links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/x"}]})


def test_paypal_error_has_no_secret():
    def h(req):
        return httpx.Response(401, json={"name": "AUTHENTICATION_FAILURE"}, headers={"paypal-debug-id": "d1"})

    c = PayPalSandboxClient(client_id="a", client_secret="TOPSECRET", transport=httpx.MockTransport(h))
    o = parse_order(make_order())
    with pytest.raises(Exception) as e:
        c.create_order(o, order_hash(o), "r")
    assert "TOPSECRET" not in str(e.value) and "AUTHENTICATION_FAILURE" in str(e.value)


def test_order_body_has_experience_context(monkeypatch):
    o = parse_order(make_order())
    monkeypatch.delenv("AI4PG_PUBLIC_BASE_URL", raising=False)
    ec = order_to_paypal(o, order_hash(o))["payment_source"]["paypal"]["experience_context"]
    assert ec == {"return_url": "http://127.0.0.1:8088/paypal/return", "cancel_url": "http://127.0.0.1:8088/paypal/cancel",
                  "user_action": "PAY_NOW", "shipping_preference": "NO_SHIPPING"}
    monkeypatch.setenv("AI4PG_PUBLIC_BASE_URL", "https://gate.example.org/")
    ec = order_to_paypal(o, order_hash(o))["payment_source"]["paypal"]["experience_context"]
    assert ec["return_url"] == "https://gate.example.org/paypal/return"


@pytest.mark.parametrize("bad", ["ftp://x.org", "https://x.org/?a=1", "javascript:alert(1)", "https://"])
def test_experience_context_rejects_bad_base(bad):
    o = parse_order(make_order())
    with pytest.raises(PayPalConfigError):
        order_to_paypal(o, order_hash(o), public_base=bad)
