"""Shopping agent with a pluggable OpenAI-compatible model provider.

The agent is UNTRUSTED from the gate's point of view. It only proposes orders. It cannot
approve them and it holds no PayPal credentials. Whatever it later submits for payment is
re-checked by the gate against the exact order a human approved.

Provider config (env only, nothing committed):
  AI4PG_LLM_BASE_URL  e.g. https://<provider>/v1   (OpenAI-compatible /chat/completions)
  AI4PG_LLM_API_KEY
  AI4PG_LLM_MODEL
"""
from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

DEMO_PAYEE = "sb-tvjtp53628657@business.example.com"

# Demo catalog. Prices are the merchant's, not the model's: the agent picks SKUs and
# quantities, and prices are filled from here.
CATALOG: dict[str, dict[str, str]] = {
    "SKU-NOTEBOOK": {"name": "Dot grid notebook", "unit_amount": "12.00"},
    "SKU-PEN-3": {"name": "Gel pen 3 pack", "unit_amount": "6.50"},
    "SKU-MUG": {"name": "Ceramic mug", "unit_amount": "18.00"},
    "SKU-STICKERS": {"name": "Sticker sheet", "unit_amount": "4.25"},
    "SKU-BACKPACK": {"name": "Laptop backpack", "unit_amount": "89.00"},
}

SYSTEM_PROMPT = (
    "You are a shopping assistant. Choose products for the user's request from this catalog only. "
    "Reply with ONLY a JSON object of the form {\"items\": [{\"sku\": \"...\", \"quantity\": 1}], \"note\": \"...\"}. "
    "Catalog: " + json.dumps({k: v for k, v in CATALOG.items()}, sort_keys=True)
)


class Provider(Protocol):
    label: str  # "LIVE:<model>" or "SCRIPTED"

    def complete(self, system: str, user: str) -> str: ...


class ProviderError(RuntimeError):
    pass


class OpenAICompatibleProvider:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None, transport: httpx.BaseTransport | None = None, timeout: float = 90.0, max_calls: int | None = None, max_tokens: int | None = None):
        self.base = (base_url or os.environ.get("AI4PG_LLM_BASE_URL", "")).rstrip("/")
        self._key = api_key if api_key is not None else os.environ.get("AI4PG_LLM_API_KEY", "")
        self.model = model or os.environ.get("AI4PG_LLM_MODEL", "")
        if not (self.base and self._key and self.model):
            raise ProviderError("AI4PG_LLM_BASE_URL, AI4PG_LLM_API_KEY and AI4PG_LLM_MODEL must be set")
        if urlsplit(self.base).scheme != "https":
            raise ProviderError("model base URL must be https")
        self.label = f"LIVE:{self.model}"
        # Hard cap on inference calls made by this provider instance (spend control).
        self.max_calls = max_calls if max_calls is not None else int(os.environ.get("AI4PG_LLM_MAX_CALLS", "25"))
        self.max_tokens = max_tokens or int(os.environ.get("AI4PG_LLM_MAX_TOKENS", "1500"))
        self.calls = 0
        self.last_usage: dict[str, Any] | None = None
        self.last_raw: str | None = None
        self.served_model: str | None = None
        self._http = httpx.Client(transport=transport, timeout=timeout, follow_redirects=False)

    def __repr__(self) -> str:
        return f"OpenAICompatibleProvider(base={self.base!r}, model={self.model!r})"

    def complete(self, system: str, user: str) -> str:
        if self.calls >= self.max_calls:
            raise ProviderError(f"inference call cap reached ({self.max_calls})")
        self.calls += 1
        r = self._http.post(
            f"{self.base}/chat/completions",
            headers={"Authorization": f"Bearer {self._key}"},
            json={"model": self.model, "temperature": 0, "max_tokens": self.max_tokens, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
        )
        if r.status_code != 200:
            raise ProviderError(f"model provider returned HTTP {r.status_code}")
        j = r.json()
        self.last_usage = j.get("usage")
        self.served_model = j.get("model")
        self.last_raw = j["choices"][0]["message"].get("content") or ""
        return self.last_raw


class ScriptedProvider:
    """Deterministic stand-in used for tests and the offline demo. Always labelled SCRIPTED."""

    label = "SCRIPTED"

    def __init__(self, reply: str | None = None):
        self.reply = reply or json.dumps({"items": [{"sku": "SKU-NOTEBOOK", "quantity": 2}, {"sku": "SKU-PEN-3", "quantity": 1}], "note": "scripted reply"})

    def complete(self, system: str, user: str) -> str:
        return self.reply


def provider_from_env() -> Provider:
    try:
        return OpenAICompatibleProvider()
    except ProviderError:
        return ScriptedProvider()


def _extract_json(text: str) -> dict[str, Any]:
    """Return the last JSON object in the reply that has an "items" list.

    Tolerates reasoning text, code fences or <think> blocks around the answer.
    """
    dec = json.JSONDecoder()
    found = None
    for m in re.finditer(r"\{", text or ""):
        try:
            obj, _ = dec.raw_decode(text, m.start())
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("items"), list):
            found = obj
    if found is None:
        raise ProviderError("model reply contained no JSON object with items")
    return found


@dataclass
class Proposal:
    order: dict[str, Any]
    provider_label: str
    note: str


class ShoppingAgent:
    def __init__(self, provider: Provider, payee: str = DEMO_PAYEE, currency: str = "USD"):
        self.provider = provider
        self.payee = payee
        self.currency = currency

    def propose(self, request: str) -> Proposal:
        reply = _extract_json(self.provider.complete(SYSTEM_PROMPT, request[:1000]))
        items = []
        total = Decimal("0")
        for it in reply.get("items", [])[:20]:
            sku = it.get("sku")
            qty = it.get("quantity", 1)
            if sku not in CATALOG or isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= 10:
                continue  # ignore hallucinated SKUs or silly quantities
            c = CATALOG[sku]
            items.append({"sku": sku, "name": c["name"], "quantity": qty, "unit_amount": c["unit_amount"]})
            total += Decimal(c["unit_amount"]) * qty
        if not items:
            raise ProviderError("model proposed no valid catalog items")
        order = {"payee": self.payee, "currency": self.currency, "items": items, "total": f"{total:.2f}", "description": "AI4 Pay Gate demo order"}
        return Proposal(order=order, provider_label=self.provider.label, note=str(reply.get("note", ""))[:200])


# ---- adversarial behaviours used by the demo and tests (SCRIPTED, not model output) ----
def tamper(order: dict[str, Any], mode: str) -> dict[str, Any]:
    o = copy.deepcopy(order)
    if mode == "raise_amount":
        o["items"][0]["unit_amount"] = f"{Decimal(o['items'][0]['unit_amount']) + Decimal('20.00'):.2f}"
        o["total"] = f"{sum(Decimal(i['unit_amount']) * i['quantity'] for i in o['items']):.2f}"
    elif mode == "swap_payee":
        o["payee"] = "attacker@personal.example.com"
    elif mode == "add_item":
        c = CATALOG["SKU-STICKERS"]
        o["items"].append({"sku": "SKU-STICKERS", "name": c["name"], "quantity": 1, "unit_amount": c["unit_amount"]})
        o["total"] = f"{sum(Decimal(i['unit_amount']) * i['quantity'] for i in o['items']):.2f}"
    elif mode == "none":
        pass
    else:
        raise ValueError(mode)
    return o
