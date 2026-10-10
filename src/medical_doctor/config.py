"""Deployment configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse

AuthMode = Literal["none", "apikey", "oauth", "jwt"]

_PUBLIC_IDPS = ("common", "organizations", "consumers")


def _list(name: str) -> list[str]:
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class UpstreamIdP:
    """An identity provider users can sign in with (Google, GitHub, Microsoft, generic OIDC)."""

    name: str  # route key: google | github | microsoft | oidc
    display_name: str
    client_id: str
    client_secret: str
    authorize_url: str = ""
    token_url: str = ""
    userinfo_url: str = ""
    scope: str = "openid email profile"
    issuer: str = ""  # generic OIDC: resolved by discovery when urls are empty
    trust_unverified_email: bool = False


@dataclass(frozen=True)
class Settings:
    public_url: str = ""  # e.g. https://doctor.example.com (no trailing slash)
    auth_mode: AuthMode = "none"
    api_keys: dict[str, str] = field(default_factory=dict)  # key -> label
    idps: tuple[UpstreamIdP, ...] = ()
    allowed_emails: tuple[str, ...] = ()
    allowed_domains: tuple[str, ...] = ()
    allow_any_user: bool = False
    db_path: str = ""
    database_url: str = ""  # Postgres (e.g. Supabase); takes precedence over db_path
    access_token_ttl: int = 3600
    refresh_token_ttl: int = 30 * 24 * 3600
    max_session_lifetime: int = 90 * 24 * 3600  # absolute cap; refresh rotation never extends past it
    jwt_issuer: str = ""
    jwt_audience: str = ""
    jwt_jwks_url: str = ""
    jwt_required_scope: str = ""
    jwt_trust_unverified_email: bool = False
    cors_origins: tuple[str, ...] = ("*",)
    allowed_hosts: tuple[str, ...] = ()
    consult_limit_per_hour: int = 20
    # Consult model providers
    llm_provider: str = "anthropic"  # anthropic | openai | meta | llama
    llm_model: str = ""  # empty = provider default
    llm_effort: str = "medium"  # effort for depth="standard"
    fast_mode: bool = False  # Claude fast mode: ~2.5x faster output at 2x price (Claude API only)
    consult_depth: str = "standard"  # quick | standard | thorough
    second_opinion_model: str = ""  # default: the most capable Claude model (physician.REVIEWER_MODEL)
    auto_chart: bool = True  # open a chart on first visit and save stated facts after each consult
    provider_models: dict[str, str] = field(default_factory=dict)  # per-provider model overrides
    second_opinion_providers: tuple[str, ...] = ()
    openai_reasoning_effort: str = ""
    meta_base_url: str = "https://api.meta.ai/v1"  # Meta Model API (Muse Spark)
    llama_base_url: str = ""  # any OpenAI-compatible open-weight Llama host (Together, Groq, vLLM, ...)
    web_search: bool = True
    verify_citations: bool = True
    consult_max_rounds: int = 12
    consult_timeout: float = 270.0  # under Vercel's 300 s function limit
    max_case_chars: int = 50_000
    # Personal health record
    data_keys: str = ""  # MEDICAL_DOCTOR_DATA_KEYS ("kid:base64key,..."); first is active
    allow_unauthenticated: bool = False  # explicit opt-in to serve a non-local deployment without auth
    on_vercel: bool = False

    @property
    def resource_url(self) -> str:
        return f"{self.public_url}/mcp"

    @property
    def public_host(self) -> str:
        return urlparse(self.public_url).netloc


def _idps_from_env() -> tuple[UpstreamIdP, ...]:
    idps: list[UpstreamIdP] = []
    e = os.environ.get
    if e("GOOGLE_CLIENT_ID") and e("GOOGLE_CLIENT_SECRET"):
        idps.append(
            UpstreamIdP(
                "google",
                "Google",
                e("GOOGLE_CLIENT_ID", ""),
                e("GOOGLE_CLIENT_SECRET", ""),
                authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
                token_url="https://oauth2.googleapis.com/token",
                userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
            )
        )
    if e("GITHUB_CLIENT_ID") and e("GITHUB_CLIENT_SECRET"):
        idps.append(
            UpstreamIdP(
                "github",
                "GitHub",
                e("GITHUB_CLIENT_ID", ""),
                e("GITHUB_CLIENT_SECRET", ""),
                authorize_url="https://github.com/login/oauth/authorize",
                token_url="https://github.com/login/oauth/access_token",
                userinfo_url="https://api.github.com/user",
                scope="read:user user:email",
            )
        )
    if e("MICROSOFT_CLIENT_ID") and e("MICROSOFT_CLIENT_SECRET"):
        tenant = e("MICROSOFT_TENANT_ID", "")
        if not tenant or tenant in _PUBLIC_IDPS:
            # Multi-tenant Entra apps return an unverified, user-editable `email` claim
            # ("nOAuth"), so email allowlisting would be spoofable. Require one tenant.
            raise ValueError("MICROSOFT_TENANT_ID must be a specific tenant ID (not common/organizations/consumers)")
        base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
        idps.append(
            UpstreamIdP(
                "microsoft",
                "Microsoft",
                e("MICROSOFT_CLIENT_ID", ""),
                e("MICROSOFT_CLIENT_SECRET", ""),
                authorize_url=f"{base}/authorize",
                token_url=f"{base}/token",
                userinfo_url="https://graph.microsoft.com/oidc/userinfo",
                trust_unverified_email=True,  # single-tenant: emails are managed by that tenant's admins
            )
        )
    if e("OIDC_ISSUER") and e("OIDC_CLIENT_ID") and e("OIDC_CLIENT_SECRET"):
        idps.append(
            UpstreamIdP(
                "oidc",
                e("OIDC_DISPLAY_NAME", "Single sign-on"),
                e("OIDC_CLIENT_ID", ""),
                e("OIDC_CLIENT_SECRET", ""),
                issuer=e("OIDC_ISSUER", "").rstrip("/"),
                scope=e("OIDC_SCOPE", "openid email profile"),
                trust_unverified_email=_flag("OIDC_TRUST_UNVERIFIED_EMAIL"),
            )
        )
    return tuple(idps)


def _api_keys_from_env() -> dict[str, str]:
    """MEDICAL_DOCTOR_API_KEYS="label1:key1,label2:key2" (bare keys get a generic label)."""
    keys: dict[str, str] = {}
    for i, item in enumerate(_list("MEDICAL_DOCTOR_API_KEYS")):
        label, _, key = item.rpartition(":")
        if not key or len(key) < 24:
            raise ValueError("Each API key must be at least 24 characters (use `medical-doctor-mcp gen-key`)")
        keys[key] = label or f"key-{i + 1}"
    return keys


def load_settings() -> Settings:
    e = os.environ.get
    mode = e("MEDICAL_DOCTOR_AUTH", "none").strip().lower()
    if mode not in ("none", "apikey", "oauth", "jwt"):
        raise ValueError(f"MEDICAL_DOCTOR_AUTH must be none|apikey|oauth|jwt, got {mode!r}")
    public_url = e("MEDICAL_DOCTOR_PUBLIC_URL", "").rstrip("/")
    if not public_url and e("VERCEL_PROJECT_PRODUCTION_URL"):
        public_url = f"https://{e('VERCEL_PROJECT_PRODUCTION_URL')}"  # set automatically on Vercel
    s = Settings(
        public_url=public_url,
        auth_mode=mode,  # type: ignore[arg-type]
        api_keys=_api_keys_from_env(),
        idps=_idps_from_env() if mode == "oauth" else (),
        allowed_emails=tuple(x.lower() for x in _list("MEDICAL_DOCTOR_ALLOWED_EMAILS")),
        allowed_domains=tuple(x.lower().lstrip("@") for x in _list("MEDICAL_DOCTOR_ALLOWED_DOMAINS")),
        allow_any_user=_flag("MEDICAL_DOCTOR_ALLOW_ANY_USER"),
        db_path=e("MEDICAL_DOCTOR_DB", os.path.expanduser("~/.medical-doctor/auth.db")),
        # Supabase's Vercel integration sets POSTGRES_URL (pooled, transaction mode).
        database_url=e("MEDICAL_DOCTOR_DATABASE_URL") or e("POSTGRES_URL") or e("DATABASE_URL") or "",
        access_token_ttl=int(e("MEDICAL_DOCTOR_ACCESS_TOKEN_TTL", "3600")),
        refresh_token_ttl=int(e("MEDICAL_DOCTOR_REFRESH_TOKEN_TTL", str(30 * 24 * 3600))),
        max_session_lifetime=int(float(e("MEDICAL_DOCTOR_MAX_SESSION_DAYS", "90")) * 24 * 3600),
        jwt_issuer=e("MEDICAL_DOCTOR_JWT_ISSUER", "").rstrip("/"),
        jwt_audience=e("MEDICAL_DOCTOR_JWT_AUDIENCE", ""),
        jwt_jwks_url=e("MEDICAL_DOCTOR_JWT_JWKS_URL", ""),
        jwt_required_scope=e("MEDICAL_DOCTOR_JWT_REQUIRED_SCOPE", ""),
        jwt_trust_unverified_email=_flag("MEDICAL_DOCTOR_JWT_TRUST_UNVERIFIED_EMAIL"),
        cors_origins=tuple(_list("MEDICAL_DOCTOR_CORS_ORIGINS") or ["*"]),
        allowed_hosts=tuple(_list("MEDICAL_DOCTOR_ALLOWED_HOSTS")),
        consult_limit_per_hour=int(e("MEDICAL_DOCTOR_CONSULT_LIMIT_PER_HOUR", "20")),
        allow_unauthenticated=_flag("MEDICAL_DOCTOR_ALLOW_UNAUTHENTICATED"),
        llm_provider=e("MEDICAL_DOCTOR_PROVIDER", "anthropic").strip().lower(),
        llm_model=e("MEDICAL_DOCTOR_MODEL", ""),
        llm_effort=e("MEDICAL_DOCTOR_EFFORT", "medium"),
        fast_mode=_flag("MEDICAL_DOCTOR_FAST_MODE"),
        consult_depth=e("MEDICAL_DOCTOR_CONSULT_DEPTH", "standard").strip().lower(),
        auto_chart=_flag("MEDICAL_DOCTOR_AUTO_CHART", True),
        second_opinion_model=e("MEDICAL_DOCTOR_SECOND_OPINION_MODEL", ""),
        provider_models={
            k: v for k, v in {
                "anthropic": e("MEDICAL_DOCTOR_ANTHROPIC_MODEL", ""),
                "openai": e("MEDICAL_DOCTOR_OPENAI_MODEL", ""),
                "meta": e("MEDICAL_DOCTOR_META_MODEL", ""),
                "llama": e("MEDICAL_DOCTOR_LLAMA_MODEL", ""),
            }.items() if v
        },
        second_opinion_providers=tuple(x.lower() for x in _list("MEDICAL_DOCTOR_SECOND_OPINION")),
        openai_reasoning_effort=e("MEDICAL_DOCTOR_OPENAI_REASONING_EFFORT", ""),
        meta_base_url=e("MEDICAL_DOCTOR_META_BASE_URL", "https://api.meta.ai/v1"),
        llama_base_url=e("MEDICAL_DOCTOR_LLAMA_BASE_URL", ""),
        web_search=_flag("MEDICAL_DOCTOR_WEB_SEARCH", True),
        verify_citations=_flag("MEDICAL_DOCTOR_VERIFY_CITATIONS", True),
        consult_max_rounds=int(e("MEDICAL_DOCTOR_MAX_TOOL_ROUNDS", "12")),
        consult_timeout=float(e("MEDICAL_DOCTOR_CONSULT_TIMEOUT", "270")),
        data_keys=e("MEDICAL_DOCTOR_DATA_KEYS", ""),
        on_vercel=bool(e("VERCEL")),
    )
    validate(s)
    if s.on_vercel and s.auth_mode == "none" and not s.allow_unauthenticated:
        raise ValueError(
            "Refusing to serve a public deployment without authentication (anyone could run paid consults). "
            "Set MEDICAL_DOCTOR_AUTH=oauth|jwt|apikey, or MEDICAL_DOCTOR_ALLOW_UNAUTHENTICATED=1 if you really mean it"
        )
    if e("VERCEL") and s.auth_mode == "oauth" and not s.database_url:
        raise ValueError(
            "On Vercel the local disk is temporary and per-instance: set POSTGRES_URL (Supabase pooled "
            "connection string) or MEDICAL_DOCTOR_DATABASE_URL so sign-ins and tokens persist"
        )
    return s


PROVIDERS = ("anthropic", "openai", "meta", "llama")


def is_training_model(model: str) -> bool:
    """Meta Model API '*-contributor' models share traffic with Meta for product improvement."""
    return "contributor" in model.lower()


def validate(s: Settings) -> None:
    if s.consult_depth not in ("quick", "standard", "thorough"):
        raise ValueError("MEDICAL_DOCTOR_CONSULT_DEPTH must be quick, standard or thorough")
    for p in (s.llm_provider, *s.second_opinion_providers):
        if p not in PROVIDERS:
            raise ValueError(f"Unknown model provider {p!r}: use {', '.join(PROVIDERS)}")
        if p == "llama" and not s.llama_base_url:
            raise ValueError("Provider 'llama' needs MEDICAL_DOCTOR_LLAMA_BASE_URL (an OpenAI-compatible Llama host)")
    for p, model in [(s.llm_provider, s.llm_model), *s.provider_models.items()]:
        if p == "meta" and model and is_training_model(model):
            raise ValueError(
                f"Meta model {model!r} is a '-contributor' variant: Meta uses its traffic to improve its products, "
                "so it must never receive patient data. Use the standard model ID (e.g. muse-spark-1.3)"
            )
    if s.auth_mode == "none":
        return
    if not s.public_url:
        raise ValueError("MEDICAL_DOCTOR_PUBLIC_URL is required when authentication is enabled")
    parsed = urlparse(s.public_url)
    if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
        raise ValueError("MEDICAL_DOCTOR_PUBLIC_URL must be https (OAuth 2.1 requirement)")
    if s.auth_mode == "apikey" and not s.api_keys:
        raise ValueError("MEDICAL_DOCTOR_AUTH=apikey requires MEDICAL_DOCTOR_API_KEYS")
    if s.auth_mode == "oauth":
        if not s.idps:
            raise ValueError(
                "MEDICAL_DOCTOR_AUTH=oauth requires at least one sign-in provider "
                "(GOOGLE_CLIENT_ID/SECRET, GITHUB_*, MICROSOFT_*, or OIDC_*)"
            )
        if not (s.allowed_emails or s.allowed_domains or s.allow_any_user):
            raise ValueError(
                "Refusing to start with open sign-up: set MEDICAL_DOCTOR_ALLOWED_DOMAINS and/or "
                "MEDICAL_DOCTOR_ALLOWED_EMAILS, or MEDICAL_DOCTOR_ALLOW_ANY_USER=1 to allow everyone"
            )
    if s.auth_mode == "jwt" and not s.jwt_issuer:
        raise ValueError("MEDICAL_DOCTOR_AUTH=jwt requires MEDICAL_DOCTOR_JWT_ISSUER")


def assert_safe_exposure(settings: Settings, host: str) -> None:
    """A network-reachable server without authentication lets anyone spend the model budget."""
    loopback = host in ("127.0.0.1", "localhost", "::1")
    if not loopback and settings.auth_mode == "none" and not settings.allow_unauthenticated:
        raise ValueError(
            f"Refusing to listen on {host} without authentication. Set MEDICAL_DOCTOR_AUTH=oauth|jwt|apikey "
            "(see README), or MEDICAL_DOCTOR_ALLOW_UNAUTHENTICATED=1 for a trusted private network"
        )
