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


def _fake_consult(structured):
    async def fake_run(**kw):
        fake_run.seen = kw
        return {"ok": True, "note": "n", "structured": structured}
    return fake_run


async def test_first_consult_opens_a_chart_and_saves_everything_stated(monkeypatch, settings):
    from medical_doctor import physician

    server = build_server(settings, local_mode=True)
    monkeypatch.setattr(physician, "credentials_for", lambda p: True)
    first = _fake_consult({"urgency": "routine", "patient_is_user": True, "problem_representation": "41M with cough",
                           "differential": [{"diagnosis": "acute bronchitis", "likelihood": "likely"}],
                           "patient_facts": [{"section": "conditions", "data": {"name": "asthma"}},
                                             {"section": "medications", "data": {"name": "albuterol", "dose": "2 puffs"}},
                                             {"section": "profile", "data": {"weight_kg": 82}},
                                             {"section": "observations", "data": {"name": "temperature", "value": "38.1", "unit": "C"}}]})
    monkeypatch.setattr(physician, "run_consult", first)
    async with Client(server) as c:  # conversation 1, no profile yet
        r = _p(await c.call_tool("consult", {"case": "I'm 41, asthma, on albuterol, 82 kg, cough and 38.1 fever"}))
    assert r["chart"]["resolved_by"] == "created" and r["chart"]["saved"]["saved"]["notes"] == 1
    pid = r["chart"]["patient_id"]

    second = _fake_consult({"urgency": "routine", "patient_is_user": True,
                            "patient_facts": [{"section": "medications", "data": {"name": "Albuterol HFA", "dose": "4 puffs"}}]})
    monkeypatch.setattr(physician, "run_consult", second)
    async with Client(build_server(settings, local_mode=True)) as c:  # conversation 2, new server instance
        r2 = _p(await c.call_tool("consult", {"case": "cough is better, now using 4 puffs"}))
        assert r2["chart"] == {"patient_id": pid, "resolved_by": "only_profile", "saved": r2["chart"]["saved"]}
        assert "asthma" in second.seen["chart_summary"] and "albuterol" in second.seen["chart_summary"]  # retrieved
        chart = _p(await c.call_tool("patient_chart", {"patient_id": pid}))
    assert [m["dose"] for m in chart["medications"]] == ["4 puffs"]  # merged, not duplicated
    assert chart["profile"]["weight_kg"] == 82 and chart["conditions"][0]["name"] == "asthma"
    assert chart["observations"][0]["date"] and len(chart["notes"]) == 2


async def test_consult_about_someone_else_is_not_filed_in_my_chart(monkeypatch, settings):
    from medical_doctor import physician

    server = build_server(settings, local_mode=True)
    monkeypatch.setattr(physician, "credentials_for", lambda p: True)
    monkeypatch.setattr(physician, "run_consult", _fake_consult({"urgency": "urgent", "patient_is_user": False,
        "patient_facts": [{"section": "profile", "data": {"date_of_birth": "2023-01-01"}}]}))
    async with Client(server) as c:
        r = _p(await c.call_tool("consult", {"case": "my 3 year old has a fever"}))
        assert r["chart"]["saved"]["saved"] == {} and "someone else" in r["chart"]["note"]
        chart = _p(await c.call_tool("patient_chart", {"patient_id": r["chart"]["patient_id"]}))
    assert "date_of_birth" not in chart["profile"] and chart["notes"] == []


async def test_my_chart_and_chart_record(settings):
    server = build_server(settings, local_mode=True)
    async with Client(server) as c:
        first = _p(await c.call_tool("my_chart", {}))
        assert first["first_visit"] and first["resolved_by"] == "created"
        saved = _p(await c.call_tool("chart_record", {"facts": [
            {"section": "allergies", "data": {"substance": "penicillin", "reaction": "hives"}},
            {"section": "family_history", "data": {"relation": "father", "condition": "MI", "age_at_onset": 52}},
            {"section": "allergies", "data": {"reaction": "no substance"}}], "note": "Told me about allergies today"}))
        assert saved["saved"] == {"allergies": 1, "family_history": 1, "notes": 1} and len(saved["rejected"]) == 1
    async with Client(build_server(settings, local_mode=True)) as c:
        again = _p(await c.call_tool("my_chart", {}))
    assert not again["first_visit"] and "penicillin" in again["summary"] and again["recent_visits"][0]["title"] == "Patient update"
