"""Postgres persistence (Supabase, Neon, RDS, ...) with the same interface as the SQLite Store.

Use it for serverless or multi-instance deployments (Vercel, Cloud Run, several replicas),
where a local SQLite file would be lost between invocations or differ per instance.

Supabase notes:
  * Use the pooled connection string (Supavisor, transaction mode, port 6543). Prepared
    statements are disabled, which transaction pooling requires.
  * Tables live in their own `medical_doctor` schema with row-level security enabled and no
    policies, so Supabase's Data API (anon/authenticated keys) can never read tokens. Only the
    server, connecting as the database owner, can.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from psycopg_pool import ConnectionPool

from .store import PURGE_INTERVAL, StoreFull

SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS medical_doctor;
CREATE TABLE IF NOT EXISTS medical_doctor.kv (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    data JSONB NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL,
    grant_id TEXT,
    PRIMARY KEY (kind, key)
);
CREATE INDEX IF NOT EXISTS kv_grant ON medical_doctor.kv (grant_id);
CREATE INDEX IF NOT EXISTS kv_expiry ON medical_doctor.kv (expires_at);
ALTER TABLE medical_doctor.kv ENABLE ROW LEVEL SECURITY;
"""

# libpq rejects query parameters it doesn't know; some providers add their own (Supabase's
# Vercel integration appends `supa=...`, Prisma-style URLs carry `pgbouncer=true`).
_LIBPQ_PARAMS = {
    "sslmode", "sslrootcert", "sslcert", "sslkey", "connect_timeout", "application_name",
    "options", "target_session_attrs", "keepalives", "keepalives_idle", "channel_binding",
}


def db_ca_file(value: str | None = None) -> str | None:
    """The database server's CA certificate as a file libpq can read. MEDICAL_DOCTOR_DB_CA_CERT holds
    either the PEM text (handy for Vercel env vars) or a path to it."""
    value = (value if value is not None else os.environ.get("MEDICAL_DOCTOR_DB_CA_CERT", "")).strip()
    if not value:
        return None
    if "-----BEGIN CERTIFICATE-----" not in value:
        return value  # a path
    path = Path(tempfile.gettempdir()) / f"md-db-ca-{hashlib.sha256(value.encode()).hexdigest()[:16]}.pem"
    if not path.exists():
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(value.replace("\\r", "").replace("\r", "").replace("\\n", "\n") + "\n")
        tmp.replace(path)
    return str(path)


