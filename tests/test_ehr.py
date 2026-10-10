"""SMART on FHIR patient-portal connector (MyChart and others) against a fake FHIR server."""

import json
from urllib.parse import parse_qs, urlparse

import httpx2 as httpx
import pytest

from medical_doctor.auth.store import AsyncStore, Store
from medical_doctor.config import Settings
from medical_doctor.ehr import EhrConnector, EhrError, check_public_https, parse_pairs
from tests.test_records import make

BASE = "https://fhir.hospital.example/api/FHIR/R4"


def fhir_server(seen):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        path = req.url.path
        if path.endswith("/.well-known/smart-configuration"):
            return httpx.Response(200, json={"authorization_endpoint": "https://fhir.hospital.example/oauth2/authorize",
                                             "token_endpoint": "https://fhir.hospital.example/oauth2/token"})
        if path.endswith("/oauth2/token"):
            form = parse_qs(req.content.decode())
            assert form["code"] == ["good-code"] and form["code_verifier"][0]
            return httpx.Response(200, json={"access_token": "portal-token", "patient": "eP1", "token_type": "Bearer"})
        assert req.headers["authorization"] == "Bearer portal-token"
        if path.endswith("/Patient/eP1"):
            return httpx.Response(200, json={"resourceType": "Patient", "id": "eP1", "name": [{"text": "Dana Portal"}],
                                             "birthDate": "1975-02-03", "gender": "female"})
        rtype = path.rsplit("/", 1)[-1]
        q = parse_qs(req.url.query.decode() if isinstance(req.url.query, bytes) else str(req.url.query))
        if rtype == "Condition":
            if "page" not in q:  # first page links to a second page, then to a foreign host
                return httpx.Response(200, json={"resourceType": "Bundle", "entry": [
                    {"resource": {"resourceType": "Condition", "id": "c1", "subject": {"reference": "Patient/eP1"},
                                  "code": {"text": "Asthma"}}}],
                    "link": [{"relation": "next", "url": f"{BASE}/Condition?patient=eP1&page=2"}]})
            return httpx.Response(200, json={"resourceType": "Bundle", "entry": [
                {"resource": {"resourceType": "Condition", "id": "c2", "subject": {"reference": "Patient/eP1"}, "code": {"text": "GERD"}}}],
                "link": [{"relation": "next", "url": "https://evil.example/steal"}]})
        if rtype == "MedicationRequest":
            return httpx.Response(200, json={"resourceType": "Bundle", "entry": [
                {"resource": {"resourceType": "MedicationRequest", "id": "m1", "status": "active", "intent": "order",
                              "subject": {"reference": "Patient/eP1"}, "medicationReference": {"display": "albuterol HFA"}}}]})
        if rtype == "Observation" and q.get("category") == ["laboratory"]:
            return httpx.Response(200, json={"resourceType": "Bundle", "entry": [
                {"resource": {"resourceType": "Observation", "id": "o1", "status": "final", "subject": {"reference": "Patient/eP1"},
                              "category": [{"coding": [{"code": "laboratory"}]}], "code": {"text": "LDL (Labcorp)"},
                              "valueQuantity": {"value": 131, "unit": "mg/dL"}}},
                {"resource": {"resourceType": "OperationOutcome", "issue": []}}]})
        if rtype == "Immunization":
            return httpx.Response(403, json={})
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})
    return handler


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("MEDICAL_DOCTOR_SMART_CLIENTS", "epic=client-123")
    seen = []
    _, service = make()
    conn = EhrConnector(Settings(public_url="https://doctor.example"), AsyncStore(Store(":memory:")), lambda: service,
                        http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(fhir_server(seen))))
    conn._resolve = lambda url: url.rstrip("/")
    return conn, service, seen


async def start(conn, service):
    pid = await service.create_patient("alice", "c", {"name": "Dana"})
    out = await conn.start(owner="alice", client_id="c", account="alice@example.com", patient_id=pid, patient_label="Dana",
                           vendor="epic", fhir_base_url=BASE)
    state = parse_qs(urlparse(out["connect_url"]).query)["s"][0]
    return pid, state


