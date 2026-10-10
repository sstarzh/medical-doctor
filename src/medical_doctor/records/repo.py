"""Encrypted record repository (SQLite or Postgres) with per-patient data keys and a hash-chained audit log.

Envelope encryption:
  * Each patient gets a random 256-bit data key (DEK). Records are AES-256-GCM encrypted with
    it, bound to (owner, patient, section, item) as associated data.
  * The DEK is stored only wrapped by the key-encryption key (KEK) from MEDICAL_DOCTOR_DATA_KEYS.
  * Deleting a patient destroys its DEK first (crypto-shredding), then the rows.

Only opaque IDs, section names and timestamps are stored in clear. Owner IDs are already
pseudonymous hashes of (issuer, subject), see identity.owner_id().
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import anyio
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .crypto import Cipher

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS md_patients (
    owner TEXT NOT NULL, patient_id TEXT NOT NULL, wrapped_dek TEXT NOT NULL, created_at REAL NOT NULL,
    PRIMARY KEY (owner, patient_id));
CREATE TABLE IF NOT EXISTS md_records (
    owner TEXT NOT NULL, patient_id TEXT NOT NULL, section TEXT NOT NULL, item_id TEXT NOT NULL,
    ciphertext TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
    PRIMARY KEY (owner, patient_id, section, item_id));
CREATE TABLE IF NOT EXISTS md_audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, owner TEXT NOT NULL, client_id TEXT,
    action TEXT NOT NULL, patient_id TEXT, section TEXT, item_id TEXT, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS md_audit_owner ON md_audit (owner, seq);
"""

PG_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS medical_doctor;
CREATE TABLE IF NOT EXISTS medical_doctor.patients (
    owner TEXT NOT NULL, patient_id TEXT NOT NULL, wrapped_dek TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (owner, patient_id));
CREATE TABLE IF NOT EXISTS medical_doctor.records (
    owner TEXT NOT NULL, patient_id TEXT NOT NULL, section TEXT NOT NULL, item_id TEXT NOT NULL,
    ciphertext TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (owner, patient_id, section, item_id));
