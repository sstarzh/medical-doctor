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
attacks via maliciously registered clients. Consent is enforced, not just displayed: the
"Continue with ..." buttons are POST forms that only succeed with a SameSite browser-binding
cookie set by the consent page, and the upstream callback checks the same cookie, so a link
straight to the upstream step (or a login CSRF) cannot complete a sign-in.
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
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from ..config import Settings
from ..web import SECURITY_HEADERS
from .store import AsyncStore, Store, StoreFull, digest
from .upstream import HttpClientFactory, LoginError, UpstreamClient, check_access, default_http, pkce_pair
from .verifiers import api_key_access_token

log = logging.getLogger("medical_doctor.auth")

SCOPE = "mcp"
PENDING_TTL = 600
TOKEN_CACHE_TTL = 30.0
TOKEN_CACHE_MAX = 10_000
CODE_TTL = 300
UNUSED_CLIENT_TTL = 24 * 3600  # registrations that never complete a sign-in expire after a day
BROWSER_COOKIE = "md_login"
# Live-record caps for unauthenticated endpoints (dynamic registration, /authorize).
STORE_CAPS = {"client": 10_000, "pending": 5_000, "upstream": 5_000}
MAX_REDIRECT_URIS = 5
MAX_CLIENT_NAME = 100
MAX_CLIENT_RECORD_BYTES = 8_192
AUTHORIZE_PER_CLIENT = (30, 600)  # pending sign-ins per client per 10 minutes
_BLOCKED_SCHEMES = {"javascript", "data", "file", "vbscript", "about", "blob"}
_LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}


