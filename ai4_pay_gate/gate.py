"""The gate: human approval -> single-use permit -> checked payment -> signed receipt.

Ordering guarantee: every check (parse, policy, permit, order hash) runs BEFORE any PayPal
API call. A blocked attempt never reaches PayPal. Every attempt, allowed or blocked,
produces a signed receipt.
"""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from typing import Any

from .canonical import Order, OrderError, order_hash, parse_order
from .paypal_client import PayPalConfigError, PayPalError, PayPalSandboxClient, approve_link, paypal_order_matches
from .permits import PermitStore
from .policy import Policy
from .receipts import ReceiptLog


@dataclass
class _Created:
    order: Order
    order_hash: str
    permit_id: str
    captured: bool = False


def _summary(order: Order | None) -> dict[str, Any]:
    if order is None:
        return {}
    return {"payee": order.payee, "currency": order.currency, "total": order.total, "item_count": len(order.items)}


class Gate:
    def __init__(self, policy: Policy, permits: PermitStore, receipts: ReceiptLog, paypal: PayPalSandboxClient | None, paypal_mode: str = "live_sandbox"):
        self.policy = policy
        self.permits = permits
        self.receipts = receipts
        self.paypal = paypal
        # "live_sandbox" for the real client, "stub" in unit tests. Recorded in every receipt.
        self.paypal_mode = paypal_mode
        self._created: dict[str, _Created] = {}
        self._lock = threading.Lock()

    # ---- human approval -------------------------------------------------------------
    def approve(self, order_data: Any, approver: str) -> dict[str, Any]:
        try:
            order = parse_order(order_data)
        except OrderError as e:
            rec = self.receipts.append({"event": "approval", "decision": "blocked", "reason": f"malformed_order: {e}", "approver": approver})
            return {"ok": False, "reason": "malformed_order", "detail": str(e), "receipt": rec}
        h = order_hash(order)
        d = self.policy.check(order)
        if not d.ok:
            rec = self.receipts.append({"event": "approval", "decision": "blocked", "reason": d.reason, "order_hash": h, "approver": approver, **_summary(order)})
            return {"ok": False, "reason": d.reason, "order_hash": h, "receipt": rec}
        pid, exp = self.permits.issue(h)
        rec = self.receipts.append({"event": "approval", "decision": "approved", "reason": "human_approved", "order_hash": h, "approved_hash": h, "permit_id": pid, "permit_expires": int(exp), "approver": approver, **_summary(order)})
        return {"ok": True, "permit_id": pid, "order_hash": h, "expires": int(exp), "receipt": rec}

    # ---- agent payment attempt ------------------------------------------------------
    def pay(self, permit_id: str, order_data: Any, actor: str = "agent") -> dict[str, Any]:
        base = {"event": "payment_attempt", "permit_id": permit_id, "actor": actor}
        try:
            order = parse_order(order_data)
        except OrderError as e:
            # Burn the permit: a malformed attempt under a permit is treated as tampering.
            self.permits.consume(permit_id, "malformed")
            rec = self.receipts.append({**base, "decision": "blocked", "reason": f"malformed_order: {e}", "paypal": {"called": False}})
            return {"ok": False, "reason": "malformed_order", "receipt": rec}
        h = order_hash(order)
        d = self.policy.check(order)
        if not d.ok:
            self.permits.consume(permit_id, "policy-block")
            rec = self.receipts.append({**base, "decision": "blocked", "reason": d.reason, "order_hash": h, **_summary(order), "paypal": {"called": False}})
            return {"ok": False, "reason": d.reason, "order_hash": h, "receipt": rec}
        c = self.permits.consume(permit_id, h)
        if not c.ok:
            rec = self.receipts.append({**base, "decision": "blocked", "reason": c.reason, "order_hash": h, "approved_hash": c.approved_hash, **_summary(order), "paypal": {"called": False}})
            return {"ok": False, "reason": c.reason, "order_hash": h, "receipt": rec}
        # All gate checks passed. Only now may PayPal be called.
        if self.paypal is None or not self.paypal.configured:
            rec = self.receipts.append({**base, "decision": "allowed", "reason": "gate_ok_paypal_not_configured", "order_hash": h, "approved_hash": c.approved_hash, **_summary(order), "paypal": {"called": False, "mode": "not_configured"}})
            return {"ok": True, "paypal_called": False, "order_hash": h, "receipt": rec}
        req_id = "ai4pg-" + secrets.token_hex(12)
        try:
            ppo = self.paypal.create_order(order, h, req_id)
        except (PayPalError, PayPalConfigError) as e:
            pp = {"called": True, "mode": self.paypal_mode, "error": getattr(e, "name", None) or "config", "status": getattr(e, "status", None), "debug_id": getattr(e, "debug_id", None)}
            rec = self.receipts.append({**base, "decision": "allowed", "reason": "gate_ok_paypal_error", "order_hash": h, "approved_hash": c.approved_hash, **_summary(order), "paypal": pp})
            return {"ok": False, "reason": "paypal_error", "receipt": rec}
        oid = ppo.get("id", "")
        with self._lock:
            self._created[oid] = _Created(order, h, permit_id)
        pp = {"called": True, "mode": self.paypal_mode, "op": "create_order", "order_id": oid, "status": ppo.get("status"), "request_id": req_id, "debug_id": getattr(self.paypal, "last_debug_id", None)}
        rec = self.receipts.append({**base, "decision": "allowed", "reason": "gate_ok_order_created", "order_hash": h, "approved_hash": c.approved_hash, **_summary(order), "paypal": pp})
        return {"ok": True, "paypal_called": True, "paypal_order_id": oid, "approve_url": approve_link(ppo), "order_hash": h, "receipt": rec}

    def adopt(self, paypal_order_id: str, order_data: Any, permit_id: str) -> str:
        """Re-register a gate-created PayPal order after a restart (e.g. a later capture run).
        The order is re-parsed and re-hashed; capture still re-reads PayPal before capturing."""
        order = parse_order(order_data)
        h = order_hash(order)
        with self._lock:
            self._created[paypal_order_id] = _Created(order, h, permit_id)
        return h

    # ---- capture after buyer approval on PayPal ---------------------------------------
    def capture(self, paypal_order_id: str) -> dict[str, Any]:
        base = {"event": "capture_attempt", "paypal_order_id": paypal_order_id}
        with self._lock:
            created = self._created.get(paypal_order_id)
            if created is None or created.captured:
                reason = "unknown_paypal_order" if created is None else "already_captured"
                rec = self.receipts.append({**base, "decision": "blocked", "reason": reason, "paypal": {"called": False}})
                return {"ok": False, "reason": reason, "receipt": rec}
            created.captured = True  # reserve; one capture attempt per gate-created order
        if self.paypal is None or not self.paypal.configured:
            rec = self.receipts.append({**base, "decision": "blocked", "reason": "paypal_not_configured", "paypal": {"called": False}})
            return {"ok": False, "reason": "paypal_not_configured", "receipt": rec}
        common = {"order_hash": created.order_hash, "permit_id": created.permit_id, **_summary(created.order)}
        try:
            current = self.paypal.get_order(paypal_order_id)
            dbg = getattr(self.paypal, "last_debug_id", None)
            if not paypal_order_matches(current, created.order, created.order_hash):
                rec = self.receipts.append({**base, **common, "decision": "blocked", "reason": "paypal_order_differs_from_approval", "readback_match": False, "paypal": {"called": True, "mode": self.paypal_mode, "op": "get_order", "debug_id": dbg}})
                return {"ok": False, "reason": "paypal_order_differs_from_approval", "receipt": rec}
            if current.get("status") != "APPROVED":
                with self._lock:
                    created.captured = False  # buyer has not approved yet; allow a later try
                rec = self.receipts.append({**base, **common, "decision": "blocked", "reason": f"paypal_status_{current.get('status')}", "readback_match": True, "paypal": {"called": True, "mode": self.paypal_mode, "op": "get_order", "status": current.get("status"), "debug_id": dbg}})
                return {"ok": False, "reason": "buyer_not_approved_yet", "paypal_status": current.get("status"), "receipt": rec}
            cap = self.paypal.capture_order(paypal_order_id, "ai4pg-cap-" + secrets.token_hex(10))
        except (PayPalError, PayPalConfigError) as e:
            rec = self.receipts.append({**base, **common, "decision": "allowed", "reason": "gate_ok_paypal_error", "paypal": {"called": True, "mode": self.paypal_mode, "error": getattr(e, "name", None), "status": getattr(e, "status", None), "debug_id": getattr(e, "debug_id", None)}})
            return {"ok": False, "reason": "paypal_error", "receipt": rec}
        cap_ids = [x.get("id") for pu in cap.get("purchase_units", []) for x in pu.get("payments", {}).get("captures", [])]
        rec = self.receipts.append({**base, **common, "decision": "allowed", "reason": "captured", "readback_match": True, "paypal": {"called": True, "mode": self.paypal_mode, "op": "capture", "status": cap.get("status"), "capture_ids": cap_ids}})
        return {"ok": True, "status": cap.get("status"), "receipt": rec}
