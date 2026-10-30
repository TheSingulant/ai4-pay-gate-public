"""Canonical order representation and hashing.

An order is reduced to one canonical JSON byte string. The SHA-256 of that string is the
order hash that a human approval is bound to. Any change to payee, currency, items,
quantities, prices or total changes the hash.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

CANON_VERSION = "ai4pg-order-v1"

# PayPal accepts these as 2-decimal currencies in the Orders API. JPY etc. are deliberately
# not supported in this reference build to keep amount handling exact.
SUPPORTED_CURRENCIES = {"USD", "EUR", "GBP", "CAD", "AUD"}

_AMOUNT_RE = re.compile(r"^(0|[1-9][0-9]{0,8})\.[0-9]{2}$")
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}$")
_MERCHANT_RE = re.compile(r"^[A-Z0-9]{13}$")
_SKU_RE = re.compile(r"^[A-Za-z0-9._-]{1,127}$")


class OrderError(ValueError):
    """Raised when an order is malformed. Malformed orders are never sent to PayPal."""


def _amount(value: Any, what: str) -> Decimal:
    if not isinstance(value, str) or not _AMOUNT_RE.match(value):
        raise OrderError(f"{what} must be a string with exactly 2 decimals, e.g. '12.50'")
    try:
        return Decimal(value)
    except InvalidOperation as exc:  # pragma: no cover - regex already guards
        raise OrderError(f"{what} is not a number") from exc


def _fmt(d: Decimal) -> str:
    return f"{d.quantize(Decimal('0.01')):.2f}"


@dataclass(frozen=True)
class Item:
    sku: str
    name: str
    quantity: int
    unit_amount: str

    def to_dict(self) -> dict[str, Any]:
        return {"sku": self.sku, "name": self.name, "quantity": self.quantity, "unit_amount": self.unit_amount}


@dataclass(frozen=True)
class Order:
    payee: str  # PayPal sandbox merchant email or 13 char merchant id
    currency: str
    items: tuple[Item, ...]
    total: str
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "payee": self.payee,
            "currency": self.currency,
            "items": [i.to_dict() for i in self.items],
            "total": self.total,
            "description": self.description,
        }


def parse_order(data: Any) -> Order:
    """Strictly validate an untrusted order dict (from the agent or an API caller)."""
    if not isinstance(data, dict):
        raise OrderError("order must be an object")
    allowed = {"payee", "currency", "items", "total", "description"}
    unknown = set(data) - allowed
    if unknown:
        raise OrderError(f"unknown order fields: {sorted(unknown)}")
    payee = data.get("payee")
    if not isinstance(payee, str) or not (_EMAIL_RE.match(payee) or _MERCHANT_RE.match(payee)):
        raise OrderError("payee must be a merchant email or 13 character PayPal merchant id")
    payee = payee.strip().lower() if "@" in payee else payee
    currency = data.get("currency")
    if currency not in SUPPORTED_CURRENCIES:
        raise OrderError(f"currency must be one of {sorted(SUPPORTED_CURRENCIES)}")
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 50:
        raise OrderError("items must be a list of 1 to 50 entries")
    items: list[Item] = []
    computed = Decimal("0")
    for idx, it in enumerate(raw_items):
        if not isinstance(it, dict) or set(it) - {"sku", "name", "quantity", "unit_amount"}:
            raise OrderError(f"item {idx} has unknown or missing fields")
        sku, name, qty = it.get("sku"), it.get("name"), it.get("quantity")
        if not isinstance(sku, str) or not _SKU_RE.match(sku):
            raise OrderError(f"item {idx} sku invalid")
        if not isinstance(name, str) or not 1 <= len(name) <= 127:
            raise OrderError(f"item {idx} name invalid")
        if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= 1000:
            raise OrderError(f"item {idx} quantity must be an integer 1..1000")
        unit = _amount(it.get("unit_amount"), f"item {idx} unit_amount")
        computed += unit * qty
        items.append(Item(sku=sku, name=name.strip(), quantity=qty, unit_amount=_fmt(unit)))
    total = _amount(data.get("total"), "total")
    if total != computed:
        raise OrderError(f"total {_fmt(total)} does not equal sum of items {_fmt(computed)}")
    if total <= 0:
        raise OrderError("total must be positive")
    desc = data.get("description", "")
    if not isinstance(desc, str) or len(desc) > 127:
        raise OrderError("description must be a string of at most 127 characters")
    return Order(payee=payee, currency=currency, items=tuple(items), total=_fmt(total), description=desc.strip())


def canonical_bytes(obj: Any) -> bytes:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8, no floats allowed."""

    def check(o: Any) -> None:
        if isinstance(o, float):
            raise OrderError("floats are not allowed in canonical data")
        if isinstance(o, dict):
            for v in o.values():
                check(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                check(v)

    check(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def order_canonical(order: Order) -> bytes:
    return canonical_bytes({"v": CANON_VERSION, "order": order.to_dict()})


def order_hash(order: Order) -> str:
    return hashlib.sha256(order_canonical(order)).hexdigest()