def validate_client_metadata(info: OAuthClientInformationFull) -> None:
    """Reject registrations that could be abused: oversized records, phishing-friendly or unsafe
    redirect URIs (userinfo tricks like https://claude.ai@evil.example, plain http off-loopback,
    script schemes). Native-app private schemes (cursor://, vscode://) remain allowed (RFC 8252)."""
    uris = [str(u) for u in (info.redirect_uris or [])]
    if not uris or len(uris) > MAX_REDIRECT_URIS:
        raise RegistrationError("invalid_redirect_uri", f"Provide 1-{MAX_REDIRECT_URIS} redirect URIs")
    for uri in uris:
        parsed = urlparse(uri)
        scheme = parsed.scheme.lower()
        if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
            raise RegistrationError("invalid_redirect_uri", "Redirect URIs must not contain user info")
        if scheme in _BLOCKED_SCHEMES or not scheme:
            raise RegistrationError("invalid_redirect_uri", f"Scheme {scheme!r} is not allowed")
        if scheme == "http" and (parsed.hostname or "") not in _LOOPBACK:
            raise RegistrationError("invalid_redirect_uri", "http redirect URIs are only allowed for loopback")
        if parsed.fragment:
            raise RegistrationError("invalid_redirect_uri", "Redirect URIs must not contain a fragment")
    if info.client_name and len(info.client_name) > MAX_CLIENT_NAME:
        raise RegistrationError("invalid_client_metadata", f"client_name longer than {MAX_CLIENT_NAME} characters")
    if len(info.model_dump_json(exclude_none=True)) > MAX_CLIENT_RECORD_BYTES:
        raise RegistrationError("invalid_client_metadata", "Client metadata too large")


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
        self.astore = AsyncStore(store)  # all I/O off the event loop
        store.caps = {**STORE_CAPS, **store.caps}
        self.upstreams = {idp.name: UpstreamClient(idp, http) for idp in settings.idps}
        # MCP clients send many requests per session and each one validates its bearer token. A short
        # per-instance cache saves a database round trip per request. Revocation in this instance clears
        # it at once; other instances honour a revocation within TOKEN_CACHE_TTL seconds.
        self._token_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    # --- client registration (RFC 7591) ------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = await self.astore.get("client", client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        validate_client_metadata(client_info)
        try:
            await self.astore.put(
                "client",
                client_info.client_id,
                client_info.model_dump(mode="json", exclude_none=True),
                UNUSED_CLIENT_TTL,
                capped=True,
            )
        except StoreFull:
            raise RegistrationError("invalid_client_metadata", "Registration is temporarily unavailable") from None
        log.info("registered OAuth client %s (%r)", client_info.client_id, (client_info.client_name or "")[:MAX_CLIENT_NAME])

    # --- authorization -----------------------------------------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource and params.resource.rstrip("/") != self.settings.resource_url:
            raise AuthorizeError("invalid_target", f"This server only issues tokens for {self.settings.resource_url}")
        if not await self.astore.hit(f"authorize:{client.client_id}", *AUTHORIZE_PER_CLIENT):
            raise AuthorizeError("temporarily_unavailable", "Too many sign-in attempts for this app; try again shortly")
        txn = secrets.token_urlsafe(32)
        try:
            await self._put_pending(txn, client, params)
        except StoreFull:
            raise AuthorizeError("temporarily_unavailable", "Too many sign-ins in progress; try again shortly") from None
        return f"{self.settings.public_url}/login?txn={txn}"

    async def _put_pending(self, txn: str, client: OAuthClientInformationFull, params: AuthorizationParams) -> None:
        await self.astore.put(
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
                "expires_at": time.time() + PENDING_TTL,
            },
            PENDING_TTL,
            capped=True,
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> GrantAuthorizationCode | None:
        data = await self.astore.get("code", digest(authorization_code))
        if not data or data["client_id"] != client.client_id:
            return None
        return GrantAuthorizationCode(code=authorization_code, **data)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: GrantAuthorizationCode
    ) -> OAuthToken:
        if not await self.astore.pop("code", digest(authorization_code.code)):  # single use
            raise TokenError("invalid_grant", "authorization code already used or expired")
        return await self._issue(
            client.client_id,
            authorization_code.scopes,
            authorization_code.subject or "",
            {"email": authorization_code.email, "name": authorization_code.name},
            grant_id=secrets.token_urlsafe(16),
            auth_time=time.time(),
        )

    # --- refresh -----------------------------------------------------------

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> GrantRefreshToken | None:
        key = digest(refresh_token)
        data = await self.astore.get("refresh", key)
        if not data:
            # Reuse of a rotated-out refresh token means it leaked (OAuth 2.1 §4.3.1 / RFC 9700):
            # kill the whole session for both the attacker and the legitimate client.
            used = await self.astore.get("refresh_used", key)
            if used:
                log.warning("refresh token reuse detected; revoking grant for %s", used.get("subject"))
                self._token_cache.clear()
                await self.astore.revoke_grant(used["grant_id"])
            return None
        if data["client_id"] != client.client_id:
            return None
        return GrantRefreshToken(token=refresh_token, **{k: v for k, v in data.items() if k not in ("claims", "auth_time")})

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: GrantRefreshToken, scopes: list[str]
    ) -> OAuthToken:
        key = digest(refresh_token.token)
        data = await self.astore.pop("refresh", key)  # rotation: old token dies
        if not data:
            raise TokenError("invalid_grant", "refresh token already used or expired")
        await self.astore.put(
            "refresh_used",
            key,
            {"grant_id": data["grant_id"], "subject": data["subject"]},
            self.settings.refresh_token_ttl,
            data["grant_id"],
        )
        return await self._issue(
            client.client_id,
            scopes or data["scopes"],
            data["subject"],
            data.get("claims", {}),
            grant_id=data["grant_id"],
            auth_time=data.get("auth_time", time.time()),
        )

    # --- tokens --------------------------------------------------------------

    async def _issue(
        self, client_id: str, scopes: list[str], subject: str, claims: dict[str, Any], grant_id: str, auth_time: float
    ) -> OAuthToken:
        s = self.settings
        now = int(time.time())
        # Absolute session lifetime: rotation never extends a sign-in past max_session_lifetime.
        remaining = int(auth_time + s.max_session_lifetime - now)
        if remaining <= 0:
            self._token_cache.clear()
            await self.astore.revoke_grant(grant_id)
            raise TokenError("invalid_grant", "session expired; sign in again")
        access_ttl, refresh_ttl = min(s.access_token_ttl, remaining), min(s.refresh_token_ttl, remaining)
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        common = {
            "client_id": client_id,
            "scopes": scopes,
            "subject": subject,
            "resource": s.resource_url,
            "grant_id": grant_id,
            "claims": claims,
        }
        await self.astore.put("access", digest(access), {**common, "expires_at": now + access_ttl}, access_ttl, grant_id)
        await self.astore.put(
            "refresh", digest(refresh), {**common, "expires_at": now + refresh_ttl, "auth_time": auth_time}, refresh_ttl, grant_id
        )
        await self.astore.extend("client", client_id, refresh_ttl)  # active clients outlive the unused-registration TTL
        return OAuthToken(access_token=access, expires_in=access_ttl, scope=" ".join(scopes), refresh_token=refresh)

    async def load_access_token(self, token: str) -> AccessToken | None:
        if key_token := api_key_access_token(self.settings, token):
            return key_token
        key = digest(token)
        hit = self._token_cache.get(key)
        now = time.time()
        if hit and hit[0] > now:
            data = dict(hit[1])
        else:
            data = await self.astore.get("access", key)
            if not data:
                self._token_cache.pop(key, None)
                return None
            if len(self._token_cache) >= TOKEN_CACHE_MAX:
                self._token_cache.clear()
            expires = data.get("expires_at") or now + TOKEN_CACHE_TTL
            self._token_cache[key] = (min(now + TOKEN_CACHE_TTL, expires), dict(data))
        claims = {**data.pop("claims", {}), "iss": self.settings.public_url}
        return GrantAccessToken(token=token, claims=claims, **data)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        grant_id = getattr(token, "grant_id", None)
        if grant_id:
            self._token_cache.clear()
            await self.astore.revoke_grant(grant_id)


