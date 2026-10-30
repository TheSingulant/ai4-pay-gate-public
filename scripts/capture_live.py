#!/usr/bin/env python3
"""LIVE sandbox capture of an order created by scripts/live_phase2.py, after buyer approval.

Run only after the sandbox buyer has approved the order at its approval link.
Usage: python scripts/capture_live.py evidence/live-<UTC>
The gate re-reads the real sandbox order and captures only if payee, amount, currency and
the approved order hash in custom_id still match. Receipts are appended to that run's chain.
Credentials come from env and are never printed.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ai4_pay_gate.gate import Gate  # noqa: E402
from ai4_pay_gate.paypal_client import PayPalSandboxClient  # noqa: E402
from ai4_pay_gate.permits import PermitStore  # noqa: E402
from ai4_pay_gate.policy import Policy  # noqa: E402
from ai4_pay_gate.receipts import ReceiptLog, load_or_create_key  # noqa: E402

ev = Path(sys.argv[1]).resolve()
keydir = ROOT / "data" / ev.name
order = json.loads((ev / "approved_order.json").read_text())
first = [json.loads(l) for l in (ev / "receipts.jsonl").read_text().splitlines() if l.strip()]
created = next(r["body"] for r in first if r["body"].get("reason") == "gate_ok_order_created")
pp = PayPalSandboxClient()
gate = Gate(Policy.load(ROOT / "policy" / "demo-policy.json"), PermitStore(keydir / "permits.sqlite"),
            ReceiptLog(load_or_create_key(keydir / "receipt_signing.key"), ev / "receipts.jsonl"), pp, paypal_mode="live_sandbox")
oid = created["paypal"]["order_id"]
gate.adopt(oid, order, created["permit_id"])
r = gate.capture(oid)
b = r["receipt"]["body"]
print(f"order {oid}: decision={b['decision']} reason={b['reason']} readback_match={b.get('readback_match')} paypal={json.dumps(b['paypal'], sort_keys=True)}")
(ev / "paypal_http_audit_capture.json").write_text(json.dumps(pp.audit, indent=1))
(ev / "export.json").write_text(json.dumps({"public_key": gate.receipts.pub, "kid": gate.receipts.kid, "receipts": gate.receipts.all()}, indent=1))
