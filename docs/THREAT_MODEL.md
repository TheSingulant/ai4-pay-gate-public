# Threat model

## Assets
* The buyer's money: only the approved payee should receive only the approved amount for the approved items.
* The approval: a person's decision about one exact order.
* The evidence: receipts that show what was allowed and blocked.

## Trust boundaries
* **Agent (untrusted).** The model and the agent code that wraps it may be wrong, prompt-injected,
  or hostile. It can call `/api/agent/propose` and `/api/agent/pay`. It does not hold the approver
  token, PayPal credentials or the receipt signing key.
* **Human approver (trusted for intent).** Approves via the approval page with the approver token.
* **Gate (trusted).** Holds PayPal sandbox credentials and the receipt signing key.
* **PayPal sandbox (external).** Source of truth for the order and capture.
* **Verifier (independent).** Needs only the public key and the receipts.

## Threats and controls

| Threat | Control | Tested |
|---|---|---|
| Agent raises the amount or changes a price after approval | Hash of canonical order must equal approved hash; checked before any PayPal call | yes |
| Agent swaps payee | Payee allowlist, plus hash binding when swapping to another allowlisted payee | yes |
| Agent adds or removes items, changes quantity | Items are part of the canonical hash; total must equal the sum of items | yes |
| Agent retries or replays a permit | Permit is burned atomically on first use; concurrent attempts yield one winner | yes |
| Agent probes with a tampered order, then sends the honest one | Any failed attempt under a permit burns it | yes |
| Agent approves its own order | `/api/approve` requires the approver token, which the agent does not have | yes |
| Stale approval used much later | Permit expiry (default 600 s) | yes |
| Float rounding or ambiguous JSON | Amounts are 2-decimal strings, floats rejected, sorted-key canonical JSON | yes |
| Order changed on the PayPal side before capture | Gate reads the order back and compares payee, amount, currency and `custom_id` hash before capture | yes (stub) |
| Accidental use of live PayPal | Base URL pinned to `api-m.sandbox.paypal.com`; live hosts and look-alikes refused | yes |
| Credential leakage in logs or errors | Credentials only from env; reprs and error messages exclude them | yes |
| Receipt tampering, deletion or reordering | Ed25519 signatures, sequence numbers, hash chain | yes |
| Mislabelled evidence | Each receipt records `paypal.mode` and the agent label | yes |

## Out of scope / known limits
* Single process, single node. The permit store is local SQLite.
* The approver token is a shared secret, not per-person identity or WebAuthn.
* A compromised gate host can sign anything. Receipts prove what the gate decided, not that the gate is honest.
* The receipt signing key is a local file with mode 600, not an HSM.
* Truncation of the newest receipts is detectable only if the verifier knows the expected count or last hash.
* The approval page shows what the server returned; a compromised browser could mislead the approver.
* Buyer approval on PayPal happens on PayPal's site and is outside the gate.
