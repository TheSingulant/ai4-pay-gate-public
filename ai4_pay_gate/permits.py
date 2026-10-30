"""Single-use payment permissions bound to an order hash.

A permit is created only by a human approval of one exact order. Consuming a permit is an
atomic SQLite transaction, so a permit can authorize at most one payment attempt that
reaches PayPal, even under concurrent retries.
"""
from __future__ import annotations

import hmac
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ConsumeResult:
    ok: bool
    reason: str
    approved_hash: str | None = None


class PermitStore:
    def __init__(self, path: str | Path = ":memory:", ttl_seconds: int = 600, clock=time.time):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL") if str(path) != ":memory:" else None
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS permits (id TEXT PRIMARY KEY, order_hash TEXT NOT NULL,"
            " created REAL NOT NULL, expires REAL NOT NULL, used REAL)"
        )
        self.ttl = ttl_seconds
        self._clock = clock

    def issue(self, order_hash: str) -> tuple[str, float]:
        pid = "pmt_" + secrets.token_urlsafe(18)
        now = self._clock()
        exp = now + self.ttl
        with self._lock:
            self._db.execute("INSERT INTO permits VALUES (?,?,?,?,NULL)", (pid, order_hash, now, exp))
        return pid, exp

    def consume(self, permit_id: str, presented_hash: str) -> ConsumeResult:
        """Check and burn a permit. A mismatched hash also burns it: a tampered attempt
        invalidates the approval so the agent cannot probe and then retry."""
        now = self._clock()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT order_hash, expires, used FROM permits WHERE id=?", (permit_id,)
                ).fetchone()
                if row is None:
                    self._db.execute("ROLLBACK")
                    return ConsumeResult(False, "unknown_permit")
                approved, expires, used = row
                if used is not None:
                    self._db.execute("ROLLBACK")
                    return ConsumeResult(False, "permit_already_used", approved)
                self._db.execute("UPDATE permits SET used=? WHERE id=? AND used IS NULL", (now, permit_id))
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        if now > expires:
            return ConsumeResult(False, "permit_expired", approved)
        if not hmac.compare_digest(approved, presented_hash):
            return ConsumeResult(False, "order_hash_mismatch", approved)
        return ConsumeResult(True, "permit_ok", approved)
