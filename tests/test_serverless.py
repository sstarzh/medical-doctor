"""The Vercel/serverless entry point must work whether or not the runtime sends ASGI lifespan events."""

import httpx2 as httpx

from medical_doctor.config import Settings
from medical_doctor.serverless import create_serverless_app

HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}


async def test_works_without_lifespan_events():
    app = create_serverless_app(Settings())
    # httpx's ASGITransport never sends lifespan events, like some serverless runtimes.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://x.vercel.app") as c:
        for _ in range(2):  # second request reuses the running app
            r = await c.post("/mcp", headers=HEADERS, json=INIT)
            assert r.status_code == 200, r.text
            assert "medical-doctor" in r.text
        assert (await c.get("/healthz")).json()["status"] == "ok"


async def test_honours_lifespan_events():
    app = create_serverless_app(Settings())
    sent = []
    messages = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message["type"])

    await app({"type": "lifespan"}, receive, send)
    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]


def test_vercel_oauth_requires_database(monkeypatch):
    import pytest

    from medical_doctor.config import load_settings

    for k, v in {"VERCEL": "1", "VERCEL_PROJECT_PRODUCTION_URL": "doctor.vercel.app", "MEDICAL_DOCTOR_AUTH": "oauth",
                 "GOOGLE_CLIENT_ID": "g", "GOOGLE_CLIENT_SECRET": "s", "MEDICAL_DOCTOR_ALLOWED_DOMAINS": "x.com"}.items():
        monkeypatch.setenv(k, v)
    for k in ("POSTGRES_URL", "DATABASE_URL", "MEDICAL_DOCTOR_DATABASE_URL", "MEDICAL_DOCTOR_PUBLIC_URL"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValueError, match="POSTGRES_URL"):
        load_settings()
    monkeypatch.setenv("POSTGRES_URL", "postgres://u:p@db.example.com/postgres")
    s = load_settings()
    assert s.public_url == "https://doctor.vercel.app" and s.database_url
