"""End-to-end OAuth tests: the flow ChatGPT / Claude / Cursor run against this server."""

import base64
import hashlib
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import httpx2 as httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from medical_doctor.auth.store import Store
from medical_doctor.auth.verifiers import JwtVerifier
from medical_doctor.config import Settings, UpstreamIdP, validate
from medical_doctor.http import create_app

PUBLIC = "https://doc.example.com"
RESOURCE = f"{PUBLIC}/mcp"
REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
API_KEY = "mdk_" + "k" * 40
GOOGLE = UpstreamIdP(
    "google",
    "Google",
    "gid",
    "gsecret",
    authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
)
SETTINGS = Settings(
    public_url=PUBLIC,
    auth_mode="oauth",
    idps=(GOOGLE,),
    allowed_domains=("example.com",),
    api_keys={API_KEY: "ci-bot"},
)
MCP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}


def fake_google(email: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            body = parse_qs(request.content.decode())
            assert body["code_verifier"][0] and body["client_secret"] == ["gsecret"]
            return httpx.Response(200, json={"access_token": "upstream-token", "token_type": "Bearer"})
        if request.url.host == "openidconnect.googleapis.com":
            assert request.headers["authorization"] == "Bearer upstream-token"
            return httpx.Response(200, json={"sub": "1234", "email": email, "email_verified": True, "name": "Dr Test"})
        return httpx.Response(404)

    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


@asynccontextmanager
async def client_for(settings=SETTINGS, email="doc@example.com"):
    app = create_app(settings, host="0.0.0.0", stateless=True, json_response=True, store=Store(), http=fake_google(email))
    inner = app.app  # unwrap CORS to run the MCP session manager lifespan
    async with inner.router.lifespan_context(inner):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC) as c:
            yield c


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def sign_in(c: httpx.AsyncClient):
    """Run the full browser + client flow; returns (client_id, final redirect response, verifier)."""
    reg = await c.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT],
            "client_name": "ChatGPT",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    assert reg.status_code == 201, reg.text
    client_id = reg.json()["client_id"]
    verifier, challenge = pkce()
    r = await c.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "client-state",
            "scope": "mcp",
            "resource": RESOURCE,
        },
    )
    assert r.status_code == 302, r.text
    login = urlparse(r.headers["location"])
    assert login.path == "/login"
    page = await c.get(f"/login?{login.query}")
    assert page.status_code == 200
    assert "ChatGPT" in page.text and "Continue with Google" in page.text and "chatgpt.com" in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    up = await c.get(f"/login/google?{login.query}")
    assert up.status_code == 302
    google = urlparse(up.headers["location"])
    gq = parse_qs(google.query)
    assert google.netloc == "accounts.google.com" and gq["code_challenge_method"] == ["S256"]
    assert gq["redirect_uri"] == [f"{PUBLIC}/oauth/callback/google"]
    cb = await c.get("/oauth/callback/google", params={"code": "upstream-code", "state": gq["state"][0]})
    return client_id, cb, verifier


async def get_tokens(c):
    client_id, cb, verifier = await sign_in(c)
    assert cb.status_code == 302, cb.text
    back = urlparse(cb.headers["location"])
    q = parse_qs(back.query)
    assert f"{back.scheme}://{back.netloc}{back.path}" == REDIRECT and q["state"] == ["client-state"]
    form = {
        "grant_type": "authorization_code",
        "code": q["code"][0],
        "redirect_uri": REDIRECT,
        "client_id": client_id,
        "code_verifier": verifier,
        "resource": RESOURCE,
    }
    tok = await c.post("/token", data=form)
    assert tok.status_code == 200, tok.text
    return client_id, form, tok.json()


async def call(c, method, params=None, token=None):
    headers = dict(MCP_HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return await c.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


INIT = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}


async def test_discovery_metadata():
    async with client_for() as c:
        prm = (await c.get("/.well-known/oauth-protected-resource/mcp")).json()
        assert prm["resource"] == RESOURCE and prm["authorization_servers"][0].rstrip("/") == PUBLIC
        asm = (await c.get("/.well-known/oauth-authorization-server")).json()
        assert asm["registration_endpoint"] == f"{PUBLIC}/register"
        assert asm["code_challenge_methods_supported"] == ["S256"]
        assert (await c.get("/healthz")).json()["status"] == "ok"


async def test_unauthenticated_mcp_gets_401_with_resource_metadata():
    async with client_for() as c:
        r = await call(c, "initialize", INIT)
        assert r.status_code == 401
        assert "resource_metadata" in r.headers["www-authenticate"]


async def test_full_oauth_flow_then_tool_call():
    async with client_for() as c:
        _, _, tokens = await get_tokens(c)
        assert tokens["token_type"] == "Bearer" and tokens["refresh_token"]
        r = await call(c, "initialize", INIT, tokens["access_token"])
        assert r.status_code == 200, r.text
        r = await call(c, "tools/call", {"name": "whoami", "arguments": {}}, tokens["access_token"])
        assert r.status_code == 200, r.text
        who = r.json()["result"]["structuredContent"]
        assert who["email"] == "doc@example.com" and who["subject"] == "google:1234"


