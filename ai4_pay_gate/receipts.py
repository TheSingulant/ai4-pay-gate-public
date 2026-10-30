"""Ed25519 signed, hash-chained receipts for every allowed or blocked payment attempt."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .canonical import canonical_bytes

RECEIPT_VERSION = "ai4pg-receipt-v1"
GENESIS = "0" * 64


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def load_or_create_key(path: str | Path) -> Ed25519PrivateKey:
    """Load a raw 32 byte Ed25519 seed, or create one with mode 600. Never logged."""
    p = Path(path)
    if p.exists():
        return Ed25519PrivateKey.from_private_bytes(p.read_bytes())
    p.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
    return key


def public_key_b64(key: Ed25519PrivateKey) -> str:
    return b64u(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def key_id(pub_b64: str) -> str:
    return "ed25519:" + hashlib.sha256(b64u_dec(pub_b64)).hexdigest()[:16]


def body_hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


class ReceiptLog:
    """Append-only JSONL log. Each receipt body includes the previous receipt's body hash."""

    def __init__(self, key: Ed25519PrivateKey, path: str | Path | None = None):
        self._key = key
        self.pub = public_key_b64(key)
        self.kid = key_id(self.pub)
        self._path = Path(path) if path else None
        self._lock = threading.Lock()
        self._mem: list[dict[str, Any]] = []
        self._prev = GENESIS
        if self._path and self._path.exists():
            for line in self._path.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    self._mem.append(r)
                    self._prev = body_hash(r["body"])

    def append(self, fields: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            body = {
                "v": RECEIPT_VERSION,
                "receipt_id": "rcpt_" + secrets.token_hex(8),
                "seq": len(self._mem),
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "kid": self.kid,
                "prev": self._prev,
                **fields,
            }
            sig = self._key.sign(canonical_bytes(body))
            rec = {"body": body, "sig": b64u(sig)}
            self._mem.append(rec)
            self._prev = body_hash(body)
            if self._path:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._path, "a") as fh:
                    fh.write(json.dumps(rec, sort_keys=True) + "\n")
            return rec

    def all(self) -> list[dict[str, Any]]:
        return list(self._mem)


def verify_receipts(receipts: list[dict[str, Any]], pub_b64: str) -> tuple[bool, list[str]]:
    """Offline verification: signature, key id, sequence, hash chain, permit reuse."""
    errors: list[str] = []
    pub = Ed25519PublicKey.from_public_bytes(b64u_dec(pub_b64))
    kid = key_id(pub_b64)
    prev = GENESIS
    allowed_permits: set[str] = set()
    for i, rec in enumerate(receipts):
        body = rec.get("body")
        if not isinstance(body, dict):
            errors.append(f"#{i}: missing body")
            continue
        try:
            pub.verify(b64u_dec(rec.get("sig", "")), canonical_bytes(body))
        except Exception:
            errors.append(f"#{i}: bad signature")
        if body.get("kid") != kid:
            errors.append(f"#{i}: key id mismatch")
        if body.get("seq") != i:
            errors.append(f"#{i}: sequence gap or reorder")
        if body.get("prev") != prev:
            errors.append(f"#{i}: hash chain broken")
        if body.get("event") == "payment_attempt" and body.get("decision") == "allowed":
            pid = body.get("permit_id")
            if pid in allowed_permits:
                errors.append(f"#{i}: permit used for more than one allowed payment")
            allowed_permits.add(pid)
            if body.get("order_hash") != body.get("approved_hash"):
                errors.append(f"#{i}: allowed receipt with order_hash != approved_hash")
        prev = body_hash(body)
    return (not errors, errors)
