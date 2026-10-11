"""Store contract: the SQLite and Postgres (Supabase) stores must behave identically.

Postgres tests run when MEDICAL_DOCTOR_TEST_DATABASE_URL points at a disposable database
(CI provides one); otherwise they are skipped.
"""

import os
import time
from pathlib import Path

import pytest

from medical_doctor.auth.store import Store, StoreFull

PG_URL = os.environ.get("MEDICAL_DOCTOR_TEST_DATABASE_URL")
needs_pg = pytest.mark.skipif(not PG_URL, reason="set MEDICAL_DOCTOR_TEST_DATABASE_URL to run Postgres tests")


def fresh_pg(**kw):
    import psycopg

    with psycopg.connect(PG_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS medical_doctor CASCADE")
    from medical_doctor.auth.pgstore import PostgresStore

    return PostgresStore(PG_URL, **kw)


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=needs_pg)])
def store(request):
    st = Store() if request.param == "sqlite" else fresh_pg()
    yield st
    if request.param == "postgres":
        st.close()


def test_put_get_pop(store):
    store.put("code", "k", {"a": 1}, 60)
    assert store.get("code", "k") == {"a": 1}
    assert store.pop("code", "k") == {"a": 1}
    assert store.pop("code", "k") is None  # single use
    assert store.get("code", "k") is None


def test_expiry_and_extend(store):
    store.put("pending", "old", {"x": 1}, -1)
    assert store.get("pending", "old") is None
    assert store.pop("pending", "old") is None
    store.put("client", "c", {"id": "c"}, 1)
    store.extend("client", "c", 3600)
    time.sleep(1.1)
    assert store.get("client", "c") == {"id": "c"}


def test_upsert_and_revoke_grant(store):
    store.put("access", "a1", {"v": 1}, 60, "g1")
    store.put("access", "a1", {"v": 2}, 60, "g1")
    store.put("refresh", "r1", {"v": 3}, 60, "g1")
    store.put("refresh", "r2", {"v": 4}, 60, "g2")
    assert store.get("access", "a1") == {"v": 2}
    store.revoke_grant("g1")
    assert store.get("access", "a1") is None and store.get("refresh", "r1") is None
    assert store.get("refresh", "r2") == {"v": 4}


def test_caps(store):
    store.caps = {"client": 2}
    store.put("client", "1", {}, 60, capped=True)
    store.put("client", "2", {}, 60, capped=True)
    with pytest.raises(StoreFull):
        store.put("client", "3", {}, 60, capped=True)
    store.put("client", "3", {}, 60)  # uncapped writes (e.g. updates) are not limited


def test_rate_limit_counter(store):
    assert store.hit("consult:a", 2, 3600) and store.hit("consult:a", 2, 3600)
    assert not store.hit("consult:a", 2, 3600)
    assert store.hit("consult:ab", 2, 3600)  # prefix of another bucket is not shared
    assert store.hit("consult:b", 1, 0.5)
    time.sleep(0.6)
    assert store.hit("consult:b", 1, 0.5)  # window slides


def test_purge(store):
    store.put("pending", "gone", {}, -1)
    store.put("pending", "kept", {}, 60)
    store.purge_expired()
    assert store.get("pending", "kept") == {}


@needs_pg
def test_pg_two_instances_share_state_and_limits():
    a = fresh_pg()
    from medical_doctor.auth.pgstore import PostgresStore

    b = PostgresStore(PG_URL)  # a second serverless instance
    try:
        a.put("access", "t", {"sub": "x"}, 60)
        assert b.get("access", "t") == {"sub": "x"}
        assert a.hit("consult:u", 2, 3600) and b.hit("consult:u", 2, 3600)
        assert not a.hit("consult:u", 2, 3600)
    finally:
        a.close()
        b.close()


@needs_pg
def test_pg_tokens_hidden_from_supabase_data_api():
    st = fresh_pg()
    import psycopg

    try:
        with psycopg.connect(PG_URL) as conn:
            schema, rls = conn.execute(
                "SELECT n.nspname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relname = 'kv' AND n.nspname = 'medical_doctor'"
            ).fetchone()
        assert schema == "medical_doctor" and rls is True  # not in `public`, RLS on, no policies
    finally:
        st.close()


def test_database_tls_verifies_the_server_when_a_ca_is_configured(tmp_path):
    from medical_doctor.auth.pgstore import clean_dsn, db_ca_file

    pem = "-----BEGIN CERTIFICATE-----\\nMIIB\\n-----END CERTIFICATE-----"  # as a Vercel env var: literal \\n
    path = db_ca_file(pem)
    assert Path(path).read_text().startswith("-----BEGIN CERTIFICATE-----\nMIIB\n") and db_ca_file(pem) == path
    crlf = db_ca_file("-----BEGIN CERTIFICATE-----\r\nMIIC\r\n-----END CERTIFICATE-----\r\n")  # pasted with return characters
    assert "\r" not in Path(crlf).read_text()
    assert db_ca_file("/etc/ssl/supabase.crt") == "/etc/ssl/supabase.crt" and db_ca_file("") is None
    dsn = clean_dsn("postgres://u:p@db.example.com/postgres", path)
    assert "sslmode=verify-full" in dsn and "sslrootcert=" in dsn
    # The Supabase integration's sslmode=require is upgraded; only an explicit disable is kept.
    supa = clean_dsn("postgres://u:p@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require&supa=x", path)
    assert supa.count("sslmode=") == 1 and "sslmode=verify-full" in supa
    assert "sslmode=disable" in clean_dsn("postgres://u@db.example.com/x?sslmode=disable", path)
    assert "sslmode" not in clean_dsn("postgres://u@localhost/x", path)


def test_clean_dsn_strips_provider_params():
    from medical_doctor.auth.pgstore import clean_dsn

    url = "postgres://u:p@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require&supa=base-pooler.x&pgbouncer=true"
    assert clean_dsn(url) == "postgres://u:p@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require&connect_timeout=5"
    assert "sslmode=require" in clean_dsn("postgres://u:p@db.example.com/postgres")
    assert "sslmode" not in clean_dsn("postgres://u@localhost:5432/x")


@needs_pg
async def test_full_oauth_flow_on_postgres():
    from tests.test_auth import INIT, call, client_for, get_tokens

    st = fresh_pg()
    try:
        async with client_for(store=st) as c:
            _, _, tokens = await get_tokens(c)
            assert (await call(c, "initialize", INIT, tokens["access_token"])).status_code == 200
    finally:
        st.close()


@needs_pg
def test_pg_accepts_nul_and_survives_dropped_connections():
    import psycopg

    st = fresh_pg()
    try:
        st.put("client", "nul", {"client_name": "evil\x00name", "state": "a\\u0000b"}, 60)
        assert st.get("client", "nul") == {"client_name": "evilname", "state": "a\\u0000b"}
        with psycopg.connect(PG_URL, autocommit=True) as admin:  # what Supavisor does to idle connections
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
        assert st.get("client", "nul")["client_name"] == "evilname"  # pool hands out a healthy connection
    finally:
        st.close()