CREATE TABLE IF NOT EXISTS medical_doctor.audit (
    seq BIGSERIAL PRIMARY KEY, ts DOUBLE PRECISION NOT NULL, owner TEXT NOT NULL, client_id TEXT,
    action TEXT NOT NULL, patient_id TEXT, section TEXT, item_id TEXT, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS audit_owner ON medical_doctor.audit (owner, seq);
ALTER TABLE medical_doctor.patients ENABLE ROW LEVEL SECURITY;
ALTER TABLE medical_doctor.records ENABLE ROW LEVEL SECURITY;
ALTER TABLE medical_doctor.audit ENABLE ROW LEVEL SECURITY;
"""


class _Sql:
    """Tiny dialect shim: SQL is written with `?` placeholders and unqualified table names."""

    def __init__(self, postgres: bool):
        self.postgres = postgres

    def q(self, sql: str) -> str:
        if not self.postgres:
            return sql
        for t in ("md_patients", "md_records", "md_audit"):
            sql = sql.replace(t, "medical_doctor." + t[3:])
        return sql.replace("?", "%s")


class RecordRepo:
    """Synchronous core; use `AsyncRecordRepo` from async code."""

    def __init__(self, kek: Cipher, *, sqlite_path: str | None = None, pg_pool: Any = None):
        self.kek = kek
        self._lock = threading.Lock()
        self._pool = pg_pool
        if pg_pool is not None:
            with pg_pool.connection() as conn, conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(hashtext('medical_doctor.records_schema'))")
                conn.execute(PG_SCHEMA)
            self.sql = _Sql(postgres=True)
        else:
            path = sqlite_path or ":memory:"
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=10)
            if path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
                Path(path).chmod(0o600)
            self._db.executescript(SQLITE_SCHEMA)
            self.sql = _Sql(postgres=False)

    # --- low-level -------------------------------------------------------------

    @contextmanager
    def _tx(self):
        """Yields an `exec(sql, params) -> rows` callable running inside one transaction."""
        if self._pool is not None:
            with self._pool.connection() as conn, conn.transaction():

                def ex(sql: str, params: Sequence[Any] = ()) -> list[Any]:
                    cur = conn.execute(self.sql.q(sql), params)
                    return cur.fetchall() if cur.description else []

                yield ex
        else:
            with self._lock:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    yield lambda sql, params=(): self._db.execute(sql, params).fetchall()
                    self._db.execute("COMMIT")
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise

    # --- keys -------------------------------------------------------------------

    @staticmethod
    def _aad(*parts: str) -> bytes:
        return "|".join(parts).encode()

    def _dek(self, ex: Callable, owner: str, patient_id: str) -> AESGCM | None:
        rows = ex("SELECT wrapped_dek FROM md_patients WHERE owner = ? AND patient_id = ?", (owner, patient_id))
        if not rows:
            return None
        return AESGCM(self.kek.decrypt(rows[0][0], self._aad("dek", owner, patient_id)))

    @staticmethod
    def _seal(dek: AESGCM, data: dict[str, Any], aad: bytes) -> str:
        nonce = secrets.token_bytes(12)
        return (nonce + dek.encrypt(nonce, json.dumps(data, separators=(",", ":")).encode(), aad)).hex()

    @staticmethod
    def _open(dek: AESGCM, token: str, aad: bytes) -> dict[str, Any]:
        raw = bytes.fromhex(token)
        return json.loads(dek.decrypt(raw[:12], raw[12:], aad))

    # --- audit (hash-chained per owner: tampering or deletion breaks the chain) ----

    def _audit(self, ex: Callable, owner: str, client_id: str | None, action: str, patient_id: str | None = None,
               section: str | None = None, item_id: str | None = None) -> None:
        if self.sql.postgres:
            ex("SELECT pg_advisory_xact_lock(hashtext(?))", ("audit:" + owner,))
        last = ex("SELECT hash FROM md_audit WHERE owner = ? ORDER BY seq DESC LIMIT 1", (owner,))
        prev = last[0][0] if last else "genesis"
        ts = time.time()
        digest = hashlib.sha256(
            json.dumps([prev, ts, owner, client_id, action, patient_id, section, item_id]).encode()
        ).hexdigest()
        ex(
            "INSERT INTO md_audit (ts, owner, client_id, action, patient_id, section, item_id, prev_hash, hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, owner, client_id, action, patient_id, section, item_id, prev, digest),
        )

    # --- patients ---------------------------------------------------------------

    def create_patient(self, owner: str, client_id: str | None, max_patients: int) -> str:
        patient_id = "pt_" + secrets.token_urlsafe(9)
        dek = secrets.token_bytes(32)
        with self._tx() as ex:
            (count,) = ex("SELECT COUNT(*) FROM md_patients WHERE owner = ?", (owner,))[0]
            if count >= max_patients:
                raise ValueError(f"Limit of {max_patients} patient profiles per account reached")
            wrapped = self.kek.encrypt(dek, self._aad("dek", owner, patient_id))
            ex("INSERT INTO md_patients VALUES (?, ?, ?, ?)", (owner, patient_id, wrapped, time.time()))
            self._audit(ex, owner, client_id, "patient.create", patient_id)
        return patient_id

    def patient_ids(self, owner: str) -> list[str]:
        with self._tx() as ex:
            return [r[0] for r in ex("SELECT patient_id FROM md_patients WHERE owner = ? ORDER BY created_at", (owner,))]

    def delete_patient(self, owner: str, client_id: str | None, patient_id: str) -> bool:
        with self._tx() as ex:
            # Crypto-shred: once the wrapped DEK is gone, any surviving copy (backups, WAL) is unreadable.
            existed = ex("SELECT 1 FROM md_patients WHERE owner = ? AND patient_id = ?", (owner, patient_id))
            ex("DELETE FROM md_patients WHERE owner = ? AND patient_id = ?", (owner, patient_id))
            ex("DELETE FROM md_records WHERE owner = ? AND patient_id = ?", (owner, patient_id))
            if existed:
                self._audit(ex, owner, client_id, "patient.erase", patient_id)
        return bool(existed)

    # --- records ----------------------------------------------------------------

    def put(self, owner: str, client_id: str | None, patient_id: str, section: str, item_id: str,
            data: dict[str, Any], max_items: int) -> None:
        with self._tx() as ex:
            dek = self._dek(ex, owner, patient_id)
            if dek is None:
                raise KeyError(patient_id)
            exists = ex(
                "SELECT 1 FROM md_records WHERE owner = ? AND patient_id = ? AND section = ? AND item_id = ?",
                (owner, patient_id, section, item_id),
            )
            if not exists:
                (count,) = ex(
                    "SELECT COUNT(*) FROM md_records WHERE owner = ? AND patient_id = ? AND section = ?",
                    (owner, patient_id, section),
                )[0]
                if count >= max_items:
                    raise ValueError(f"Section {section} is full ({max_items} items)")
            token = self._seal(dek, data, self._aad(owner, patient_id, section, item_id))
            now = time.time()
            if exists:
                ex(
                    "UPDATE md_records SET ciphertext = ?, updated_at = ? "
                    "WHERE owner = ? AND patient_id = ? AND section = ? AND item_id = ?",
                    (token, now, owner, patient_id, section, item_id),
                )
            else:
                ex("INSERT INTO md_records VALUES (?, ?, ?, ?, ?, ?, ?)", (owner, patient_id, section, item_id, token, now, now))
            self._audit(ex, owner, client_id, "record.update" if exists else "record.create", patient_id, section, item_id)

    def delete(self, owner: str, client_id: str | None, patient_id: str, section: str, item_id: str) -> bool:
        with self._tx() as ex:
            found = ex(
                "SELECT 1 FROM md_records WHERE owner = ? AND patient_id = ? AND section = ? AND item_id = ?",
                (owner, patient_id, section, item_id),
            )
            ex(
                "DELETE FROM md_records WHERE owner = ? AND patient_id = ? AND section = ? AND item_id = ?",
                (owner, patient_id, section, item_id),
            )
            if found:
                self._audit(ex, owner, client_id, "record.delete", patient_id, section, item_id)
        return bool(found)

    def read_chart(self, owner: str, client_id: str | None, patient_id: str,
                   sections: Sequence[str] | None = None, *, purpose: str = "read") -> dict[str, list[tuple[str, dict]]] | None:
        with self._tx() as ex:
            dek = self._dek(ex, owner, patient_id)
            if dek is None:
                return None
            rows = ex(
                "SELECT section, item_id, ciphertext FROM md_records WHERE owner = ? AND patient_id = ? ORDER BY created_at",
                (owner, patient_id),
            )
            chart: dict[str, list[tuple[str, dict]]] = {}
            for section, item_id, token in rows:
                if sections and section not in sections:
                    continue
                chart.setdefault(section, []).append(
                    (item_id, self._open(dek, token, self._aad(owner, patient_id, section, item_id)))
                )
            self._audit(ex, owner, client_id, f"chart.{purpose}", patient_id)
        return chart

    def audit_log(self, owner: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._tx() as ex:
            rows = ex(
                "SELECT seq, ts, client_id, action, patient_id, section, item_id, prev_hash, hash FROM md_audit "
                "WHERE owner = ? ORDER BY seq DESC LIMIT ?",
                (owner, limit),
            )
        keys = ("seq", "ts", "client_id", "action", "patient_id", "section", "item_id", "prev_hash", "hash")
        return [dict(zip(keys, r)) for r in rows]

    def verify_audit_chain(self, owner: str) -> bool:
        with self._tx() as ex:
            rows = ex(
                "SELECT ts, client_id, action, patient_id, section, item_id, prev_hash, hash FROM md_audit "
                "WHERE owner = ? ORDER BY seq",
                (owner,),
            )
        prev = "genesis"
        for ts, client_id, action, patient_id, section, item_id, prev_hash, h in rows:
            expect = hashlib.sha256(
                json.dumps([prev, ts, owner, client_id, action, patient_id, section, item_id]).encode()
            ).hexdigest()
            if prev_hash != prev or h != expect:
                return False
            prev = h
        return True

    def rewrap_keys(self) -> int:
        """KEK rotation: re-wrap every patient DEK with the active KEK (records are untouched)."""
        n = 0
        with self._tx() as ex:
            for owner, pid, wrapped in ex("SELECT owner, patient_id, wrapped_dek FROM md_patients"):
                if wrapped.startswith(self.kek.active + ":"):
                    continue
                dek = self.kek.decrypt(wrapped, self._aad("dek", owner, pid))
                ex(
                    "UPDATE md_patients SET wrapped_dek = ? WHERE owner = ? AND patient_id = ?",
                    (self.kek.encrypt(dek, self._aad("dek", owner, pid)), owner, pid),
                )
                n += 1
        return n


class AsyncRecordRepo:
    """Runs repository calls in a worker thread so database I/O never blocks the event loop."""

    def __init__(self, repo: RecordRepo):
        self.repo = repo

    def __getattr__(self, name: str) -> Any:
        fn = getattr(self.repo, name)

        async def call(*args: Any, **kwargs: Any) -> Any:
            return await anyio.to_thread.run_sync(lambda: fn(*args, **kwargs))

        return call
