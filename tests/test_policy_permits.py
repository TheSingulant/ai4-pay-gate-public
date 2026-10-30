import threading

from conftest import PAYEE, make_order

from ai4_pay_gate.canonical import parse_order
from ai4_pay_gate.permits import PermitStore


def test_policy(policy):
    assert policy.check(parse_order(make_order())).ok
    assert policy.check(parse_order(make_order(payee="x@evil.example.com"))).reason == "payee_not_allowlisted"
    assert policy.check(parse_order(make_order(currency="EUR"))).reason == "currency_not_allowed"
    big = make_order(items=[{"sku": "B", "name": "b", "quantity": 2, "unit_amount": "89.00"}], total="178.00")
    assert policy.check(parse_order(big)).reason == "amount_over_policy_max"


def test_permit_single_use():
    s = PermitStore()
    pid, _ = s.issue("h" * 64)
    assert s.consume(pid, "h" * 64).ok
    assert s.consume(pid, "h" * 64).reason == "permit_already_used"


def test_permit_mismatch_burns():
    s = PermitStore()
    pid, _ = s.issue("a" * 64)
    assert s.consume(pid, "b" * 64).reason == "order_hash_mismatch"
    assert s.consume(pid, "a" * 64).reason == "permit_already_used"


def test_permit_expiry():
    t = [1000.0]
    s = PermitStore(ttl_seconds=10, clock=lambda: t[0])
    pid, _ = s.issue("a" * 64)
    t[0] += 11
    assert s.consume(pid, "a" * 64).reason == "permit_expired"


def test_unknown_permit():
    assert PermitStore().consume("pmt_nope", "a" * 64).reason == "unknown_permit"


def test_concurrent_consume_only_one_wins(tmp_path):
    s = PermitStore(tmp_path / "p.sqlite")
    pid, _ = s.issue("a" * 64)
    results = []
    ts = [threading.Thread(target=lambda: results.append(s.consume(pid, "a" * 64).ok)) for _ in range(16)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results.count(True) == 1
