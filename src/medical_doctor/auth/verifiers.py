"""Bearer-token verifiers: static API keys and JWTs from an external authorization server."""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import anyio
import jwt
from mcp.server.auth.provider import AccessToken

from ..config import Settings
from .upstream import LoginError, check_access

log = logging.getLogger("medical_doctor.auth")


def api_key_access_token(settings: Settings, token: str) -> AccessToken | None:
    """Match a bearer token against configured API keys (constant-time comparison)."""
    candidate = hashlib.sha256(token.encode()).digest()
    for key, label in settings.api_keys.items():
        if hmac.compare_digest(candidate, hashlib.sha256(key.encode()).digest()):
            return AccessToken(
                token=token,
                client_id=f"api-key:{label}",
                scopes=["mcp"],
                subject=f"api-key:{label}",
                resource=settings.resource_url,
                claims={"iss": settings.public_url, "name": label},
            )
    return None


def parse_scopes(claims: dict[str, Any]) -> list[str]:
    """OAuth `scope` and Entra `scp` are space-separated strings; some servers emit lists."""
    scopes: list[str] = []
    for claim in ("scope", "scp"):
        value = claims.get(claim)
        if isinstance(value, str):
            scopes += value.split()
        elif isinstance(value, list):
            scopes += [str(v) for v in value]
    return scopes


class ApiKeyVerifier:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def verify_token(self, token: str) -> AccessToken | None:
        return api_key_access_token(self.settings, token)


class JwtVerifier:
    """Validates JWT access tokens from an external OAuth server (Auth0, Okta, Entra ID, Keycloak,
    WorkOS, Descope, Stytch, ...) against its JWKS: signature, issuer, audience and expiry."""

    ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "ES256", "ES384", "EdDSA"]

    def __init__(self, settings: Settings, jwks_client: Any | None = None):
        self.settings = settings
        self.audience = settings.jwt_audience or settings.resource_url
        self._jwks = jwks_client
        self._jwks_url = settings.jwt_jwks_url

    def _client(self) -> Any:
        if self._jwks is None:
            url = self._jwks_url or self._discover_jwks_url()
            self._jwks = jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)
        return self._jwks

    def _discover_jwks_url(self) -> str:
        import json
        import urllib.request

        for path in ("/.well-known/openid-configuration", "/.well-known/oauth-authorization-server"):
            try:
                with urllib.request.urlopen(self.settings.jwt_issuer + path, timeout=10) as r:  # noqa: S310 (https issuer)
                    return json.load(r)["jwks_uri"]
            except Exception:  # try the next well-known document
                continue
        raise RuntimeError(f"Could not discover jwks_uri for issuer {self.settings.jwt_issuer}")

    def _decode(self, token: str) -> dict[str, Any]:
        key = self._client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=self.ALGORITHMS,
            audience=self.audience,
            options={"require": ["exp", "iss", "aud", "sub"]},
            leeway=30,
        )
        # Checked here rather than via jwt.decode(issuer=[...]): list issuers need PyJWT >= 2.10,
        # and issuers differ on a trailing slash ("https://x.auth0.com/" vs ".../realms/x").
        if str(claims["iss"]).rstrip("/") != self.settings.jwt_issuer.rstrip("/"):
            raise jwt.InvalidIssuerError("issuer mismatch")
        return claims

    async def verify_token(self, token: str) -> AccessToken | None:
        if key_token := api_key_access_token(self.settings, token):
            return key_token
        try:
            claims = await anyio.to_thread.run_sync(self._decode, token)
        except Exception as exc:
            log.info("rejected JWT: %s", type(exc).__name__)
            return None
        scopes = parse_scopes(claims)
        if self.settings.jwt_required_scope and self.settings.jwt_required_scope not in scopes:
            return None
        email = (claims.get("email") or "").lower()
        if self.settings.allowed_emails or self.settings.allowed_domains:
            verified = claims.get("email_verified") in (True, "true") or self.settings.jwt_trust_unverified_email
            try:
                check_access(self.settings, email, verified)
            except LoginError as exc:
                log.info("rejected JWT for %s: %s", claims.get("sub"), exc)
                return None
        return AccessToken(
            token=token,
            client_id=str(claims.get("azp") or claims.get("client_id") or "external"),
            scopes=scopes,
            expires_at=int(claims["exp"]),
            subject=str(claims["sub"]),
            resource=self.settings.resource_url,  # audience already verified above
            claims={k: claims[k] for k in ("iss", "email", "name") if k in claims},
        )
