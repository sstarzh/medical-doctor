"""A chart saved in one conversation must be there in the next one (new MCP session, new server instance)."""

import os

import pytest
from mcp import Client
from mcp.server.auth.provider import AccessToken

import medical_doctor.identity as ident
from medical_doctor.config import Settings
from medical_doctor.records.crypto import generate_key
from medical_doctor.server import build_server, chart_suggestions

PG_URL = os.environ.get("MEDICAL_DOCTOR_TEST_DATABASE_URL")


def _p(r):
    sc = r.structured_content
    return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.skipif(not PG_URL, reason="no test database"))])
def settings(request, tmp_path):
    keys = f"k1:{generate_key()}"
    if request.param == "sqlite":
        return Settings(db_path=str(tmp_path / "auth.db"), data_keys=keys)
    import psycopg

    with psycopg.connect(PG_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS medical_doctor CASCADE")
    return Settings(database_url=PG_URL, data_keys=keys)


async def test_chart_survives_new_session_and_new_server_instance(settings):
    first = build_server(settings, local_mode=True)
    async with Client(first) as c:  # conversation 1
        pid = _p(await c.call_tool("patient_create", {"profile": {"name": "Sam", "date_of_birth": "1980-01-01"}}))["patient_id"]
        await c.call_tool("patient_update", {"patient_id": pid, "section": "allergies", "data": {"substance": "penicillin"}})
    second = build_server(settings, local_mode=True)  # e.g. another Vercel instance / cold start
    async with Client(second) as c:  # conversation 2
        listed = _p(await c.call_tool("patient_list", {}))
        assert [p["patient_id"] for p in listed] == [pid]
        chart = _p(await c.call_tool("patient_chart", {"patient_id": pid}))
        assert chart["allergies"][0]["substance"] == "penicillin"


def test_owner_is_the_person_not_the_chat_or_client(monkeypatch):
    owners = set()
    for client_id in ("claude-chat-1", "claude-chat-2", "chatgpt"):
        tok = AccessToken(token="t-" + client_id, client_id=client_id, scopes=["mcp"], subject="google:123",
                          claims={"iss": "https://doctor.example"})
        monkeypatch.setattr(ident, "get_access_token", lambda tok=tok: tok)
        owners.add(ident.current_principal(False).owner)
    assert len(owners) == 1
    other = AccessToken(token="x", client_id="c", scopes=["mcp"], subject="google:999", claims={"iss": "https://doctor.example"})
    monkeypatch.setattr(ident, "get_access_token", lambda: other)
    assert ident.current_principal(False).owner not in owners


def test_chart_suggestions_keep_only_valid_stated_facts():
    out = chart_suggestions({"patient_facts": [
        {"section": "medications", "data": {"name": "lisinopril", "dose": "10 mg"}},
        {"section": "allergies", "data": {"reaction": "missing substance"}},
        {"section": "care_plan", "data": {"kind": "test", "summary": "x"}},
        {"section": "bogus", "data": {}},
        {"section": "profile", "data": {"weight_kg": 82}},
    ]})
    assert out == [{"section": "medications", "data": {"name": "lisinopril", "dose": "10 mg", "status": "active"}},
                   {"section": "profile", "data": {"relationship": "self", "weight_kg": 82.0}}]


async def test_consult_says_nothing_was_saved(monkeypatch):
    from medical_doctor import physician
    from medical_doctor.server import mcp

    async def fake_run(**kw):
        return {"ok": True, "note": "n", "structured": {"urgency": "routine",
                "patient_facts": [{"section": "conditions", "data": {"name": "asthma"}}]}}

    monkeypatch.setattr(physician, "credentials_for", lambda p: True)
    monkeypatch.setattr(physician, "run_consult", fake_run)
    async with Client(mcp) as c:
        r = _p(await c.call_tool("consult", {"case": "I have asthma and a cough"}))
    assert r["saved_to_chart"] is False and "patient_create" in r["memory_note"]
    assert r["suggested_chart_updates"] == [{"section": "conditions", "data": {"name": "asthma", "status": "active"}}]
