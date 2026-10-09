"""SQLite persistence for OAuth clients, pending logins, codes, tokens and rate-limit counters.

Secrets (authorization codes, access and refresh tokens) are stored only as
SHA-256 hashes, so a leaked database cannot be replayed. Every record expires,
expired rows are purged periodically, and per-kind row caps stop unauthenticated
endpoints (registration, /authorize) from filling the disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    kind TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL, expires_at REAL NOT NULL, grant_id TEXT,
    PRIMARY KEY (kind, key)
);
CREATE INDEX IF NOT EXISTS kv_grant ON kv(grant_id);
CREATE INDEX IF NOT EXISTS kv_expiry ON kv(expires_at);
"""

PURGE_INTERVAL = 300.0


class StoreFull(Exception):
    """Too many live records of one kind (likely abuse of an unauthenticated endpoint)."""


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class Store:
    def __init__(self, path: str = ":memory:", caps: dict[str, int] | None = None):
        self.memory = path == ":memory:"
        if not self.memory:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=10)
        if not self.memory:
            self._db.execute("PRAGMA journal_mode=WAL")
            os.chmod(path, 0o600)
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self._last_purge = time.time()
        self.caps = caps or {}

    def _maybe_purge(self) -> None:
        if time.time() - self._last_purge > PURGE_INTERVAL:
            self._last_purge = time.time()
            self._db.execute("DELETE FROM kv WHERE expires_at < ?", (time.time(),))

    def put(
        self, kind: str, key: str, data: dict[str, Any], ttl: float, grant_id: str | None = None, *, capped: bool = False
    ) -> None:
        with self._lock:
            self._maybe_purge()
            if capped and kind in self.caps:
                (live,) = self._db.execute(
                    "SELECT COUNT(*) FROM kv WHERE kind = ? AND expires_at >= ?", (kind, time.time())
                ).fetchone()
                if live >= self.caps[kind]:
                    raise StoreFull(kind)
            self._db.execute(
                "INSERT OR REPLACE INTO kv VALUES (?, ?, ?, ?, ?)",
                (kind, key, json.dumps(data), time.time() + ttl, grant_id),
            )

    def get(self, kind: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT data, expires_at FROM kv WHERE kind = ? AND key = ?", (kind, key)
            ).fetchone()
        if not row or row[1] < time.time():
            return None
        return json.loads(row[0])

    def pop(self, kind: str, key: str) -> dict[str, Any] | None:
        """Atomically fetch and delete (single-use records such as authorization codes)."""
        with self._lock:
            row = self._db.execute(
                "DELETE FROM kv WHERE kind = ? AND key = ? RETURNING data, expires_at", (kind, key)
            ).fetchone()
        if not row or row[1] < time.time():
            return None
        return json.loads(row[0])

    def extend(self, kind: str, key: str, ttl: float) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE kv SET expires_at = MAX(expires_at, ?) WHERE kind = ? AND key = ?", (time.time() + ttl, kind, key)
            )

    def delete(self, kind: str, key: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM kv WHERE kind = ? AND key = ?", (kind, key))

    def revoke_grant(self, grant_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM kv WHERE grant_id = ?", (grant_id,))

    def purge_expired(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM kv WHERE expires_at < ?", (time.time(),))

    def hit(self, bucket: str, limit: int, window: float) -> bool:
        """Sliding-window counter. Records a hit and returns True if `bucket` is under `limit`.

        Runs in one IMMEDIATE transaction, so it is atomic across processes sharing the file.
        """
        now = time.time()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                (count,) = self._db.execute(
                    # keys are "<bucket>|..."; '}' sorts right after '|', so this is an exact prefix range
                    "SELECT COUNT(*) FROM kv WHERE kind = 'rate' AND key >= ? AND key < ? AND expires_at >= ?",
                    (bucket + "|", bucket + "}", now),
                ).fetchone()
                allowed = count < limit
                if allowed:
                    self._db.execute(
                        "INSERT INTO kv VALUES ('rate', ?, '{}', ?, NULL)",
                        (f"{bucket}|{now:.6f}|{os.urandom(4).hex()}", now + window),
                    )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return allowed
