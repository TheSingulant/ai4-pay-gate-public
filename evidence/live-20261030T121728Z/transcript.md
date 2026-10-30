# AI4 Pay Gate LIVE fresh order 20261030T121728Z
Labels: PayPal = LIVE (sandbox, https://api-m.sandbox.paypal.com); model = LIVE (nvidia/Nemotron-3.5-Super-VL); approval click = operator-script; capture = NOT performed
experience_context: {"cancel_url": "http://127.0.0.1:8088/paypal/cancel", "return_url": "http://127.0.0.1:8088/paypal/return", "shipping_preference": "NO_SHIPPING", "user_action": "PAY_NOW"}
inference calls used: 1 (cap 2); served model: nvidia/Nemotron-3.5-Super-VL
proposed order: {"currency": "USD", "description": "AI4 Pay Gate demo order", "items": [{"name": "Dot grid notebook", "quantity": 2, "sku": "SKU-NOTEBOOK", "unit_amount": "12.00"}, {"name": "Gel pen 3 pack", "quantity": 1, "sku": "SKU-PEN-3", "unit_amount": "6.50"}], "payee": "sb-tvjtp53628657@business.example.com", "total": "30.50"}
2026-10-30T12:17:29Z approval: ok=True permit=pmt_hQ7IGYJtiBu4jOXtSSCmh7Yu order_hash=6c0580459a310e93b02e2b509c0b756eb7a315598586734ea7a5e791ec9b5346
2026-10-30T12:17:31Z pay: ok=True reason=gate_ok_order_created order=6YN25222V39712547 status=PAYER_ACTION_REQUIRED debug_id=f866888f247d0
buyer approval link (sandbox): https://www.sandbox.paypal.com/checkoutnow?token=6YN25222V39712547
Capture: NOT performed. Pending buyer approval, then scripts/capture_live.py on this folder.

## Buyer approval and capture (LIVE sandbox)

- The sandbox buyer approved on PayPal hosted checkout. PayPal redirected the browser to
  `http://127.0.0.1:8088/paypal/return?token=6YN25222V39712547&PayerID=<redacted>`: the return-URL flow worked
  end to end with a PayerID. The return page only reports approval and did not capture.
- 2026-10-30T12:24Z `scripts/capture_live.py`: OAuth 200, GET order 200 (APPROVED, 30.50 USD, payee
  sb-tvjtp53628657@business.example.com), gate read-back matched (`readback_match=true`), POST capture 201
- Capture `71V442751H327834G`, status COMPLETED, final capture, 30.50 USD. Receipt 3 appended (`reason=captured`).
- Read-only GET afterwards: order COMPLETED, custom_id equals the approved order hash. Payer details not stored.
