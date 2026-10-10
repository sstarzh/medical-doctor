"""Streamable-HTTP ASGI app with authentication, CORS and host protection."""

from __future__ import annotations

from typing import Any

from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings
from .server import build_server

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def transport_security(settings: Settings, host: str) -> TransportSecuritySettings | None:
    """DNS-rebinding protection: needed for unauthenticated servers reachable from a browser."""
    if host in LOCAL_HOSTS and not settings.public_url:
        return None  # SDK default: localhost-only host/origin checks
    if settings.auth_mode != "none":
        # Every request must carry a valid bearer token, which a rebinding page cannot obtain.
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts = [h for h in (settings.public_host, *settings.allowed_hosts) if h]
    if not hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    origins = [o for o in settings.cors_origins if o != "*"] + ([settings.public_url] if settings.public_url else [])
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[*hosts, *(f"{h}:*" for h in hosts if ":" not in h)],
        allowed_origins=origins,
    )


def create_app(
    settings: Settings,
    *,
    host: str = "127.0.0.1",
    stateless: bool = False,
    json_response: bool = False,
    store: Any = None,
    http: Any = None,
    records: Any = None,
) -> ASGIApp:
    if store is None and (settings.auth_mode == "oauth" or settings.consult_limit_per_hour > 0):
        from .auth.store import open_store

        store = open_store(settings)
    # A loopback-only server without auth is a single local user (e.g. Claude Desktop via HTTP).
    local_mode = settings.auth_mode == "none" and host in LOCAL_HOSTS
    mcp = build_server(settings, store=store, http=http, local_mode=local_mode, records=records)
    app = mcp.streamable_http_app(
        stateless_http=stateless,
        json_response=json_response,
        transport_security=transport_security(settings, host),
        host=host,
    )
    if settings.auth_mode == "oauth" and store is not None:
        app = IpRateLimit(app, store, trust_vercel=settings.on_vercel)
    # Browser-based MCP clients (MCP Inspector, web IDEs) need CORS; bearer tokens, not cookies,
    # carry auth, so a permissive origin list does not expose credentials.
    return CORSMiddleware(
        app,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "Mcp-Session-Id", "Mcp-Protocol-Version", "Last-Event-ID"],
        expose_headers=["Mcp-Session-Id", "WWW-Authenticate"],
        max_age=600,
    )


# (method, path prefix) -> (requests, window seconds) per client IP, for unauthenticated endpoints.
IP_LIMITS: dict[tuple[str, str], tuple[int, float]] = {
    ("POST", "/register"): (10, 3600),
    ("GET", "/authorize"): (60, 600),
    ("POST", "/authorize"): (60, 600),
    ("POST", "/token"): (120, 600),
    ("GET", "/login"): (60, 600),
    ("POST", "/login/"): (30, 600),
    ("GET", "/oauth/callback/"): (60, 600),
}


class IpRateLimit:
    """Per-IP limits on the sign-in endpoints, so one client cannot exhaust registration or
    pending-sign-in capacity for everyone (counters live in the shared store)."""

    def __init__(self, app: ASGIApp, store: object, *, trust_vercel: bool = False):
        self.app = app
        self.store = store
        self.trust_vercel = trust_vercel

    def _ip(self, scope: Scope) -> str:
        if self.trust_vercel:
            for name, value in scope.get("headers", []):
                if name == b"x-vercel-forwarded-for":  # set by Vercel's edge, not spoofable by clients
                    return value.decode().split(",")[0].strip()
        client = scope.get("client")
        return client[0] if client else "unknown"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            method, path = scope["method"], scope["path"]
            for (m, prefix), (limit, window) in IP_LIMITS.items():
                if method == m and (path == prefix or (prefix.endswith("/") and path.startswith(prefix))):
                    import anyio

                    ok = await anyio.to_thread.run_sync(
                        self.store.hit, f"ip:{m}:{prefix}:{self._ip(scope)}", limit, window  # type: ignore[attr-defined]
                    )
                    if not ok:
                        response = JSONResponse(
                            {"error": "rate_limited", "error_description": "Too many requests; try again later."},
                            status_code=429,
                            headers={"Retry-After": str(int(window))},
                        )
                        await response(scope, receive, send)
                        return
                    break
        await self.app(scope, receive, send)
