"""Observability: request IDs, JSON logs, tool timing — and no PHI in any of it."""

import json
import logging

from mcp import Client
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from medical_doctor import observability
from medical_doctor.server import mcp


def test_request_id_echoed_and_generated(caplog):
    app = observability.RequestContext(Starlette(routes=[Route("/x", lambda r: PlainTextResponse(observability.request_id.get()))]))
    c = TestClient(app)
    with caplog.at_level(logging.INFO, "medical_doctor.metrics"):
        r = c.get("/x?q=secret", headers={"x-request-id": "abc-123"})
    assert r.text == "abc-123" and r.headers["x-request-id"] == "abc-123"
    line = [rec for rec in caplog.records if rec.getMessage() == "http_request"][-1]
    assert line.fields["path"] == "/x" and line.fields["status"] == 200 and "secret" not in json.dumps(line.fields)
    r = c.get("/x", headers={"x-request-id": "bad id\n<script>"})
    assert r.headers["x-request-id"] != "bad id\n<script>" and len(r.headers["x-request-id"]) == 16


def test_json_formatter_includes_request_id_and_fields():
    token = observability.request_id.set("rid-1")
    try:
        rec = logging.LogRecord("medical_doctor.metrics", logging.INFO, __file__, 1, "consult", None, None)
        rec.fields = {"event": "consult", "rounds": 3}
        out = json.loads(observability.JsonFormatter().format(rec))
    finally:
        observability.request_id.reset(token)
    assert out["request_id"] == "rid-1" and out["rounds"] == 3 and out["level"] == "info"


async def test_tool_timing_logs_name_and_duration_but_never_arguments(caplog):
    case = "Jane Doe, 34, has crushing chest pain"
    with caplog.at_level(logging.INFO, "medical_doctor.metrics"):
        async with Client(mcp) as c:
            await c.call_tool("red_flag_screen", {"text": case})
    lines = [r.fields for r in caplog.records if getattr(r, "fields", {}).get("event") == "mcp_request"]
    call = [f for f in lines if f.get("tool") == "red_flag_screen"]
    assert call and call[0]["outcome"] == "ok" and call[0]["duration_ms"] >= 0
    assert "Jane" not in caplog.text and "chest pain" not in caplog.text


def test_consult_metrics_have_no_clinical_text(caplog):
    class Span:
        def __init__(self):
            self.attrs = {}

        def set_attribute(self, k, v):
            self.attrs[k] = v

    span = Span()
    result = {"ok": True, "provider": "openai", "model": "gpt-6-sol", "note": "Patient Jane has sepsis",
              "usage": {"input_tokens": 10, "output_tokens": 5, "rounds": 2}, "structured": {"urgency": "emergency", "source": "model"},
              "citation_check": {"counts": {"verified": 2, "not_found": 1}}}
    with caplog.at_level(logging.INFO, "medical_doctor.metrics"):
        observability.record_consult(span, result, 1.234)
    f = caplog.records[-1].fields
    assert f["urgency"] == "emergency" and f["citations_failed"] == 1 and span.attrs["gen_ai.usage.input_tokens"] == 10
    assert "Jane" not in json.dumps(f) and "sepsis" not in json.dumps(span.attrs)
