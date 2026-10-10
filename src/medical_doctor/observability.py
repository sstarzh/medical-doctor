"""Logs, request IDs, tool timing and traces — without patient data.

* JSON logs (one object per line) when LOG_FORMAT=json or on Vercel; plain text otherwise.
* Every HTTP request gets a request ID (incoming X-Request-Id, else Vercel's id, else a new one) that
  is echoed in the response and stamped on every log line written while handling it.
* `ToolTimingMiddleware` logs each MCP request: method, tool name, duration, outcome. Never arguments
  or results — those carry PHI.
* Traces: the MCP SDK already opens an OpenTelemetry span per request; `consult_span` adds consult
  attributes (provider, model, urgency, tokens, rounds). With `opentelemetry-sdk` and
  `opentelemetry-exporter-otlp` installed and OTEL_EXPORTER_OTLP_ENDPOINT set, `configure()` exports
  them (Honeycomb, Grafana, Datadog, Langfuse...). Without them the API is a no-op.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("md_request_id", default=None)
tracer = trace.get_tracer("medical_doctor")
log = logging.getLogger("medical_doctor.metrics")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if rid := request_id.get():
            out["request_id"] = rid
        span = trace.get_current_span().get_span_context()
        if span.is_valid:
            out["trace_id"] = format(span.trace_id, "032x")
        out.update(getattr(record, "fields", None) or {})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure(level: str | None = None) -> None:
    """Idempotent logging/tracing setup for every entry point (CLI, HTTP, Vercel)."""
    global _configured
    if _configured:
        return
    _configured = True
    handler = logging.StreamHandler()
    if os.environ.get("LOG_FORMAT", "json" if os.environ.get("VERCEL") else "text").lower() == "json":
        handler.setFormatter(JsonFormatter())
        logging.Formatter.converter = time.gmtime
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level or os.environ.get("LOG_LEVEL", "INFO"))
    _configure_tracing()


def _configure_tracing() -> None:
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") and not os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        return
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logging.getLogger(__name__).warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT is set but opentelemetry-sdk/exporter are not installed: "
            "pip install 'medical-doctor-mcp[otel]'"
        )
        return
    provider = TracerProvider(resource=Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", "medical-doctor-mcp")}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)


def metric(event: str, **fields: Any) -> None:
    """A structured event line (fields must never contain clinical text)."""
    log.info(event, extra={"fields": {"event": event, **fields}})


class RequestContext:
    """ASGI middleware: request ID, access log (method, path, status, duration — no query/body)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        incoming = (headers.get(b"x-request-id") or headers.get(b"x-vercel-id") or b"").decode("latin-1")
        rid = incoming if _SAFE_ID.match(incoming) else secrets.token_hex(8)
        token = request_id.set(rid)
        start = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {**message, "headers": [*message.get("headers", []), (b"x-request-id", rid.encode())]}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if scope["path"] != "/healthz":
                metric("http_request", method=scope["method"], path=scope["path"], status=status,
                       duration_ms=round((time.perf_counter() - start) * 1000, 1))
            request_id.reset(token)


class ToolTimingMiddleware:
    """MCP server middleware: one log line per request with timing and outcome (no arguments/results)."""

    async def __call__(self, ctx: Any, call_next: Any) -> Any:
        if ctx.request_id is None:  # notifications
            return await call_next(ctx)
        name = (ctx.params or {}).get("name") if ctx.method in ("tools/call", "prompts/get") else None
        start = time.perf_counter()
        outcome = "ok"
        try:
            result = await call_next(ctx)
            if getattr(result, "is_error", False) or (isinstance(result, dict) and result.get("isError")):
                outcome = "tool_error"
            return result
        except Exception as exc:
            outcome = type(exc).__name__
            raise
        finally:
            fields: dict[str, Any] = {"method": ctx.method, "outcome": outcome,
                                      "duration_ms": round((time.perf_counter() - start) * 1000, 1)}
            if isinstance(name, str):
                fields["tool"] = name[:80]
            metric("mcp_request", **fields)


@contextmanager
def consult_span(provider: str, model: str, **attrs: Any) -> Iterator[Any]:
    with tracer.start_as_current_span("consult", attributes={"gen_ai.system": provider, "gen_ai.request.model": model, **attrs}) as span:
        yield span


def record_consult(span: Any, result: dict[str, Any], duration: float) -> None:
    usage = result.get("usage") or {}
    structured = result.get("structured") or {}
    counts = (result.get("citation_check") or {}).get("counts") or {}
    fields = {
        "provider": result.get("provider"), "model": result.get("model"), "ok": bool(result.get("ok")),
        "urgency": structured.get("urgency"), "structured_source": structured.get("source"),
        "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens"), "rounds": usage.get("rounds"),
        "tool_calls": len(result.get("tool_calls") or []), "panel": len(result.get("panel") or []) or None,
        "citations_verified": counts.get("verified"), "citations_failed": (counts.get("mismatch", 0) + counts.get("not_found", 0)) or None,
        "duration_s": round(duration, 2),
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    for k, v in fields.items():
        span.set_attribute(f"medical_doctor.{k}", v)
    if usage.get("input_tokens") is not None:
        span.set_attribute("gen_ai.usage.input_tokens", usage["input_tokens"])
        span.set_attribute("gen_ai.usage.output_tokens", usage.get("output_tokens") or 0)
    metric("consult", **fields)
