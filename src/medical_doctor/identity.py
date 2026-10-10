"""Who is calling: maps the authenticated principal to a stable, pseudonymous record owner."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from mcp.server.auth.middleware.auth_context import get_access_token


class AuthRequired(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    owner: str  # pseudonymous: sha256(issuer|subject); never an email
    client_id: str | None
    personal: bool  # False for shared credentials (e.g. a team API key) that must not own a chart


LOCAL = Principal(owner="local", client_id="stdio", personal=True)


def current_principal(local_mode: bool) -> Principal:
    token = get_access_token()
    if token is None:
        if local_mode:
            return LOCAL  # single-user stdio / localhost
        raise AuthRequired("Sign in to use patient records.")
    claims = token.claims or {}
    issuer = str(claims.get("iss") or "")
    subject = str(token.subject or token.client_id)
    owner = "own_" + hashlib.sha256(f"{issuer}|{subject}".encode()).hexdigest()[:32]
    # API keys are shared credentials unless labelled as belonging to one person ("person:alice").
    personal = not subject.startswith("api-key:") or subject.startswith("api-key:person:")
    return Principal(owner=owner, client_id=token.client_id, personal=personal)