async def test_full_connect_imports_records_and_never_leaks_the_token(setup):
    conn, service, seen = setup
    pid, state = await start(conn, service)
    ctx = await conn.astore.get("ehr", state)
    q = parse_qs(urlparse(ctx["authorize"]).query)
    assert q["client_id"] == ["client-123"] and q["aud"] == [BASE] and q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == ["https://doctor.example/ehr/callback"] and "patient/Observation.read" in q["scope"][0]
    ctx["browser"] = "b1"  # what /ehr/start does
    await conn.astore.put("ehr", state, ctx, 600)
    result = await conn.complete(state, "good-code", "b1")
    assert result["imported"] == {"conditions": 2, "medications": 1, "observations": 1}
    assert result["unavailable"] == {"Immunization": "not available (HTTP 403)"}
    chart = await service.chart("alice", "c", pid)
    assert {c["name"] for c in chart["conditions"]} == {"Asthma", "GERD"} and chart["medications"][0]["name"] == "albuterol HFA"
    assert chart["profile"]["name"] == "Dana" and chart["profile"]["date_of_birth"] == "1975-02-03"  # blanks filled only
    assert all(r.url.host != "evil.example" for r in seen)
    assert await conn.astore.get("ehr", state) is None  # single use


async def test_other_browser_and_reuse_refused(setup):
    conn, service, _ = setup
    _, state = await start(conn, service)
    ctx = await conn.astore.get("ehr", state)
    ctx["browser"] = "b1"
    await conn.astore.put("ehr", state, ctx, 600)
    with pytest.raises(EhrError, match="same browser"):
        await conn.complete(state, "good-code", "someone-else")
    with pytest.raises(EhrError, match="expired"):
        await conn.complete(state, "good-code", "b1")


async def test_unconfigured_vendor_and_missing_setup(setup, monkeypatch):
    conn, service, _ = setup
    pid = await service.create_patient("alice", "c", {"name": "Dana"})
    with pytest.raises(EhrError, match="No app registered"):
        await conn.start(owner="alice", client_id="c", account="a", patient_id=pid, patient_label="D", vendor="oracle", fhir_base_url=BASE)
    monkeypatch.delenv("MEDICAL_DOCTOR_SMART_CLIENTS")
    bare = EhrConnector(Settings(public_url="https://doctor.example"), conn.astore, lambda: service)
    assert "MEDICAL_DOCTOR_SMART_CLIENTS" in bare.available()


@pytest.mark.parametrize("url", ["http://fhir.example/R4", "https://127.0.0.1/R4", "https://user:pw@fhir.example/R4",
                                 "https://localhost/R4", "https://169.254.169.254/latest", "https://10.0.0.5/fhir"])
def test_ssrf_guard(url):
    with pytest.raises(EhrError):
        check_public_https(url)


def test_parse_pairs():
    assert parse_pairs("epic=abc, Oracle = d=e ,bad") == {"epic": "abc", "oracle": "d=e"}


async def test_tool_explains_missing_setup():
    from mcp import Client

    from medical_doctor.server import mcp

    async with Client(mcp) as c:
        pid = (await c.call_tool("patient_create", {"profile": {"name": "Z"}})).structured_content["patient_id"]
        r = await c.call_tool("ehr_connect", {"patient_id": pid, "fhir_base_url": "sandbox"})
        assert r.is_error and "public https URL" in r.content[0].text
        await c.call_tool("patient_erase", {"patient_id": pid, "confirm": "ERASE"})


def test_consent_page_binds_browser(monkeypatch):
    from starlette.testclient import TestClient

    from medical_doctor.server import build_server

    monkeypatch.setenv("MEDICAL_DOCTOR_SMART_CLIENTS", "epic=client-123")
    store = Store(":memory:")
    server = build_server(Settings(public_url="https://doctor.example", consult_limit_per_hour=0), store=store, local_mode=True)
    state = "s" * 43
    store.put("ehr", state, {"owner": "o", "client_id": None, "account": "alice@example.com", "patient_id": "pt_1",
                             "patient_label": "Dana", "vendor": "epic", "base": BASE, "token": "t",
                             "authorize": "https://fhir.hospital.example/oauth2/authorize?x=1", "verifier": "v", "browser": None}, 600)
    app = server.streamable_http_app(host="127.0.0.1")
    with TestClient(app, base_url="https://doctor.example") as c:
        page = c.get(f"/ehr/start?s={state}")
        assert page.status_code == 200 and "alice@example.com" in page.text and "Dana" in page.text
        other = TestClient(app, base_url="https://doctor.example")
        assert other.post("/ehr/go", data={"s": state}, follow_redirects=False).status_code == 404  # no cookie
        go = c.post("/ehr/go", data={"s": state}, follow_redirects=False)
        assert go.status_code == 303 and go.headers["location"].startswith("https://fhir.hospital.example/oauth2/authorize")
        assert "expired" in c.get("/ehr/start?s=bogus").text
