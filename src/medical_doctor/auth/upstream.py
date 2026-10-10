"""Sign-in with upstream identity providers (Google, GitHub, Microsoft, generic OIDC)."""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx2 as httpx

from ..config import Settings, UpstreamIdP

HttpClientFactory = Callable[[], httpx.AsyncClient]


def default_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(15.0), headers={"User-Agent": "medical-doctor-mcp"})


class LoginError(Exception):
    """Sign-in failed or the user is not allowed; message is safe to show to the user."""


@dataclass(frozen=True)
class Identity:
    provider: str
    subject: str  # stable, provider-scoped: "google:1234"
    email: str
    email_verified: bool
    name: str = ""
    hosted_domain: str | None = None  # Google Workspace domain ("hd" claim); None for consumer accounts


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class UpstreamClient:
    def __init__(self, idp: UpstreamIdP, http: HttpClientFactory = default_http):
        self.idp = idp
        self._http = http
        self._discovered: dict[str, str] | None = None

    async def _endpoints(self) -> tuple[str, str, str]:
        idp = self.idp
        if idp.authorize_url:
            return idp.authorize_url, idp.token_url, idp.userinfo_url
        if self._discovered is None:
            async with self._http() as c:
                r = await c.get(f"{idp.issuer}/.well-known/openid-configuration")
                r.raise_for_status()
                self._discovered = r.json()
        d = self._discovered
        return d["authorization_endpoint"], d["token_endpoint"], d["userinfo_endpoint"]

    async def authorize_url(self, redirect_uri: str, state: str, code_challenge: str) -> str:
        authorize, _, _ = await self._endpoints()
        params = {
            "client_id": self.idp.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": self.idp.scope,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if self.idp.name == "google":
            params["prompt"] = "select_account"
        return f"{authorize}?{urlencode(params)}"

    async def identity(self, code: str, redirect_uri: str, code_verifier: str) -> Identity:
        _, token_url, userinfo_url = await self._endpoints()
        async with self._http() as c:
            r = await c.post(
                token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self.idp.client_id,
                    "client_secret": self.idp.client_secret,
                    "code_verifier": code_verifier,
                },
                headers={"Accept": "application/json"},
            )
            if r.status_code != 200:
                raise LoginError(f"{self.idp.display_name} rejected the sign-in (HTTP {r.status_code}).")
            access = r.json().get("access_token")
            if not access:
                raise LoginError(f"{self.idp.display_name} did not return an access token.")
            auth = {"Authorization": f"Bearer {access}", "Accept": "application/json"}
            if self.idp.name == "github":
                return await self._github_identity(c, auth)
            u = await c.get(userinfo_url, headers=auth)
            u.raise_for_status()
            info = u.json()
        verified = info.get("email_verified")
        verified = verified is True or str(verified).lower() == "true" or self.idp.trust_unverified_email
        return Identity(
            provider=self.idp.name,
            subject=f"{self.idp.name}:{info['sub']}",
            email=(info.get("email") or "").lower(),
            email_verified=bool(verified),
            name=info.get("name", ""),
            hosted_domain=(info.get("hd") or "").lower() or None,
        )

    async def _github_identity(self, c: httpx.AsyncClient, auth: dict[str, str]) -> Identity:
        u = await c.get("https://api.github.com/user", headers=auth)
        u.raise_for_status()
        user = u.json()
        emails = await c.get("https://api.github.com/user/emails", headers=auth)
        emails.raise_for_status()
        primary = next((e for e in emails.json() if e.get("primary") and e.get("verified")), None)
        return Identity(
            provider="github",
            subject=f"github:{user['id']}",
            email=(primary or {}).get("email", "").lower(),
            email_verified=primary is not None,
            name=user.get("name") or user.get("login", ""),
        )


def check_access(
    settings: Settings, email: str, email_verified: bool, *, hosted_domain: str | None = None, provider: str | None = None
) -> None:
    """Enforce the sign-in allowlist. Raises LoginError when the user may not use the service.

    For Google, a domain match also requires the Workspace `hd` claim to equal that domain:
    a consumer Google account created with a company address (e.g. by a former employee) has a
    verified email but no `hd`, and must not pass a domain allowlist.
    """
    if settings.allow_any_user and not (settings.allowed_emails or settings.allowed_domains):
        return
    if not email or not email_verified:
        raise LoginError("Your account has no verified email address, which this service requires.")
    if email in settings.allowed_emails:
        return
    domain = email.rsplit("@", 1)[-1]
    if domain in settings.allowed_domains:
        if provider == "google" and hosted_domain != domain:
            raise LoginError(f"Sign in with your {domain} Google Workspace account (not a personal Google account).")
        return
    raise LoginError(f"{email} is not authorized to use this service. Ask the administrator for access.")
