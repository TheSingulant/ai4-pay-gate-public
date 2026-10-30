# Evidence

## LIVE fresh order 20261030T121728Z (2026-10-30 12:17 UTC, captured 12:24 UTC)

Folder: [live-20261030T121728Z](live-20261030T121728Z/). Created with `scripts/fresh_order.py` after the
return-URL fix: `payment_source.paypal.experience_context` with return_url, cancel_url,
`user_action=PAY_NOW`, `shipping_preference=NO_SHIPPING`.

| Step | Result |
|---|---|
| Model builds cart | 1 inference call (cap 2), same 30.50 USD cart |
| Approve + pay | OAuth 200, create order 200, sandbox order `6YN25222V39712547`, status PAYER_ACTION_REQUIRED |
| Buyer approval | Sandbox buyer approved on PayPal hosted checkout; PayPal redirected to `http://127.0.0.1:8088/paypal/return?token=6YN25222V39712547&PayerID=<redacted>` (clean return-URL flow, PayerID present). The return page did not capture |
| Capture (2026-10-30 12:24 UTC) | `scripts/capture_live.py`: read-back APPROVED and matched, capture 201, capture `71V442751H327834G` COMPLETED, 30.50 USD, order COMPLETED |
| `verify.py` | 3 receipts (all allowed or approved), VALID |

## LIVE run 20261030T115405Z (2026-10-30 11:54 UTC)

Folder: [live-20261030T115405Z](live-20261030T115405Z/). PayPal SANDBOX (`api-m.sandbox.paypal.com`) and
Nebius Token Factory `nvidia/Nemotron-3.5-Super-VL` were both called for real. No PayPal Live host was used.

| Step | Result |
|---|---|
| Model builds cart | 1 inference call; cart 2 x SKU-NOTEBOOK + 1 x SKU-PEN-3, total 30.50 USD |
| Approve + pay (allow path) | OAuth 200, create order 201, sandbox order `4CY72468DE151393K`, status CREATED |
| Raised amount | blocked `order_hash_mismatch`, 0 PayPal HTTP calls |
| Swapped payee | blocked `payee_not_allowlisted`, 0 PayPal HTTP calls |
| Added item | blocked `order_hash_mismatch`, 0 PayPal HTTP calls |
| Replay of used permit | blocked `permit_already_used`, 0 PayPal HTTP calls |
| Read-back before capture | real order matched approved payee, amount, currency and hash; tampered variants did not; capture refused, status CREATED |
| Buyer approval | Sandbox buyer approved on PayPal's hosted checkout (by hand); order status APPROVED |
| Capture (2026-10-30 12:14 UTC) | `scripts/capture_live.py`: gate read-back matched, then capture 201, capture `3M2115364G941974K` COMPLETED, 30.50 USD, order COMPLETED |
| `verify.py` | 11 receipts (6 allowed or approved, 5 blocked), VALID |

Files: `transcript.md` (human readable), `receipts.jsonl` + `public_key.txt` + `export.json` (signed receipts),
`paypal_http_audit.json` (method, path, status, PayPal debug id only), `sandbox_order_readback.json`,
`model_calls.json` (model reply and token usage), `approved_order.json`,
`paypal_http_audit_capture.json` (capture run HTTP audit), `sandbox_order_after_capture.json` (read-only GET after capture, payer details omitted).

Redaction: no client id, client secret, API key, access token or Authorization header is recorded.
The approval click was performed by the evidence script on the operator's behalf.
Unit test and SCRIPTED demo output is not evidence of live behaviour and is not stored here.