# --- Login / consent pages and the upstream callback ------------------------

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>
:root{{color-scheme:light dark;--bg:#f6f7f9;--card:#fff;--fg:#1b1f24;--muted:#5b6470;--line:#d9dde3;--accent:#0b6bcb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1216;--card:#171b21;--fg:#e8ebef;--muted:#9aa4b1;--line:#2a313a;--accent:#5aa9ff}}}}
body{{margin:0;font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--fg)}}
main{{max-width:440px;margin:8vh auto;padding:0 16px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:28px}}
h1{{font-size:1.25rem;margin:0 0 12px}} p{{margin:0 0 14px}} .muted{{color:var(--muted);font-size:.9rem}}
button.btn{{display:block;width:100%;text-align:center;padding:12px;margin:10px 0;border:1px solid var(--line);
border-radius:8px;background:transparent;color:var(--fg);font:inherit;font-weight:600;cursor:pointer}}
button.btn:hover{{border-color:var(--accent)}} form{{margin:0}}
code{{font-size:.9em}}
</style></head><body><main><div class="card">{body}</div>
<p class="muted" style="margin-top:14px">Clinical decision support only — not a substitute for a licensed clinician.</p>
</main></body></html>"""


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(title=html.escape(title), body=body), status_code=status, headers=SECURITY_HEADERS)


def _error(message: str, status: int = 400) -> HTMLResponse:
    return _page("Sign-in failed", f"<h1>Sign-in failed</h1><p>{html.escape(message)}</p>", status)


def register_login_routes(mcp: Any, provider: BrokerOAuthProvider) -> None:
    settings, astore = provider.settings, provider.astore
    secure_cookie = settings.public_url.startswith("https://")

    def callback_url(name: str) -> str:
        return f"{settings.public_url}/oauth/callback/{name}"

    def browser_id(request: Request) -> str | None:
        value = request.cookies.get(BROWSER_COOKIE)
        return digest(value) if value else None

    def expired() -> HTMLResponse:
        return _error("This sign-in link has expired. Start again from your AI app.", 404)

    @mcp.custom_route("/login", methods=["GET"])
    async def login(request: Request) -> Response:
        """Consent page. Binds the pending sign-in to this browser via a SameSite cookie."""
        txn = request.query_params.get("txn", "")
        pending = await astore.get("pending", txn)
        if not pending:
            return expired()
        cookie = request.cookies.get(BROWSER_COOKIE) or secrets.token_urlsafe(32)
        pending["browser"] = digest(cookie)
        # Keep the original expiry: revisiting the consent page must not extend a pending sign-in.
        await astore.put("pending", txn, pending, max(1.0, pending.get("expires_at", time.time() + PENDING_TTL) - time.time()))
        client = html.escape(pending["client_name"] or "An MCP client")
        parsed_redirect = urlparse(pending["redirect_uri"])
        host = html.escape(parsed_redirect.hostname or parsed_redirect.scheme + ":")
        buttons = "".join(
            f'<form method="post" action="/login/{name}"><input type="hidden" name="txn" value="{html.escape(txn)}">'
            f'<button class="btn" type="submit">Continue with {html.escape(up.idp.display_name)}</button></form>'
            for name, up in provider.upstreams.items()
        )
        body = (
            "<h1>Sign in to Medical Doctor</h1>"
            f"<p><strong>{client}</strong> wants to use the clinical consult service on your behalf.</p>"
            f"<p class='muted'>After sign-in you will be returned to <code>{host}</code>. "
            "Only continue if you started this from that app.</p>"
            f"{buttons}"
        )
        response = _page("Sign in — Medical Doctor", body)
        response.set_cookie(
            BROWSER_COOKIE, cookie, max_age=PENDING_TTL, httponly=True, secure=secure_cookie, samesite="lax", path="/"
        )
        return response

    @mcp.custom_route("/login/{provider}", methods=["POST"])
    async def login_with(request: Request) -> Response:
        """The user's consent click. Only works from the consent page in the same browser:
        it is a POST (no link can trigger it) and needs the SameSite=Lax cookie that the
        consent page set, which browsers do not send on cross-site POSTs."""
        name = request.path_params["provider"]
        form = await request.form()
        txn = str(form.get("txn", ""))
        upstream = provider.upstreams.get(name)
        pending = await astore.get("pending", txn)
        if not upstream or not pending:
            return expired()
        browser = browser_id(request)
        if not browser or pending.get("browser") != browser:
            return _error("Please start the sign-in from the consent page in this browser.", 403)
        state = secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()
        try:
            await astore.put("upstream", state, {"txn": txn, "provider": name, "verifier": verifier, "browser": browser}, PENDING_TTL, capped=True)
            url = await upstream.authorize_url(callback_url(name), state, challenge)
        except StoreFull:
            return _error("Too many sign-ins in progress. Please try again shortly.", 503)
        except Exception:
            log.exception("could not start sign-in via %s", name)
            return _error("The identity provider could not be reached. Please try again.", 502)
        return RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/oauth/callback/{provider}", methods=["GET"])
    async def callback(request: Request) -> Response:
        name = request.path_params["provider"]
        q = request.query_params
        ctx = await astore.pop("upstream", q.get("state", ""))
        if not ctx or ctx["provider"] != name:
            return _error("Invalid or expired sign-in state. Start again from your AI app.")
        if ctx.get("browser") != browser_id(request):
            # Callback landed in a different browser than the one that consented (login CSRF).
            return _error("Please finish the sign-in in the same browser you started it in.", 403)
        if q.get("error"):
            return _error(f"Sign-in was cancelled or refused ({q.get('error')}).")
        pending = await astore.pop("pending", ctx["txn"])
        if not pending:
            return expired()
        try:
            ident = await provider.upstreams[name].identity(q.get("code", ""), callback_url(name), ctx["verifier"])
            check_access(settings, ident.email, ident.email_verified, hosted_domain=ident.hosted_domain, provider=name)
        except LoginError as exc:
            log.warning("sign-in refused via %s: %s", name, exc)
            return _error(str(exc), 403)
        except Exception:
            log.exception("upstream sign-in failed via %s", name)
            return _error("The identity provider could not be reached. Please try again.", 502)

        code = secrets.token_urlsafe(32)
        await astore.put(
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
        log.info("sign-in ok: %s via %s for client %s", ident.subject, name, pending["client_id"])
        response = RedirectResponse(
            construct_redirect_uri(pending["redirect_uri"], code=code, state=pending["state"]),
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )
        return response
