# AI4 Pay Gate

An AI shopping agent can pay through PayPal only for the exact order a person approved,
and every allowed or blocked attempt leaves an Ed25519 signed receipt that anyone can verify offline.

Built by The Singulant for the PayPal AI Hackathon. Apache-2.0. This is a small, separately
written reference gate. It is not the AI4 production core.

Demo video (2:42): https://youtu.be/dhMbj3J-bvw

## Status: what is LIVE and what is SCRIPTED

Be precise about this when reading any output from this repo.

| Part | Status |
|---|---|
| Canonical order hashing, policy, single-use permits, receipts, `verify.py` | Implemented, unit tested, and exercised in the LIVE run below (11 receipts VALID) |
| PayPal sandbox: OAuth client credentials + create order | **LIVE** (sandbox), run `evidence/live-20261030T115405Z`, sandbox order `4CY72468DE151393K` |
| Blocked attempts (raised amount, swapped payee, added item, replay) with zero PayPal calls | **LIVE** run, same evidence folder |
| Read-back of the real sandbox order before capture | **LIVE**: while `CREATED`, read-back matched and capture was refused; after buyer approval, read-back matched again before capture |
| Buyer approval | **LIVE**: sandbox buyer approved on PayPal's hosted checkout page (by hand, in a browser) |
| Fresh order with return/cancel URLs (`experience_context`, `PAY_NOW`, `NO_SHIPPING`) | **LIVE** (sandbox): `scripts/fresh_order.py`, order `6YN25222V39712547`, buyer returned to `/paypal/return` with PayerID, then gated capture `71V442751H327834G` COMPLETED (30.50 USD), run `evidence/live-20261030T121728Z` |
| Capture | **LIVE** (sandbox): `scripts/capture_live.py`, capture `3M2115364G941974K` COMPLETED, 30.50 USD, 2026-10-30 12:14 UTC |
| AI agent building the cart | **LIVE**: `nvidia/Nemotron-3.5-Super-VL` via Nebius Token Factory, 1 inference call |
| Human approval click in the LIVE run | Performed by the evidence script on the operator's behalf (`approver="operator-script"`) |
| Unit tests | STUB transports for PayPal and the model. Never used as live evidence |
| `scripts/demo_offline.py` | SCRIPTED. Fixed agent reply, no PayPal. Not live evidence |

Every receipt records `paypal.mode` (`live_sandbox`, `stub`, or `not_configured`) and the
agent label (`LIVE:<model>` or `SCRIPTED`), so a receipt cannot be mistaken for a live run.
See [evidence/README.md](evidence/README.md).

## How it works

1. The agent proposes an order: payee, currency, items, total. Prices come from the merchant
   catalog, not from the model.
2. A person reviews it in the approval page and approves with the approver token. The agent
   never holds that token.
3. The gate checks policy (payee allowlist, max amount, currency), computes
   `SHA-256(canonical order JSON)` and issues a single-use permit bound to that hash
   (default lifetime 10 minutes).
4. The agent submits `permit_id + order` to pay. The gate re-parses, re-checks policy,
   re-hashes, and atomically burns the permit. Only if the hash equals the approved hash is
   the PayPal Orders v2 API called. A changed payee, amount, item list or a retry is blocked
   before any PayPal request, including the OAuth token request.
5. The PayPal order carries the approved hash in `custom_id`. Before capture, the gate reads
   the order back from PayPal and refuses to capture if payee, amount, currency or hash differ.
6. The order sends the buyer back to `/paypal/return` (or `/paypal/cancel`). Those pages only
   show what happened. They never capture; capture still needs the approver token and the read-back.
   Set `AI4PG_PUBLIC_BASE_URL` to the gate's public origin (default `http://127.0.0.1:8088`).
7. Every step writes a signed, hash-chained receipt. `verify.py` checks signatures, chain,
   sequence and that no permit paid twice, using only the public key.

## Prerequisites

- Python 3.11 or 3.12 and git. No credentials are needed for the tests, the offline demo or `verify.py`.
- For a live sandbox run only: a free PayPal Developer account (https://developer.paypal.com) and an OpenAI-compatible model API key.

## Quick start (local)

```bash
git clone https://github.com/TheSingulant/ai4-pay-gate-public && cd ai4-pay-gate-public
```


```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
python -m pytest -q                 # unit tests, no network
python scripts/demo_offline.py      # SCRIPTED walkthrough, then run the printed verify command
python -m ai4_pay_gate              # http://127.0.0.1:8088 , approver token in ./data/approver_token
```

LIVE evidence run (needs sandbox and model credentials in env, never printed):

```bash
export PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... NEBIUS_API_KEY=...
AI4PG_LLM_MAX_CALLS=3 python scripts/live_phase2.py      # writes evidence/live-<UTC>/
python verify.py evidence/live-<UTC>/receipts.jsonl --pubkey="$(cat evidence/live-<UTC>/public_key.txt)"
# after the sandbox buyer approves at the printed link:
python scripts/capture_live.py evidence/live-<UTC>
```

### PayPal sandbox setup

1. Sign in at https://developer.paypal.com, open Apps & Credentials, keep the toggle on **Sandbox**, and create an app (type Merchant).
2. Copy the sandbox Client ID and Secret into `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`.
3. Under Testing Tools > Sandbox Accounts, note your sandbox Business account email (payee) and use the sandbox Personal account to approve the order at the printed checkout link.
4. The app does not read `.env` by itself. Load it with `set -a; . ./.env; set +a`.

For live sandbox use, copy `.env.example` to `.env`, fill PayPal SANDBOX app credentials and an
OpenAI-compatible model provider, and export them. Edit `policy/demo-policy.json` so the payee
is your sandbox business account email. The client refuses any host other than
`https://api-m.sandbox.paypal.com`.

## Layout

```
ai4_pay_gate/canonical.py      strict order parsing, canonical JSON, SHA-256 order hash
ai4_pay_gate/policy.py         payee allowlist, max amount, currencies
ai4_pay_gate/permits.py        single-use, expiring permits (SQLite, atomic burn)
ai4_pay_gate/paypal_client.py  sandbox-pinned Orders v2 client
ai4_pay_gate/gate.py           approve, pay, capture; checks before PayPal; receipts
ai4_pay_gate/receipts.py       Ed25519 signed hash-chained receipts and verifier core
ai4_pay_gate/agent.py          shopping agent, OpenAI-compatible or scripted provider
ai4_pay_gate/app.py            FastAPI service and approval page
verify.py                      offline receipt verifier CLI
docs/THREAT_MODEL.md           threat model
```

See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
