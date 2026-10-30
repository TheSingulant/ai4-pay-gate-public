#!/usr/bin/env python3
"""LIVE evidence run: PayPal SANDBOX + Nebius Token Factory (Nemotron).

Reads PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET and NEBIUS_API_KEY from the environment and
never prints, logs or writes them. Writes redacted evidence to evidence/live-<UTC>/.
The human approval click is performed by this script on the operator's behalf and is
labelled as such in every approval receipt (approver="operator-script").
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai4_pay_gate.agent import OpenAICompatibleProvider, ProviderError, ShoppingAgent, tamper  # noqa: E402
from ai4_pay_gate.canonical import order_hash, parse_order  # noqa: E402
from ai4_pay_gate.gate import Gate  # noqa: E402
from ai4_pay_gate.paypal_client import PayPalSandboxClient, paypal_order_matches  # noqa: E402
from ai4_pay_gate.permits import PermitStore  # noqa: E402
from ai4_pay_gate.policy import Policy  # noqa: E402
from ai4_pay_gate.receipts import ReceiptLog, load_or_create_key  # noqa: E402

NEBIUS_BASE = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1")
NEBIUS_MODEL = os.environ.get("NEBIUS_MODEL", "nvidia/Nemotron-3.5-Super-VL")
MAX_INFERENCE = int(os.environ.get("AI4PG_LLM_MAX_CALLS", "3"))
REQUEST = "Two dot grid notebooks and one gel pen pack for a new hire's first day."

ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
out = ROOT / "evidence" / f"live-{ts}"
out.mkdir(parents=True)
keydir = ROOT / "data" / f"live-{ts}"  # gitignored; signing key never leaves here
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
key = load_or_create_key(keydir / "receipt_signing.key")
gate = Gate(policy, PermitStore(keydir / "permits.sqlite", 600), ReceiptLog(key, out / "receipts.jsonl"), paypal, paypal_mode="live_sandbox")
say(f"# AI4 Pay Gate LIVE evidence run {ts}")
say(f"Labels: PayPal = LIVE (sandbox, {paypal.base}); model = LIVE ({NEBIUS_MODEL} via Nebius Token Factory); approval click = performed by operator-script")
say(f"Receipt public key: {gate.receipts.pub} ({gate.receipts.kid})")
say("")

# ---- step 4: live Nemotron agent builds the cart ---------------------------------------
say("## Step 4. LIVE Nemotron agent builds the cart from the catalog")
prov = OpenAICompatibleProvider(NEBIUS_BASE, os.environ["NEBIUS_API_KEY"], NEBIUS_MODEL, max_calls=MAX_INFERENCE, max_tokens=2000)
agent = ShoppingAgent(prov, payee=sorted(policy.payee_allowlist)[0])
proposal = None
model_calls = []
while proposal is None:
    t0 = time.time()
    try:
        proposal = agent.propose(REQUEST)
    except ProviderError as e:
        model_calls.append({"ts": utc(), "ok": False, "error": str(e), "seconds": round(time.time() - t0, 1), "usage": prov.last_usage, "served_model": prov.served_model, "content": (prov.last_raw or "")[:4000]})
        say(f"{utc()} model call {prov.calls}: {e}")
        if "cap reached" in str(e) or prov.calls >= MAX_INFERENCE:
            break
        continue
    model_calls.append({"ts": utc(), "ok": True, "seconds": round(time.time() - t0, 1), "usage": prov.last_usage, "served_model": prov.served_model, "content": (prov.last_raw or "")[:4000]})
(out / "model_calls.json").write_text(json.dumps({"base_url": NEBIUS_BASE, "requested_model": NEBIUS_MODEL, "request": REQUEST, "inference_calls": prov.calls, "calls": model_calls}, indent=1))
say(f"inference calls used: {prov.calls} (cap {MAX_INFERENCE}); served model: {prov.served_model}")
if proposal is None:
    say("Model did not produce a valid cart; stopping before PayPal.")
    (out / "transcript.md").write_text("\n".join(log) + "\n")
    sys.exit(3)
order = proposal.order
say(f"agent label: {proposal.provider_label}; note: {proposal.note}")
say("proposed order: " + json.dumps(order, sort_keys=True))
(out / "approved_order.json").write_text(json.dumps(order, indent=1, sort_keys=True))
say("")


def pp_calls() -> int:
    return len(paypal.audit)


# ---- step 1: approve + live sandbox OAuth + create order --------------------------------
say("## Step 1. Approve exact cart, gate allow path, LIVE sandbox OAuth + create order")
a1 = gate.approve(order, "operator-script")
say(f"{utc()} approval: ok={a1['ok']} permit={a1.get('permit_id')} order_hash={a1.get('order_hash')}")
n0 = pp_calls()
r1 = gate.pay(a1["permit_id"], order, actor=f"agent:{proposal.provider_label}")
b = r1["receipt"]["body"]
say(f"{utc()} pay: ok={r1['ok']} reason={b['reason']} paypal_http_calls={pp_calls() - n0}")
say(f"  sandbox order id: {b['paypal'].get('order_id')} status: {b['paypal'].get('status')} debug_id: {b['paypal'].get('debug_id')}")
say(f"  buyer approval link (sandbox): {r1.get('approve_url')}")
oid = b["paypal"].get("order_id")
say("")

# ---- step 2: live blocked attempts -------------------------------------------------------
say("## Step 2. Blocked attempts (each under a fresh human approval of the original cart), no PayPal call")
blocked = []
for mode in ("raise_amount", "swap_payee", "add_item"):
    a = gate.approve(order, "operator-script")
    n0 = pp_calls()
    r = gate.pay(a["permit_id"], tamper(order, mode), actor=f"agent:{proposal.provider_label}")
    d = pp_calls() - n0
    blocked.append((mode, r["receipt"]["body"]["reason"], d))
    say(f"{utc()} {mode}: decision={r['receipt']['body']['decision']} reason={r['receipt']['body']['reason']} paypal_http_calls={d}")
n0 = pp_calls()
r = gate.pay(a1["permit_id"], order, actor=f"agent:{proposal.provider_label}")
d = pp_calls() - n0
blocked.append(("replay", r["receipt"]["body"]["reason"], d))
say(f"{utc()} replay of step 1 permit with the exact approved order: decision={r['receipt']['body']['decision']} reason={r['receipt']['body']['reason']} paypal_http_calls={d}")
say("")

# ---- step 3: read-back before capture -----------------------------------------------------
say("## Step 3. Read-back of the real sandbox order before capture")
cap = gate.capture(oid) if oid else {"ok": False, "reason": "no_order"}
cb = cap["receipt"]["body"]
say(f"{utc()} capture attempt: decision={cb['decision']} reason={cb['reason']} readback_match={cb.get('readback_match')} paypal_status={cb['paypal'].get('status')} debug_id={cb['paypal'].get('debug_id')}")
real = paypal.get_order(oid) if oid else {}
pu = (real.get("purchase_units") or [{}])[0]
snapshot = {
    "id": real.get("id"), "status": real.get("status"), "intent": real.get("intent"),
    "custom_id": pu.get("custom_id"), "amount": pu.get("amount"), "payee": pu.get("payee"),
    "items": pu.get("items"), "links": [{"rel": l.get("rel"), "href": l.get("href")} for l in real.get("links", [])],
    "fetched": utc(), "debug_id": paypal.last_debug_id,
}
(out / "sandbox_order_readback.json").write_text(json.dumps(snapshot, indent=1, sort_keys=True))
checks = {"approved_order": paypal_order_matches(real, parse_order(order), order_hash(parse_order(order)))}
for mode in ("raise_amount", "add_item"):
    t = parse_order(tamper(order, mode))
    checks[mode] = paypal_order_matches(real, t, order_hash(t))
t = parse_order(dict(order, payee="sb-other@business.example.com"))
checks["other_payee"] = paypal_order_matches(real, t, order_hash(t))
say(f"read-back comparison of the real sandbox order vs: {checks}  (only approved_order may be True)")
say("Capture: needs the sandbox buyer to approve at the link above (PayPal hosted login). Not performed here; no capture is claimed.")
say("")

# ---- step 5: receipts + verify ------------------------------------------------------------
say("## Step 5. Receipts verified offline")
(out / "public_key.txt").write_text(gate.receipts.pub + "\n")
(out / "export.json").write_text(json.dumps({"public_key": gate.receipts.pub, "kid": gate.receipts.kid, "receipts": gate.receipts.all()}, indent=1))
(out / "paypal_http_audit.json").write_text(json.dumps(paypal.audit, indent=1))
v = subprocess.run([sys.executable, str(ROOT / "verify.py"), str(out / "receipts.jsonl"), f"--pubkey={gate.receipts.pub}"], capture_output=True, text=True)
for line in v.stdout.splitlines():
    say("  " + line)
say(f"verify.py exit code: {v.returncode}")
say("")
say("## PayPal HTTP calls made (method path status debug_id), headers and bodies not recorded")
for x in paypal.audit:
    say(f"  {x['ts']} {x['method']} {x['host']}{x['path']} {x['status']} {x['paypal_debug_id']}")
(out / "transcript.md").write_text("\n".join(log) + "\n")
print("EVIDENCE_DIR", out)
