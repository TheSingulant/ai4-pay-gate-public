import pytest
from conftest import PAYEE2, make_order

from ai4_pay_gate.agent import tamper
from ai4_pay_gate.receipts import verify_receipts


def paypal_create_calls(stub):
    return [c for c in stub.calls if c == ("POST", "/v2/checkout/orders")]


def test_exact_order_allowed_and_paypal_called_once(gate, stub):
    a = gate.approve(make_order(), "human")
    r = gate.pay(a["permit_id"], make_order())
    assert r["ok"] and r["paypal_called"]
    assert r["approve_url"].startswith("https://www.sandbox.paypal.com/")
    assert len(paypal_create_calls(stub)) == 1
    body = r["receipt"]["body"]
    assert body["paypal"]["mode"] == "stub"  # never presented as live
    assert body["order_hash"] == body["approved_hash"] == a["order_hash"]


@pytest.mark.parametrize("mode", ["raise_amount", "add_item"])
def test_tampered_order_blocked_before_paypal(gate, stub, mode):
    a = gate.approve(make_order(), "human")
    r = gate.pay(a["permit_id"], tamper(make_order(), mode))
    assert not r["ok"] and r["reason"] == "order_hash_mismatch"
    assert stub.calls == []  # not even an OAuth call
    assert r["receipt"]["body"]["paypal"] == {"called": False}


def test_swap_to_non_allowlisted_payee_blocked_by_policy(gate, stub):
    a = gate.approve(make_order(), "human")
    r = gate.pay(a["permit_id"], tamper(make_order(), "swap_payee"))
    assert r["reason"] == "payee_not_allowlisted" and stub.calls == []


def test_swap_to_other_allowlisted_payee_blocked_by_hash(gate, stub):
    a = gate.approve(make_order(), "human")
    r = gate.pay(a["permit_id"], make_order(payee=PAYEE2))
    assert r["reason"] == "order_hash_mismatch" and stub.calls == []


def test_tamper_burns_permit_so_honest_retry_fails(gate, stub):
    a = gate.approve(make_order(), "human")
    gate.pay(a["permit_id"], tamper(make_order(), "raise_amount"))
    r = gate.pay(a["permit_id"], make_order())
    assert r["reason"] == "permit_already_used" and stub.calls == []


def test_retry_blocked(gate, stub):
    a = gate.approve(make_order(), "human")
    assert gate.pay(a["permit_id"], make_order())["ok"]
    r = gate.pay(a["permit_id"], make_order())
    assert r["reason"] == "permit_already_used"
    assert len(paypal_create_calls(stub)) == 1


def test_approval_refused_over_policy(gate):
    big = make_order(items=[{"sku": "B", "name": "b", "quantity": 2, "unit_amount": "89.00"}], total="178.00")
    r = gate.approve(big, "human")
    assert not r["ok"] and r["reason"] == "amount_over_policy_max"


def test_malformed_pay_blocked(gate, stub):
    a = gate.approve(make_order(), "human")
    r = gate.pay(a["permit_id"], {"payee": "x"})
    assert r["reason"] == "malformed_order" and stub.calls == []
    assert gate.pay(a["permit_id"], make_order())["reason"] == "permit_already_used"


def test_capture_requires_buyer_approval_then_once(gate, stub):
    a = gate.approve(make_order(), "human")
    oid = gate.pay(a["permit_id"], make_order())["paypal_order_id"]
    assert gate.capture(oid)["reason"] == "buyer_not_approved_yet"
    stub.approve_all = True
    assert gate.capture(oid)["ok"]
    assert gate.capture(oid)["reason"] == "already_captured"


def test_capture_blocks_if_paypal_side_order_differs(gate, stub):
    a = gate.approve(make_order(), "human")
    oid = gate.pay(a["permit_id"], make_order())["paypal_order_id"]
    stub.approve_all = True
    stub.orders[oid]["purchase_units"][0]["amount"]["value"] = "99.00"
    r = gate.capture(oid)
    assert r["reason"] == "paypal_order_differs_from_approval"
    assert ("POST", f"/v2/checkout/orders/{oid}/capture") not in stub.calls


def test_capture_unknown_order(gate):
    assert gate.capture("NOTCREATEDBYGATE")["reason"] == "unknown_paypal_order"


def test_every_attempt_has_valid_signed_receipt(gate):
    a = gate.approve(make_order(), "human")
    gate.pay(a["permit_id"], tamper(make_order(), "raise_amount"))
    gate.pay(a["permit_id"], make_order())
    recs = gate.receipts.all()
    assert [r["body"]["decision"] for r in recs] == ["approved", "blocked", "blocked"]
    ok, errs = verify_receipts(recs, gate.receipts.pub)
    assert ok, errs


def test_paypal_audit_trail_has_no_secrets_and_records_readback(gate, stub):
    a = gate.approve(make_order(), "human")
    oid = gate.pay(a["permit_id"], make_order())["paypal_order_id"]
    r = gate.capture(oid)
    assert r["receipt"]["body"]["readback_match"] is True
    paths = [(x["method"], x["path"]) for x in gate.paypal.audit]
    assert paths == [("POST", "/v1/oauth2/token"), ("POST", "/v2/checkout/orders"), ("GET", f"/v2/checkout/orders/{oid}")]
    blob = repr(gate.paypal.audit) + repr(gate.receipts.all())
    assert "stub-secret" not in blob and "stub-access" not in blob


def test_adopt_then_capture_after_restart(gate, stub):
    a = gate.approve(make_order(), "human")
    oid = gate.pay(a["permit_id"], make_order())["paypal_order_id"]
    gate._created.clear()  # simulate restart
    assert gate.capture(oid)["reason"] == "unknown_paypal_order"
    gate.adopt(oid, make_order(), a["permit_id"])
    stub.approve_all = True
    assert gate.capture(oid)["ok"]
