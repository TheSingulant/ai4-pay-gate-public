import pytest
from conftest import make_order

from ai4_pay_gate.canonical import OrderError, canonical_bytes, order_hash, parse_order


def test_hash_is_stable_and_key_order_independent():
    a = make_order()
    b = dict(reversed(list(make_order().items())))
    assert order_hash(parse_order(a)) == order_hash(parse_order(b))
    assert len(order_hash(parse_order(a))) == 64


@pytest.mark.parametrize("change", [
    {"payee": "sb-other-merchant@business.example.com"},
    {"currency": "EUR"},
    {"description": "changed"},
])
def test_any_field_change_changes_hash(change):
    assert order_hash(parse_order(make_order())) != order_hash(parse_order(make_order(**change)))


def test_item_quantity_change_changes_hash():
    o = make_order()
    o["items"][1]["quantity"] = 2
    o["total"] = "37.00"
    assert order_hash(parse_order(o)) != order_hash(parse_order(make_order()))


def test_payee_email_case_normalised():
    assert parse_order(make_order(payee="SB-Demo-Merchant@business.example.com")).payee == "sb-demo-merchant@business.example.com"


@pytest.mark.parametrize("bad", [
    {"total": "31.00"},
    {"total": 30.5},
    {"total": "30.5"},
    {"currency": "JPY"},
    {"payee": "not-an-email"},
    {"items": []},
    {"extra_field": 1},
])
def test_rejects_malformed(bad):
    with pytest.raises(OrderError):
        parse_order(make_order(**bad))


def test_rejects_bool_and_negative_quantity():
    for q in (True, 0, -1, 1.0):
        o = make_order()
        o["items"][0]["quantity"] = q
        with pytest.raises(OrderError):
            parse_order(o)


def test_canonical_rejects_floats():
    with pytest.raises(OrderError):
        canonical_bytes({"a": 1.5})
