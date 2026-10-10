"""Personal health record: encryption at rest, tenancy, erasure, audit, export, MCP tools."""

import json
import os

import pytest
from mcp import Client

from medical_doctor.records.crypto import Cipher, generate_key
from medical_doctor.records.repo import AsyncRecordRepo, RecordRepo
from medical_doctor.records.service import PatientService, RecordsError, summarize, to_fhir_bundle
from medical_doctor.server import mcp

PG_URL = os.environ.get("MEDICAL_DOCTOR_TEST_DATABASE_URL")


def make(path=":memory:", pg_pool=None):
    kek = Cipher.from_spec(f"k1:{generate_key()}")
    repo = RecordRepo(kek, pg_pool=pg_pool) if pg_pool else RecordRepo(kek, sqlite_path=path)
    return repo, PatientService(AsyncRecordRepo(repo))


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.skipif(not PG_URL, reason="no test database"))])
def svc(request):
    if request.param == "sqlite":
        repo, service = make()
        yield repo, service
        return
    import psycopg

    from medical_doctor.auth.pgstore import PostgresStore

    with psycopg.connect(PG_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS medical_doctor CASCADE")
    store = PostgresStore(PG_URL)
    repo, service = make(pg_pool=store.pool)
    yield repo, service
    store.close()


def raw_rows(repo):
    if repo.sql.postgres:
        with repo._pool.connection() as conn:
            return conn.execute("SELECT owner, patient_id, section, item_id, ciphertext FROM medical_doctor.records").fetchall()
    return repo._db.execute("SELECT owner, patient_id, section, item_id, ciphertext FROM md_records").fetchall()


async def test_chart_roundtrip_and_encryption_at_rest(svc):
    repo, s = svc
    pid = await s.create_patient("alice", "c", {"name": "Alice Example", "date_of_birth": "1980-05-01", "sex_at_birth": "female"})
    await s.upsert("alice", "c", pid, "allergies", {"substance": "penicillin", "reaction": "hives", "severity": "moderate"}, None)
    await s.upsert("alice", "c", pid, "medications", {"name": "lisinopril", "dose": "10 mg", "frequency": "daily"}, None)
    chart = await s.chart("alice", "c", pid)
    assert chart["profile"]["name"] == "Alice Example"
    assert chart["allergies"][0]["substance"] == "penicillin"
    blob = json.dumps(raw_rows(repo))
    for secret in ("Alice", "penicillin", "lisinopril", "1980-05-01"):
        assert secret not in blob  # nothing clinical or identifying is stored in clear


async def test_singleton_sections_merge(svc):
    _, s = svc
    pid = await s.create_patient("a", "c", {"name": "A", "weight_kg": 70})
    await s.upsert("a", "c", pid, "profile", {"height_cm": 175}, None)
    p = (await s.chart("a", "c", pid))["profile"]
    assert p["weight_kg"] == 70 and p["height_cm"] == 175


async def test_tenancy_isolation(svc):
    _, s = svc
    pid = await s.create_patient("alice", "c", {"name": "Alice"})
    with pytest.raises(RecordsError):
        await s.chart("mallory", "c", pid)
    with pytest.raises(RecordsError):
        await s.upsert("mallory", "c", pid, "notes", {"title": "x", "text": "y"}, None)
    assert await s.list_patients("mallory", "c") == []


async def test_ciphertext_is_bound_to_its_row(svc):
    repo, s = svc
    pid = await s.create_patient("alice", "c", {"name": "Alice"})
    await s.upsert("alice", "c", pid, "notes", {"title": "a", "text": "secret one"}, "n1")
    await s.upsert("alice", "c", pid, "notes", {"title": "b", "text": "secret two"}, "n2")
    # An attacker with database write access swaps ciphertexts between rows: decryption must fail.
    rows = {r[3]: r[4] for r in raw_rows(repo) if r[2] == "notes"}
    q = "UPDATE {t} SET ciphertext = {p} WHERE item_id = {p}"
    if repo.sql.postgres:
        with repo._pool.connection() as conn:
            conn.execute(q.format(t="medical_doctor.records", p="%s"), (rows["n2"], "n1"))
    else:
        repo._db.execute(q.format(t="md_records", p="?"), (rows["n2"], "n1"))
    with pytest.raises(Exception):
        await s.chart("alice", "c", pid)


async def test_erase_is_crypto_shredding(svc):
    repo, s = svc
    pid = await s.create_patient("alice", "c", {"name": "Alice"})
    await s.upsert("alice", "c", pid, "conditions", {"name": "asthma"}, None)
    assert await s.erase_patient("alice", "c", pid)
    assert raw_rows(repo) == []
    with pytest.raises(RecordsError):
        await s.chart("alice", "c", pid)


async def test_audit_chain_detects_tampering(svc):
    repo, s = svc
    pid = await s.create_patient("alice", "c", {"name": "Alice"})
    await s.chart("alice", "c", pid)
    log = await s.audit_log("alice")
    assert log["chain_intact"] and {e["action"] for e in log["events"]} >= {"patient.create", "chart.read"}
    if repo.sql.postgres:
        with repo._pool.connection() as conn:
            conn.execute("UPDATE medical_doctor.audit SET action = 'nothing' WHERE action = 'chart.read'")
    else:
        repo._db.execute("UPDATE md_audit SET action = 'nothing' WHERE action = 'chart.read'")
    assert (await s.audit_log("alice"))["chain_intact"] is False


async def test_key_rotation_rewraps_without_data_loss():
    k1, k2 = generate_key(), generate_key()
    repo = RecordRepo(Cipher.from_spec(f"k1:{k1}"))
    s = PatientService(AsyncRecordRepo(repo))
    pid = await s.create_patient("alice", "c", {"name": "Alice"})
    repo.kek = Cipher.from_spec(f"k2:{k2},k1:{k1}")  # new active key, old one still listed
    assert repo.rewrap_keys() == 1
    repo.kek = Cipher.from_spec(f"k2:{k2}")  # old key retired
    assert (await s.chart("alice", "c", pid))["profile"]["name"] == "Alice"


async def test_validation_and_limits(svc):
    _, s = svc
    with pytest.raises(Exception):
        await s.create_patient("a", "c", {"name": "x", "unknown_field": 1})
    pid = await s.create_patient("a", "c", {"name": "A"})
    with pytest.raises(RecordsError):
        await s.upsert("a", "c", pid, "allergies", {"reaction": "missing substance"}, None)
    with pytest.raises(RecordsError):
        await s.upsert("a", "c", pid, "bogus", {}, None)
    with pytest.raises(RecordsError):  # oversized free-form payload
        await s.upsert("a", "c", pid, "care_plan", {"kind": "test", "summary": "x", "details": {"blob": "x" * 70_000}}, None)


def test_summary_and_fhir_export():
    chart = {
        "patient_id": "pt_1",
        "profile": {"name": "Sam", "date_of_birth": "2024-01-15", "sex_at_birth": "male", "weight_kg": 9.5},
        "social_history": {}, "allergies": [{"id": "a1", "substance": "amoxicillin", "severity": "anaphylaxis"}],
        "conditions": [{"id": "c1", "name": "eczema"}], "medications": [], "immunizations": [], "procedures": [],
        "family_history": [], "observations": [{"id": "o1", "name": "eGFR", "value": "95", "date": "2025-01-01"}],
        "notes": [], "care_plan": [],
    }
    from datetime import date

    text = summarize(chart, today=date(2025, 1, 15))
    assert "1.0 y" in text and "amoxicillin (allergy, anaphylaxis)" in text and "eGFR: 95 (2025-01-01)" in text
    bundle = to_fhir_bundle(chart)
    types = [e["resource"]["resourceType"] for e in bundle["entry"]]
    assert bundle["resourceType"] == "Bundle" and types[0] == "Patient" and "AllergyIntolerance" in types


# --- MCP tools (local single-user mode) ---------------------------------------


def _p(result):
    sc = result.structured_content
    return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc


async def test_record_tools_end_to_end():
    async with Client(mcp) as c:
        pid = _p(await c.call_tool("patient_create", {"profile": {"name": "Kid", "relationship": "child", "date_of_birth": "2022-03-01", "weight_kg": 14}}))["patient_id"]
        await c.call_tool("patient_update", {"patient_id": pid, "section": "allergies", "data": {"substance": "penicillin", "severity": "anaphylaxis"}})
        chart = _p(await c.call_tool("patient_chart", {"patient_id": pid}))
        assert chart["allergies"][0]["substance"] == "penicillin"
        assert pid in [p["patient_id"] for p in _p(await c.call_tool("patient_list", {}))]
        prev = _p(await c.call_tool("preventive_care_due", {"patient_id": pid}))
        assert any(i["item"] == "Immunizations" for i in prev["items"])
        export = _p(await c.call_tool("patient_export", {"patient_id": pid}))
        assert export["resourceType"] == "Bundle"
        bad = await c.call_tool("patient_erase", {"patient_id": pid, "confirm": "yes"})
        assert bad.is_error  # explicit confirmation required
        assert _p(await c.call_tool("patient_erase", {"patient_id": pid, "confirm": "ERASE"}))["erased"]


async def test_record_tools_need_personal_identity(monkeypatch):
    from mcp.server.auth.provider import AccessToken

    import medical_doctor.identity as ident

    shared = AccessToken(token="t", client_id="api-key:team", scopes=["mcp"], subject="api-key:team", claims={"iss": "x"})
    monkeypatch.setattr(ident, "get_access_token", lambda: shared)
    async with Client(mcp) as c:
        r = await c.call_tool("patient_list", {})
        assert r.is_error and "personal sign-in" in r.content[0].text


def test_summary_shows_trends_and_recent_visits():
    from medical_doctor.records.service import observation_trends

    obs = [{"name": "HbA1c", "value": "7.2", "unit": "%", "date": "2025-01-10"},
           {"name": "hba1c", "value": "8.1", "unit": "%", "date": "2025-07-15"},
           {"name": "HbA1c", "value": "7.6", "date": "2025-04-02"},
           {"name": "blood pressure", "value": "142/88", "date": "2025-07-01"}]
    lines = observation_trends(obs)
    assert lines[0].endswith("7.2 (2025-01-10) → 7.6 (2025-04-02) → 8.1 (2025-07-15) ↑") and "%" in lines[0]
    assert lines[1] == "blood pressure: 142/88 (2025-07-01)"
    chart = {"profile": {}, "observations": obs, "notes": [
        {"title": "Consult: cough", "text": "better", "date": "2025-05-01"},
        {"title": "Consult: knee", "text": "pain", "date": "2025-08-01"}]}
    text = summarize(chart)
    assert "Recent visits: 2025-05-01 Consult: cough" in text and text.index("cough") < text.index("knee")
