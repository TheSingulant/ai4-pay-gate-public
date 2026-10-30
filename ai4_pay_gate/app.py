"""FastAPI service: agent endpoints, human approval endpoint, receipts.

Trust split:
  * /api/agent/*  is what the AI agent may call. It can propose and attempt payment only.
  * /api/approve and /api/capture require the human approver secret (X-Approver-Token).
    The agent process is never given that secret.
"""
from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .agent import Provider, ProviderError, ShoppingAgent, provider_from_env
from .gate import Gate
from .paypal_client import PayPalSandboxClient
from .permits import PermitStore
from .policy import Policy
from .receipts import ReceiptLog, load_or_create_key

STATIC = Path(__file__).parent / "static"
DEFAULT_POLICY = Path(__file__).resolve().parent.parent / "policy" / "demo-policy.json"


class ProposeIn(BaseModel):
    request: str


class ApproveIn(BaseModel):
    order: dict[str, Any]


class PayIn(BaseModel):
    permit_id: str
    order: dict[str, Any]


class CaptureIn(BaseModel):
    paypal_order_id: str


def _approver_token(data_dir: Path) -> str:
    env = os.environ.get("AI4PG_APPROVER_TOKEN", "")
    if env:
        return env
    p = data_dir / "approver_token"
    if p.exists():
        return p.read_text().strip()
    data_dir.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(24)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(tok)
    return tok


def create_app(gate: Gate | None = None, provider: Provider | None = None, approver_token: str | None = None) -> FastAPI:
    data_dir = Path(os.environ.get("AI4PG_DATA_DIR", "./data"))
    if gate is None:
        policy = Policy.load(os.environ.get("AI4PG_POLICY_PATH", str(DEFAULT_POLICY)))
        ttl = int(os.environ.get("AI4PG_PERMIT_TTL_SECONDS", "600"))
        data_dir.mkdir(parents=True, exist_ok=True)
        key = load_or_create_key(data_dir / "receipt_signing.key")
        gate = Gate(policy, PermitStore(data_dir / "permits.sqlite", ttl), ReceiptLog(key, data_dir / "receipts.jsonl"), PayPalSandboxClient())
    provider = provider or provider_from_env()
    token = approver_token or _approver_token(data_dir)
    payee = sorted(gate.policy.payee_allowlist)[0] if gate.policy.payee_allowlist else "unset@example.com"
    agent = ShoppingAgent(provider, payee=payee)

    app = FastAPI(title="AI4 Pay Gate", version="0.1.0", docs_url=None, redoc_url=None)

    def require_approver(x_approver_token: str | None) -> None:
        if not x_approver_token or not hmac.compare_digest(x_approver_token.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="approver token required")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC / "approve.html").read_text()

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return {
            "paypal": {"base": gate.paypal.base if gate.paypal else None, "configured": bool(gate.paypal and gate.paypal.configured), "mode": gate.paypal_mode if (gate.paypal and gate.paypal.configured) else "not_configured"},
            "model": {"label": provider.label},
            "policy": {"payees": sorted(gate.policy.payee_allowlist), "max_amount": str(gate.policy.max_amount), "currencies": sorted(gate.policy.currencies)},
            "receipt_key_id": gate.receipts.kid,
        }

    @app.post("/api/agent/propose")
    def propose(body: ProposeIn) -> dict[str, Any]:
        try:
            p = agent.propose(body.request)
        except ProviderError as e:
            raise HTTPException(status_code=502, detail=str(e))
        return {"order": p.order, "provider": p.provider_label, "note": p.note}

    @app.post("/api/approve")
    def approve(body: ApproveIn, x_approver_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_approver(x_approver_token)
        return gate.approve(body.order, approver="human-ui")

    @app.post("/api/agent/pay")
    def pay(body: PayIn) -> dict[str, Any]:
        return gate.pay(body.permit_id, body.order, actor=f"agent:{provider.label}")

    @app.post("/api/capture")
    def capture(body: CaptureIn, x_approver_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_approver(x_approver_token)
        try:
            return gate.capture(body.paypal_order_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    def _safe_id(v: str | None) -> str:
        return v if (v and 5 <= len(v) <= 36 and v.isalnum()) else ""

    def _page(title: str, msg: str, oid: str) -> str:
        ref = f"<p>PayPal order: <code>{oid}</code></p>" if oid else ""
        return (
            "<!doctype html><meta charset=utf-8><meta name=referrer content=no-referrer>"
            f"<title>{title}</title><h1>{title}</h1><p>{msg}</p>{ref}"
            "<p>This page never captures payment. Capture requires the human approver and a gate read-back.</p>"
        )

    @app.get("/paypal/return", response_class=HTMLResponse)
    def paypal_return(token: str | None = None, PayerID: str | None = None) -> str:  # noqa: N803
        oid = _safe_id(token)
        payer = "yes" if _safe_id(PayerID) else "no"
        return _page("Buyer approval received", f"PayPal returned the buyer here (payer id present: {payer}). No money has moved yet.", oid)

    @app.get("/paypal/cancel", response_class=HTMLResponse)
    def paypal_cancel(token: str | None = None) -> str:
        return _page("Payment cancelled", "The buyer cancelled on PayPal. Nothing will be captured.", _safe_id(token))

    @app.get("/api/receipts")
    def receipts() -> dict[str, Any]:
        return {"public_key": gate.receipts.pub, "kid": gate.receipts.kid, "receipts": gate.receipts.all()}

    @app.get("/api/pubkey")
    def pubkey() -> dict[str, str]:
        return {"public_key": gate.receipts.pub, "kid": gate.receipts.kid}

    return app


def main() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("PORT", "8088")))


if __name__ == "__main__":  # pragma: no cover
    main()
