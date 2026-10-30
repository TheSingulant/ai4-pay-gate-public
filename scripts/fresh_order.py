#!/usr/bin/env python3
"""LIVE: one fresh PayPal SANDBOX order through the gate, left CREATED for buyer approval.

Nemotron (Nebius Token Factory) builds the cart (cap AI4PG_LLM_MAX_CALLS, default 2), the
operator script approves the exact cart, the gate creates the order with return/cancel URLs.
Nothing is captured here. After the buyer approves, run scripts/capture_live.py on the folder.
Credentials come from env and are never printed or written.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai4_pay_gate.agent import OpenAICompatibleProvider, ProviderError, ShoppingAgent  # noqa: E402
from ai4_pay_gate.gate import Gate  # noqa: E402
from ai4_pay_gate.paypal_client import PayPalSandboxClient, experience_context  # noqa: E402
from ai4_pay_gate.permits import PermitStore  # noqa: E402
from ai4_pay_gate.policy import Policy  # noqa: E402
from ai4_pay_gate.receipts import ReceiptLog, load_or_create_key  # noqa: E402

NEBIUS_BASE = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1")
NEBIUS_MODEL = os.environ.get("NEBIUS_MODEL", "nvidia/Nemotron-3.5-Super-VL")
MAX_INFERENCE = min(int(os.environ.get("AI4PG_LLM_MAX_CALLS", "2")), 2)
REQUEST = "Two dot grid notebooks and one gel pen pack for a new hire's first day."

ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
out = ROOT / "evidence" / f"live-{ts}"
out.mkdir(parents=True)
keydir = ROOT / "data" / f"live-{ts}"
keydir.mkdir(parents=True)
log: list[str] = []


def say(line: str = "") -> None:
    print(line)
    log.append(line)


def utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


policy = Policy.load(ROOT / "policy" / "demo-policy.json")
paypal = PayPalSandboxClient()
assert paypal.configured, "PayPal sandbox credentials missing from env"
gate = Gate(policy, PermitStore(keydir / "permits.sqlite", 600), ReceiptLog(load_or_create_key(keydir / "receipt_signing.key"), out / "receipts.jsonl"), paypal, paypal_mode="live_sandbox")
(out / "public_key.txt").write_text(gate.receipts.pub + "\n")
say(f"# AI4 Pay Gate LIVE fresh order {ts}")
say(f"Labels: PayPal = LIVE (sandbox, {paypal.base}); model = LIVE ({NEBIUS_MODEL}); approval click = operator-script; capture = NOT performed")
say(f"experience_context: {json.dumps(experience_context(), sort_keys=True)}")

prov = OpenAICompatibleProvider(NEBIUS_BASE, os.environ["NEBIUS_API_KEY"], NEBIUS_MODEL, max_calls=MAX_INFERENCE, max_tokens=2000)
agent = ShoppingAgent(prov, payee=sorted(policy.payee_allowlist)[0])
proposal, calls = None, []
while proposal is None and prov.calls < MAX_INFERENCE:
    t0 = time.time()
    try:
        proposal = agent.propose(REQUEST)
        calls.append({"ts": utc(), "ok": True, "seconds": round(time.time() - t0, 1), "usage": prov.last_usage, "served_model": prov.served_model, "content": (prov.last_raw or "")[:4000]})
    except ProviderError as e:
        calls.append({"ts": utc(), "ok": False, "error": str(e), "seconds": round(time.time() - t0, 1), "usage": prov.last_usage, "content": (prov.last_raw or "")[:4000]})
(out / "model_calls.json").write_text(json.dumps({"base_url": NEBIUS_BASE, "requested_model": NEBIUS_MODEL, "request": REQUEST, "inference_calls": prov.calls, "cap": MAX_INFERENCE, "calls": calls}, indent=1))
say(f"inference calls used: {prov.calls} (cap {MAX_INFERENCE}); served model: {prov.served_model}")
if proposal is None:
    say("Model did not produce a valid cart; stopping before PayPal.")
    (out / "transcript.md").write_text("\n".join(log) + "\n")
    sys.exit(3)
order = proposal.order
(out / "approved_order.json").write_text(json.dumps(order, indent=1, sort_keys=True))
say("proposed order: " + json.dumps(order, sort_keys=True))
a = gate.approve(order, "operator-script")
say(f"{utc()} approval: ok={a['ok']} permit={a.get('permit_id')} order_hash={a.get('order_hash')}")
r = gate.pay(a["permit_id"], order, actor=f"agent:{proposal.provider_label}")
b = r["receipt"]["body"]
say(f"{utc()} pay: ok={r['ok']} reason={b['reason']} order={b['paypal'].get('order_id')} status={b['paypal'].get('status')} debug_id={b['paypal'].get('debug_id')}")
say(f"buyer approval link (sandbox): {r.get('approve_url')}")
say("Capture: NOT performed. Pending buyer approval, then scripts/capture_live.py on this folder.")
(out / "paypal_http_audit.json").write_text(json.dumps(paypal.audit, indent=1))
(out / "export.json").write_text(json.dumps({"public_key": gate.receipts.pub, "kid": gate.receipts.kid, "receipts": gate.receipts.all()}, indent=1))
(out / "transcript.md").write_text("\n".join(log) + "\n")
print(f"EVIDENCE_DIR={out.relative_to(ROOT)}")