async def test_authorization_code_is_single_use():
    async with client_for() as c:
        _, form, _ = await get_tokens(c)
        again = await c.post("/token", data=form)
        assert again.status_code == 400 and again.json()["error"] == "invalid_grant"


async def test_refresh_rotation_and_revocation():
    async with client_for() as c:
        client_id, _, tokens = await get_tokens(c)
        refresh = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id}
        new = await c.post("/token", data=refresh)
        assert new.status_code == 200, new.text
        assert (await c.post("/token", data=refresh)).status_code == 400  # old refresh token rotated out
        access = new.json()["access_token"]
        assert (await call(c, "initialize", INIT, access)).status_code == 200
        # SDK quirk: RevocationRequest requires a client_secret field even for public clients.
        rv = await c.post("/revoke", data={"token": access, "client_id": client_id, "client_secret": ""})
        assert rv.status_code == 200, rv.text
        assert (await call(c, "initialize", INIT, access)).status_code == 401


async def test_user_outside_allowlist_is_refused():
    async with client_for(email="stranger@gmail.com") as c:
        _, cb, _ = await sign_in(c)
        assert cb.status_code == 403 and "not authorized" in cb.text


async def test_wrong_resource_is_rejected():
    async with client_for() as c:
        reg = (await c.post("/register", json={"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none"})).json()
        _, challenge = pkce()
        r = await c.get(
            "/authorize",
            params={"response_type": "code", "client_id": reg["client_id"], "redirect_uri": REDIRECT,
                    "code_challenge": challenge, "code_challenge_method": "S256", "resource": "https://evil.example/mcp"},
        )
        assert "invalid_target" in r.headers.get("location", r.text)


async def test_api_key_works_alongside_oauth():
    async with client_for() as c:
        assert (await call(c, "initialize", INIT, API_KEY)).status_code == 200
        assert (await call(c, "initialize", INIT, "mdk_wrong_key_wrong_key_wrong_key")).status_code == 401


async def test_apikey_mode():
    s = Settings(public_url=PUBLIC, auth_mode="apikey", api_keys={API_KEY: "bot"})
    async with client_for(s) as c:
        assert (await call(c, "initialize", INIT)).status_code == 401
        assert (await call(c, "initialize", INIT, API_KEY)).status_code == 200


async def test_cors_preflight():
    async with client_for() as c:
        r = await c.options("/mcp", headers={"Origin": "http://localhost:6274", "Access-Control-Request-Method": "POST",
                                             "Access-Control-Request-Headers": "authorization,content-type,mcp-session-id"})
        assert r.status_code == 200 and "mcp-session-id" in r.headers["access-control-allow-headers"].lower()


# --- configuration safety ---------------------------------------------------


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"allowed_domains": ()}, "open sign-up"),
        ({"public_url": ""}, "PUBLIC_URL is required"),
        ({"public_url": "http://doc.example.com"}, "https"),
        ({"idps": ()}, "sign-in provider"),
    ],
)
def test_unsafe_config_refused(overrides, message):
    with pytest.raises(ValueError, match=message):
        validate(replace(SETTINGS, **overrides))


def test_microsoft_multitenant_refused(monkeypatch):
    from medical_doctor.config import load_settings

    monkeypatch.setenv("MEDICAL_DOCTOR_AUTH", "oauth")
    monkeypatch.setenv("MEDICAL_DOCTOR_PUBLIC_URL", PUBLIC)
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "x")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "y")
    monkeypatch.setenv("MICROSOFT_TENANT_ID", "common")
    with pytest.raises(ValueError, match="nOAuth|specific tenant"):
        load_settings()


# --- external JWT (Auth0 / Okta / Entra / Keycloak ...) ------------------------

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _FakeJwks:
    def get_signing_key_from_jwt(self, token):
        return type("K", (), {"key": _KEY.public_key()})()


def _jwt(**claims):
    base = {"iss": "https://idp.example.com/", "aud": RESOURCE, "sub": "u1", "exp": int(time.time()) + 300,
            "scope": "mcp", "email": "doc@example.com", "email_verified": True}
    base.update(claims)
    return jwt.encode(base, _KEY, algorithm="RS256")


JWT_SETTINGS = Settings(public_url=PUBLIC, auth_mode="jwt", jwt_issuer="https://idp.example.com", allowed_domains=("example.com",))


async def test_jwt_verifier_accepts_valid_token():
    v = JwtVerifier(JWT_SETTINGS, jwks_client=_FakeJwks())
    tok = await v.verify_token(_jwt())
    assert tok and tok.subject == "u1" and tok.claims["email"] == "doc@example.com"


@pytest.mark.parametrize(
    "claims",
    [{"aud": "https://other.example/mcp"}, {"exp": int(time.time()) - 600}, {"iss": "https://evil.example"}, {"email": "x@gmail.com"}],
)
async def test_jwt_verifier_rejects(claims):
    v = JwtVerifier(JWT_SETTINGS, jwks_client=_FakeJwks())
    assert await v.verify_token(_jwt(**claims)) is None
