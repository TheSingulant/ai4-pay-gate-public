"""Static payment policy: payee allowlist, per-order maximum, allowed currencies."""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .canonical import Order


@dataclass(frozen=True)
class Decision:
    ok: bool
    reason: str


@dataclass(frozen=True)
class Policy:
    payee_allowlist: frozenset[str]
    max_amount: Decimal
    currencies: frozenset[str]

    @classmethod
    def from_dict(cls, d: dict) -> "Policy":
        payees = frozenset(p.lower() if "@" in p else p for p in d["payee_allowlist"])
        return cls(payee_allowlist=payees, max_amount=Decimal(str(d["max_amount"])), currencies=frozenset(d["currencies"]))

    @classmethod
    def load(cls, path: str | Path) -> "Policy":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def check(self, order: Order) -> Decision:
        if order.payee not in self.payee_allowlist:
            return Decision(False, "payee_not_allowlisted")
        if order.currency not in self.currencies:
            return Decision(False, "currency_not_allowed")
        if Decimal(order.total) > self.max_amount:
            return Decision(False, "amount_over_policy_max")
        return Decision(True, "policy_ok")
