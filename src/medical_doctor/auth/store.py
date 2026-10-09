"""SQLite persistence for OAuth clients, pending logins, codes and tokens.

Secrets (authorization codes, access and refresh tokens) are stored only as
SHA-256 hashes, so a leaked database cannot be replayed.
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
CREATE TABLE IF NOT EXISTS clients (client_id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS kv (
    kind TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL, expires_at REAL NOT NULL, grant_id TEXT,
    PRIMARY KEY (kind, key)
);
CREATE INDEX IF NOT EXISTS kv_grant ON kv(grant_id);
"""


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class Store:
    def __init__(self, path: str = ":memory:"):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL") if path != ":memory:" else None
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        if path != ":memory:":
            os.chmod(path, 0o600)

    # clients
    def put_client(self, client_id: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO clients VALUES (?, ?, ?)", (client_id, json.dumps(data), time.time())
            )

    def get_client(self, client_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT data FROM clients WHERE client_id = ?", (client_id,)).fetchone()
        return json.loads(row[0]) if row else None

    # expiring records, keyed by kind + key (secrets are hashed by the caller via `digest`)
    def put(self, kind: str, key: str, data: dict[str, Any], ttl: float, grant_id: str | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO kv VALUES (?, ?, ?, ?, ?)",
                (kind, key, json.dumps(data), time.time() + ttl, grant_id),
            )

    def get(self, kind: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT data, expires_at FROM kv WHERE kind = ? AND key = ?", (kind, key)
            ).fetchone()
        if not row:
            return None
        if row[1] < time.time():
            self.delete(kind, key)
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

    def delete(self, kind: str, key: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM kv WHERE kind = ? AND key = ?", (kind, key))

    def revoke_grant(self, grant_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM kv WHERE grant_id = ?", (grant_id,))

    def purge_expired(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM kv WHERE expires_at < ?", (time.time(),))
