import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai4_pay_gate.gate import Gate  # noqa: E402
from ai4_pay_gate.paypal_client import PayPalSandboxClient  # noqa: E402
from ai4_pay_gate.permits import PermitStore  # noqa: E402
from ai4_pay_gate.policy import Policy  # noqa: E402
from ai4_pay_gate.receipts import ReceiptLog, load_or_create_key  # noqa: E402

PAYEE = "sb-demo-merchant@business.example.com"
PAYEE2 = "sb-other-merchant@business.example.com"


def make_order(**kw):
    o = {
        "payee": PAYEE,
        "currency": "USD",
        "items": [
            {"sku": "SKU-NOTEBOOK", "name": "Dot grid notebook", "quantity": 2, "unit_amount": "12.00"},
            {"sku": "SKU-PEN-3", "name": "Gel pen 3 pack", "quantity": 1, "unit_amount": "6.50"},
        ],
        "total": "30.50",
        "description": "test order",
    }
    o.update(kw)
    return o


class StubPayPal:
    """Unit-level STUB of the PayPal sandbox API (httpx MockTransport). Not live evidence."""

    def __init__(self):
        self.calls = []
        self.orders = {}
        self.approve_all = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        assert request.url.host == "api-m.sandbox.paypal.com"
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "stub-access", "expires_in": 3600})
        if request.url.path == "/v2/checkout/orders" and request.method == "POST":
            body = json.loads(request.content)
            oid = f"STUB{len(self.orders) + 1:08d}"
            pu = body["purchase_units"][0]
            self.orders[oid] = {"id": oid, "status": "CREATED", "purchase_units": [{"custom_id": pu["custom_id"], "amount": pu["amount"], "payee": pu["payee"]}],
                                "links": [{"rel": "approve", "href": f"https://www.sandbox.paypal.com/checkoutnow?token={oid}"}]}
            return httpx.Response(201, json=self.orders[oid])
        parts = request.url.path.split("/")
        if len(parts) >= 5 and parts[3] == "orders":
            oid = parts[4]
            o = self.orders.get(oid)
            if o is None:
                return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"}, headers={"paypal-debug-id": "dbg1"})
            if self.approve_all and o["status"] == "CREATED":
                o["status"] = "APPROVED"
            if request.method == "GET":
                return httpx.Response(200, json=o)
            if parts[-1] == "capture":
                o["status"] = "COMPLETED"
                return httpx.Response(201, json={"id": oid, "status": "COMPLETED", "purchase_units": [{"payments": {"captures": [{"id": "CAP" + oid}]}}]})
        return httpx.Response(400, json={"name": "UNEXPECTED"})


@pytest.fixture
def policy():
    return Policy.from_dict({"payee_allowlist": [PAYEE, PAYEE2], "max_amount": "150.00", "currencies": ["USD"]})


@pytest.fixture
def stub():
    return StubPayPal()


@pytest.fixture
def gate(tmp_path, policy, stub):
    key = load_or_create_key(tmp_path / "k")
    pp = PayPalSandboxClient(client_id="stub-id", client_secret="stub-secret", transport=httpx.MockTransport(stub.handler))
    return Gate(policy, PermitStore(tmp_path / "p.sqlite", 600), ReceiptLog(key, tmp_path / "r.jsonl"), pp, paypal_mode="stub")
