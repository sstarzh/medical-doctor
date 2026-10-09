"""OAuth 2.1 authorization server for MCP clients, brokering sign-in to Google/GitHub/Microsoft/OIDC.

MCP clients (ChatGPT, Claude, Cursor, VS Code, Gemini CLI, ...) discover this server through
Protected Resource Metadata, register with Dynamic Client Registration, and run authorization
code + PKCE. We never hand out upstream (Google, ...) tokens: the user signs in upstream, we
check the allowlist, then issue our own short-lived, resource-bound tokens.

    MCP client ──/authorize──▶ consent page ──▶ Google ──/oauth/callback──▶ our code ──▶ client
                                                                              │
                                             client ──/token──▶ access + refresh token

Consent page: per MCP security best practices for proxy servers, the user always sees which
client (name + redirect host) is asking before being sent upstream, which blocks confused-deputy
attacks via maliciously registered clients.
"""

from __future__ import annotations

import html
import logging
import secrets
import time
from typing import Any
from urllib.parse import urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from ..config import Settings
from .store import Store, digest
from .upstream import HttpClientFactory, LoginError, UpstreamClient, check_access, default_http, pkce_pair
from .verifiers import api_key_access_token

log = logging.getLogger("medical_doctor.auth")

SCOPE = "mcp"
PENDING_TTL = 600
CODE_TTL = 300


class GrantAccessToken(AccessToken):
    grant_id: str | None = None


class GrantRefreshToken(RefreshToken):
    grant_id: str | None = None


class GrantAuthorizationCode(AuthorizationCode):
    email: str = ""
    name: str = ""


class BrokerOAuthProvider:
    """Implements mcp.server.auth.provider.OAuthAuthorizationServerProvider."""

    def __init__(self, settings: Settings, store: Store, http: HttpClientFactory = default_http):
        self.settings = settings
        self.store = store
        self.upstreams = {idp.name: UpstreamClient(idp, http) for idp in settings.idps}

    # --- client registration (RFC 7591) ------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = self.store.get_client(client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.store.put_client(client_info.client_id, client_info.model_dump(mode="json", exclude_none=True))
        log.info("registered OAuth client %s (%s)", client_info.client_id, client_info.client_name)

    # --- authorization -----------------------------------------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource and params.resource.rstrip("/") != self.settings.resource_url:
            raise AuthorizeError("invalid_target", f"This server only issues tokens for {self.settings.resource_url}")
        txn = secrets.token_urlsafe(32)
        self.store.put(
            "pending",
            txn,
            {
                "client_id": client.client_id,
                "client_name": client.client_name or "",
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "code_challenge": params.code_challenge,
                "state": params.state,
                "scopes": params.scopes or [SCOPE],
            },
            PENDING_TTL,
        )
        return f"{self.settings.public_url}/login?txn={txn}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> GrantAuthorizationCode | None:
        data = self.store.get("code", digest(authorization_code))
        if not data or data["client_id"] != client.client_id:
            return None
        return GrantAuthorizationCode(code=authorization_code, **data)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: GrantAuthorizationCode
    ) -> OAuthToken:
        if not self.store.pop("code", digest(authorization_code.code)):  # single use
            raise TokenError("invalid_grant", "authorization code already used or expired")
        return self._issue(
            client.client_id,
            authorization_code.scopes,
            authorization_code.subject or "",
            {"email": authorization_code.email, "name": authorization_code.name},
            grant_id=secrets.token_urlsafe(16),
        )

    # --- refresh -----------------------------------------------------------

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> GrantRefreshToken | None:
        data = self.store.get("refresh", digest(refresh_token))
        if not data or data["client_id"] != client.client_id:
            return None
        return GrantRefreshToken(token=refresh_token, **{k: v for k, v in data.items() if k != "claims"})

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: GrantRefreshToken, scopes: list[str]
    ) -> OAuthToken:
        data = self.store.pop("refresh", digest(refresh_token.token))  # rotation: old token dies
        if not data:
            raise TokenError("invalid_grant", "refresh token already used or expired")
        return self._issue(
            client.client_id, scopes or data["scopes"], data["subject"], data.get("claims", {}), grant_id=data["grant_id"]
        )

    # --- tokens --------------------------------------------------------------

    def _issue(self, client_id: str, scopes: list[str], subject: str, claims: dict[str, Any], grant_id: str) -> OAuthToken:
        s = self.settings
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        now = int(time.time())
        common = {"client_id": client_id, "scopes": scopes, "subject": subject, "resource": s.resource_url, "grant_id": grant_id}
        self.store.put(
            "access", digest(access), {**common, "expires_at": now + s.access_token_ttl, "claims": claims}, s.access_token_ttl, grant_id
        )
        self.store.put(
            "refresh", digest(refresh), {**common, "expires_at": now + s.refresh_token_ttl, "claims": claims}, s.refresh_token_ttl, grant_id
        )
        return OAuthToken(access_token=access, expires_in=s.access_token_ttl, scope=" ".join(scopes), refresh_token=refresh)

    async def load_access_token(self, token: str) -> AccessToken | None:
        if key_token := api_key_access_token(self.settings, token):
            return key_token
        data = self.store.get("access", digest(token))
        if not data:
            return None
        claims = {**data.pop("claims", {}), "iss": self.settings.public_url}
        return GrantAccessToken(token=token, claims=claims, **data)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        grant_id = getattr(token, "grant_id", None)
        if grant_id:
            self.store.revoke_grant(grant_id)


