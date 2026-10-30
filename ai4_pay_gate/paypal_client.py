"""Minimal PayPal Orders API v2 client, hard-pinned to the PayPal SANDBOX.

Credentials come only from PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET in the environment.
The base URL is pinned to https://api-m.sandbox.paypal.com. Any other host, including the
live host api-m.paypal.com, is refused at construction time. Redirects are not followed.
Credentials and access tokens are never logged or returned.
"""
from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from .canonical import Order

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
LIVE_HOSTS = {"api-m.paypal.com", "api.paypal.com", "www.paypal.com", "paypal.com"}


class PayPalConfigError(RuntimeError):
    pass


class PayPalError(RuntimeError):
    def __init__(self, status: int, debug_id: str | None, name: str | None):
        super().__init__(f"PayPal API error status={status} name={name} debug_id={debug_id}")
        self.status, self.debug_id, self.name = status, debug_id, name


def check_base_url(url: str) -> str:
    u = urlsplit(url.strip())
    if u.hostname in LIVE_HOSTS:
        raise PayPalConfigError("refusing PayPal LIVE host; this build is sandbox only")
    if u.scheme != "https" or u.hostname != "api-m.sandbox.paypal.com" or u.port not in (None, 443) or u.path not in ("", "/") or u.query or u.username:
        raise PayPalConfigError("PayPal base URL must be exactly https://api-m.sandbox.paypal.com")
    return SANDBOX_BASE


DEFAULT_PUBLIC_BASE = "http://127.0.0.1:8088"


def experience_context(public_base: str | None = None) -> dict[str, str]:
    """Buyer return/cancel redirect for PayPal hosted checkout.

    The return page only reports approval; it never captures. Capture stays behind the gate.
    """
    base = (public_base or os.environ.get("AI4PG_PUBLIC_BASE_URL") or DEFAULT_PUBLIC_BASE).rstrip("/")
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.query or parts.fragment:
        raise PayPalConfigError("AI4PG_PUBLIC_BASE_URL must be a plain http(s) origin")
    return {
        "return_url": f"{base}/paypal/return",
        "cancel_url": f"{base}/paypal/cancel",
        "user_action": "PAY_NOW",
        "shipping_preference": "NO_SHIPPING",
    }


def order_to_paypal(order: Order, order_hash: str, public_base: str | None = None) -> dict[str, Any]:
    """Map a validated canonical order to an Orders v2 create body.

    custom_id carries the approved order hash so the binding is visible on the PayPal side.
    payment_source.paypal.experience_context sends the buyer back to /paypal/return or /paypal/cancel.
    """
    payee = {"email_address": order.payee} if "@" in order.payee else {"merchant_id": order.payee}
    return {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": "ai4pg",
                "custom_id": order_hash,
                "description": order.description or "AI4 Pay Gate order",
                "payee": payee,
                "amount": {
                    "currency_code": order.currency,
                    "value": order.total,
                    "breakdown": {"item_total": {"currency_code": order.currency, "value": order.total}},
                },
                "items": [
                    {
                        "name": i.name,
                        "sku": i.sku,
                        "quantity": str(i.quantity),
                        "unit_amount": {"currency_code": order.currency, "value": i.unit_amount},
                        "category": "DIGITAL_GOODS",
                    }
                    for i in order.items
                ],
            }
        ],
        "payment_source": {"paypal": {"experience_context": experience_context(public_base)}},
    }


class PayPalSandboxClient:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
    ):
        self.base = check_base_url(base_url or os.environ.get("PAYPAL_BASE_URL") or SANDBOX_BASE)
        self._cid = client_id if client_id is not None else os.environ.get("PAYPAL_CLIENT_ID", "")
        self._sec = client_secret if client_secret is not None else os.environ.get("PAYPAL_CLIENT_SECRET", "")
        # Redaction-safe audit trail: method, path, status, PayPal debug id only. No headers, no bodies.
        self.audit: list[dict[str, Any]] = []
        self._http = httpx.Client(
            base_url=self.base, transport=transport, timeout=timeout, follow_redirects=False,
            event_hooks={"response": [self._record]},
        )
        self._token: str | None = None
        self._token_exp = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._cid and self._sec)

    def __repr__(self) -> str:  # never include credentials
        return f"PayPalSandboxClient(base={self.base!r}, configured={self.configured})"

    def _record(self, r: httpx.Response) -> None:
        self.audit.append({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "method": r.request.method,
            "host": r.request.url.host,
            "path": r.request.url.path,
            "status": r.status_code,
            "paypal_debug_id": r.headers.get("paypal-debug-id"),
        })

    @property
    def last_debug_id(self) -> str | None:
        return self.audit[-1]["paypal_debug_id"] if self.audit else None

    def _raise(self, r: httpx.Response) -> None:
        name = None
        try:
            name = r.json().get("name")
        except Exception:
            pass
        raise PayPalError(r.status_code, r.headers.get("paypal-debug-id"), name)

    def _access_token(self) -> str:
        if not self.configured:
            raise PayPalConfigError("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET not set")
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        r = self._http.post(
            "/v1/oauth2/token",
            auth=(self._cid, self._sec),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
        )
        if r.status_code != 200:
            self._raise(r)
        j = r.json()
        self._token = j["access_token"]
        self._token_exp = time.time() + int(j.get("expires_in", 300))
        return self._token

    def _call(self, method: str, path: str, json_body: Any = None, request_id: str | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._access_token()}", "Content-Type": "application/json", "Prefer": "return=representation"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        r = self._http.request(method, path, json=json_body, headers=headers)
        if r.status_code not in (200, 201):
            self._raise(r)
        return r.json()

    def create_order(self, order: Order, order_hash: str, request_id: str) -> dict[str, Any]:
        return self._call("POST", "/v2/checkout/orders", order_to_paypal(order, order_hash), request_id)

    def get_order(self, paypal_order_id: str) -> dict[str, Any]:
        _check_id(paypal_order_id)
        return self._call("GET", f"/v2/checkout/orders/{paypal_order_id}")

    def capture_order(self, paypal_order_id: str, request_id: str) -> dict[str, Any]:
        _check_id(paypal_order_id)
        return self._call("POST", f"/v2/checkout/orders/{paypal_order_id}/capture", {}, request_id)


def _check_id(oid: str) -> None:
    if not (isinstance(oid, str) and 5 <= len(oid) <= 36 and oid.isalnum()):
        raise ValueError("invalid PayPal order id")


def approve_link(paypal_order: dict[str, Any]) -> str | None:
    for link in paypal_order.get("links", []):
        if link.get("rel") in ("approve", "payer-action"):
            href = link.get("href", "")
            host = urlsplit(href).hostname or ""
            if host == "www.sandbox.paypal.com":
                return href
    return None


def paypal_order_matches(paypal_order: dict[str, Any], order: Order, order_hash: str) -> bool:
    """Re-check a PayPal-side order against the approved canonical order before capture."""
    try:
        pu = paypal_order["purchase_units"][0]
        amt = pu["amount"]
        payee = pu.get("payee", {})
        payee_ok = payee.get("email_address", "").lower() == order.payee if "@" in order.payee else payee.get("merchant_id") == order.payee
        return (
            len(paypal_order["purchase_units"]) == 1
            and pu.get("custom_id") == order_hash
            and amt["currency_code"] == order.currency
            and amt["value"] == order.total
            and payee_ok
        )
    except (KeyError, IndexError, TypeError, AttributeError):
        return False
