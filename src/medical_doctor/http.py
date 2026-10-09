"""Streamable-HTTP ASGI app with authentication, CORS and host protection."""

from __future__ import annotations

from typing import Any

from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp

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
) -> ASGIApp:
    mcp = build_server(settings, store=store, http=http)
    app = mcp.streamable_http_app(
        stateless_http=stateless,
        json_response=json_response,
        transport_security=transport_security(settings, host),
        host=host,
    )
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
