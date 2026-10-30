#!/usr/bin/env python3
"""Offline receipt verifier for AI4 Pay Gate. Needs only the public key and the receipts.

Usage:
  python verify.py receipts.jsonl --pubkey=<base64url Ed25519 public key>
  (use the = form: a base64url key can start with "-")
  python verify.py export.json           (an /api/receipts export that embeds public_key)

Checks every Ed25519 signature, the key id, sequence numbers, the hash chain, that each
allowed payment had order_hash == approved_hash, and that no permit authorized two payments.
Note: trust the public key only if you obtained it out of band, not from the same file.
Exit code 0 = all valid, 1 = invalid, 2 = usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ai4_pay_gate.receipts import verify_receipts  # noqa: E402


def load(path: Path) -> tuple[list[dict], str | None]:
    text = path.read_text()
    if path.suffix == ".json":
        doc = json.loads(text)
        return doc["receipts"], doc.get("public_key")
    return [json.loads(l) for l in text.splitlines() if l.strip()], None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", type=Path)
    ap.add_argument("--pubkey", help="base64url raw Ed25519 public key")
    a = ap.parse_args(argv)
    try:
        receipts, embedded = load(a.file)
    except (OSError, ValueError, KeyError) as e:
        print(f"error: cannot read receipts: {e}", file=sys.stderr)
        return 2
    pub = a.pubkey or embedded
    if not pub:
        print("error: no public key (pass --pubkey)", file=sys.stderr)
        return 2
    ok, errors = verify_receipts(receipts, pub)
    allowed = sum(1 for r in receipts if r.get("body", {}).get("decision") in ("allowed", "approved"))
    blocked = sum(1 for r in receipts if r.get("body", {}).get("decision") == "blocked")
    print(f"receipts={len(receipts)} allowed_or_approved={allowed} blocked={blocked} key_source={'arg' if a.pubkey else 'embedded'}")
    for e in errors:
        print("FAIL", e)
    print("VALID" if ok else "INVALID")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