# --- Login / consent pages and the upstream callback ------------------------

_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'; form-action 'none'",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>
:root{{color-scheme:light dark;--bg:#f6f7f9;--card:#fff;--fg:#1b1f24;--muted:#5b6470;--line:#d9dde3;--accent:#0b6bcb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1216;--card:#171b21;--fg:#e8ebef;--muted:#9aa4b1;--line:#2a313a;--accent:#5aa9ff}}}}
body{{margin:0;font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--fg)}}
main{{max-width:440px;margin:8vh auto;padding:0 16px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:28px}}
h1{{font-size:1.25rem;margin:0 0 12px}} p{{margin:0 0 14px}} .muted{{color:var(--muted);font-size:.9rem}}
a.btn{{display:block;text-align:center;padding:12px;margin:10px 0;border:1px solid var(--line);border-radius:8px;
color:var(--fg);text-decoration:none;font-weight:600}} a.btn:hover{{border-color:var(--accent)}}
code{{font-size:.9em}}
</style></head><body><main><div class="card">{body}</div>
<p class="muted" style="margin-top:14px">Clinical decision support only — not a substitute for a licensed clinician.</p>
</main></body></html>"""


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(title=html.escape(title), body=body), status_code=status, headers=_SECURITY_HEADERS)


def _error(message: str, status: int = 400) -> HTMLResponse:
    return _page("Sign-in failed", f"<h1>Sign-in failed</h1><p>{html.escape(message)}</p>", status)


def register_login_routes(mcp: Any, provider: BrokerOAuthProvider) -> None:
    settings, store = provider.settings, provider.store

    def callback_url(name: str) -> str:
        return f"{settings.public_url}/oauth/callback/{name}"

    @mcp.custom_route("/login", methods=["GET"])
    async def login(request: Request) -> Response:
        txn = request.query_params.get("txn", "")
        pending = store.get("pending", txn)
        if not pending:
            return _error("This sign-in link has expired. Start again from your AI app.", 404)
        client = html.escape(pending["client_name"] or "An MCP client")
        host = html.escape(urlparse(pending["redirect_uri"]).netloc or pending["redirect_uri"])
        buttons = "".join(
            f'<a class="btn" href="/login/{name}?txn={txn}">Continue with {html.escape(up.idp.display_name)}</a>'
            for name, up in provider.upstreams.items()
        )
        body = (
            "<h1>Sign in to Medical Doctor</h1>"
            f"<p><strong>{client}</strong> wants to use the clinical consult service on your behalf.</p>"
            f"<p class='muted'>After sign-in you will be returned to <code>{host}</code>. "
            "Only continue if you started this from that app.</p>"
            f"{buttons}"
        )
        return _page("Sign in — Medical Doctor", body)

    @mcp.custom_route("/login/{provider}", methods=["GET"])
    async def login_with(request: Request) -> Response:
        name = request.path_params["provider"]
        txn = request.query_params.get("txn", "")
        upstream = provider.upstreams.get(name)
        if not upstream or not store.get("pending", txn):
            return _error("This sign-in link has expired. Start again from your AI app.", 404)
        state = secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()
        store.put("upstream", state, {"txn": txn, "provider": name, "verifier": verifier}, PENDING_TTL)
        url = await upstream.authorize_url(callback_url(name), state, challenge)
        return RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/oauth/callback/{provider}", methods=["GET"])
    async def callback(request: Request) -> Response:
        name = request.path_params["provider"]
        q = request.query_params
        ctx = store.pop("upstream", q.get("state", ""))
        if not ctx or ctx["provider"] != name:
            return _error("Invalid or expired sign-in state. Start again from your AI app.")
        if q.get("error"):
            return _error(f"Sign-in was cancelled or refused ({q.get('error')}).")
        pending = store.pop("pending", ctx["txn"])
        if not pending:
            return _error("This sign-in link has expired. Start again from your AI app.")
        try:
            ident = await provider.upstreams[name].identity(q.get("code", ""), callback_url(name), ctx["verifier"])
            check_access(settings, ident.email, ident.email_verified)
        except LoginError as exc:
            log.warning("sign-in refused via %s: %s", name, exc)
            return _error(str(exc), 403)
        except Exception:
            log.exception("upstream sign-in failed via %s", name)
            return _error("The identity provider could not be reached. Please try again.", 502)

        code = secrets.token_urlsafe(32)
        store.put(
            "code",
            digest(code),
            {
                "scopes": pending["scopes"],
                "expires_at": time.time() + CODE_TTL,
                "client_id": pending["client_id"],
                "code_challenge": pending["code_challenge"],
                "redirect_uri": pending["redirect_uri"],
                "redirect_uri_provided_explicitly": pending["redirect_uri_provided_explicitly"],
                "resource": settings.resource_url,
                "subject": ident.subject,
                "email": ident.email,
                "name": ident.name,
            },
            CODE_TTL,
        )
        log.info("sign-in ok: %s via %s for client %s", ident.email or ident.subject, name, pending["client_id"])
        return RedirectResponse(
            construct_redirect_uri(pending["redirect_uri"], code=code, state=pending["state"]),
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )
