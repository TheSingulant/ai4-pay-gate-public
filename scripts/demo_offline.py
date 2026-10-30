#!/usr/bin/env python3
"""SCRIPTED offline walkthrough. No network, no PayPal call, no model call.

Shows the gate decisions for: pay as approved, raised amount, swapped payee, added item,
retry. PayPal is NOT configured here, so allowed attempts are recorded as
"gate_ok_paypal_not_configured". This output is not live evidence.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai4_pay_gate.agent import ScriptedProvider, ShoppingAgent, tamper  # noqa: E402
from ai4_pay_gate.gate import Gate  # noqa: E402
from ai4_pay_gate.permits import PermitStore  # noqa: E402
from ai4_pay_gate.policy import Policy  # noqa: E402
from ai4_pay_gate.receipts import ReceiptLog, load_or_create_key  # noqa: E402

root = Path(__file__).resolve().parents[1]
policy = Policy.load(root / "policy" / "demo-policy.json")
d = Path(tempfile.mkdtemp(prefix="ai4pg-demo-"))
key = load_or_create_key(d / "k")
gate = Gate(policy, PermitStore(), ReceiptLog(key, d / "receipts.jsonl"), paypal=None, paypal_mode="not_configured")
agent = ShoppingAgent(ScriptedProvider(), payee=sorted(policy.payee_allowlist)[0])
order = agent.propose("two notebooks and a pen pack").order
print("[SCRIPTED] proposal total", order["total"], order["currency"], "to", order["payee"])
for mode in ("raise_amount", "swap_payee", "add_item", "none", "retry"):
    pid = gate.approve(order, "demo-human")["permit_id"] if mode != "retry" else last_pid
    r = gate.pay(pid, tamper(order, "none" if mode == "retry" else mode))
    last_pid = pid
    b = r["receipt"]["body"]
    print(f"[SCRIPTED] {mode:13s} -> {b['decision']:8s} {b['reason']:32s} paypal_called={b['paypal']['called']}")
(d / "export.json").write_text(json.dumps({"public_key": gate.receipts.pub, "receipts": gate.receipts.all()}))
print("receipts written to", d / "receipts.jsonl")
print("verify with: python verify.py", d / "export.json")