def clean_dsn(url: str, ca_file: str | None = None) -> str:
    """libpq DSN with only parameters libpq knows. Remote hosts always use TLS: with the server's CA
    (MEDICAL_DOCTOR_DB_CA_CERT) the certificate and host name are verified (verify-full), which stops
    an attacker on the network path from impersonating the database; without it, encryption only."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k in _LIBPQ_PARAMS]
    mode = dict(query).get("sslmode")
    if parts.hostname not in ("localhost", "127.0.0.1"):
        if ca_file and mode != "disable":
            # Upgrades the integration's own sslmode=require too; only an explicit opt-out is kept.
            query = [(k, v) for k, v in query if k not in ("sslmode", "sslrootcert")]
            query += [("sslmode", "verify-full"), ("sslrootcert", ca_file)]
        elif not mode:
            query.append(("sslmode", "require"))
    if not any(k == "connect_timeout" for k, _ in query):
        query.append(("connect_timeout", "5"))  # fail fast instead of hanging on the OS TCP timeout
    return urlunsplit(parts._replace(query=urlencode(query)))


def _strip_nul(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {_strip_nul(k): _strip_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_nul(v) for v in value]
    return value


def _jsonb(data: dict[str, Any]) -> str:
    """JSON text that Postgres JSONB accepts: no NaN/Infinity, and no NUL characters, which
    unauthenticated inputs such as an OAuth client_name or state could otherwise inject."""
    return json.dumps(_strip_nul(data), allow_nan=False)


class PostgresStore:
    def __init__(self, url: str, caps: dict[str, int] | None = None, *, max_size: int = 5):
        self.pool = ConnectionPool(
            clean_dsn(url, db_ca_file()),
            min_size=1,
            max_size=max_size,
            kwargs={"autocommit": True, "prepare_threshold": None},  # transaction-pooler safe
            # Supavisor/Postgres drop idle connections and serverless instances get frozen: check a
            # connection before handing it out and recycle idle ones, so requests never get a dead one.
            check=ConnectionPool.check_connection,
            max_idle=60,
            timeout=10,
            open=True,
        )
        self.caps = caps or {}
        self._last_purge = 0.0
        # Many serverless instances may cold-start at once; serialize the idempotent DDL.
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('medical_doctor.schema'))")
            conn.execute(SCHEMA_SQL)

    def close(self) -> None:
        self.pool.close()

    def _maybe_purge(self, conn: Any) -> None:
        if time.time() - self._last_purge > PURGE_INTERVAL:
            self._last_purge = time.time()
            conn.execute("DELETE FROM medical_doctor.kv WHERE expires_at < %s", (time.time(),))

    def put(
        self, kind: str, key: str, data: dict[str, Any], ttl: float, grant_id: str | None = None, *, capped: bool = False
    ) -> None:
        with self.pool.connection() as conn:
            self._maybe_purge(conn)
            if capped and kind in self.caps:
                (live,) = conn.execute(
                    "SELECT COUNT(*) FROM medical_doctor.kv WHERE kind = %s AND expires_at >= %s", (kind, time.time())
                ).fetchone()
                if live >= self.caps[kind]:
                    raise StoreFull(kind)
            conn.execute(
                """INSERT INTO medical_doctor.kv (kind, key, data, expires_at, grant_id) VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (kind, key) DO UPDATE
                   SET data = EXCLUDED.data, expires_at = EXCLUDED.expires_at, grant_id = EXCLUDED.grant_id""",
                (kind, key, _jsonb(data), time.time() + ttl, grant_id),
            )

    def get(self, kind: str, key: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT data FROM medical_doctor.kv WHERE kind = %s AND key = %s AND expires_at >= %s",
                (kind, key, time.time()),
            ).fetchone()
        return row[0] if row else None

    def pop(self, kind: str, key: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "DELETE FROM medical_doctor.kv WHERE kind = %s AND key = %s RETURNING data, expires_at", (kind, key)
            ).fetchone()
        if not row or row[1] < time.time():
            return None
        return row[0]

    def extend(self, kind: str, key: str, ttl: float) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE medical_doctor.kv SET expires_at = GREATEST(expires_at, %s) WHERE kind = %s AND key = %s",
                (time.time() + ttl, kind, key),
            )

    def delete(self, kind: str, key: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("DELETE FROM medical_doctor.kv WHERE kind = %s AND key = %s", (kind, key))

    def revoke_grant(self, grant_id: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("DELETE FROM medical_doctor.kv WHERE grant_id = %s", (grant_id,))

    def purge_expired(self) -> None:
        with self.pool.connection() as conn:
            conn.execute("DELETE FROM medical_doctor.kv WHERE expires_at < %s", (time.time(),))

    def hit(self, bucket: str, limit: int, window: float) -> bool:
        """Sliding-window counter, atomic across all instances via a per-bucket advisory lock."""
        now = time.time()
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (bucket,))
            (count,) = conn.execute(
                # starts_with is an exact prefix match; a text range (key >= 'b|' AND key < 'b}') would
                # depend on the database collation (en_US.UTF-8 on Supabase sorts punctuation differently).
                "SELECT COUNT(*) FROM medical_doctor.kv WHERE kind = 'rate' AND starts_with(key, %s) AND expires_at >= %s",
                (bucket + "|", now),
            ).fetchone()
            if count >= limit:
                return False
            conn.execute(
                "INSERT INTO medical_doctor.kv (kind, key, data, expires_at) VALUES ('rate', %s, '{}', %s)",
                (f"{bucket}|{now:.6f}|{os.urandom(4).hex()}", now + window),
            )
        return True
