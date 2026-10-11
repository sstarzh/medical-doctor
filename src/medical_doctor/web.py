"""Security headers shared by every HTML page the server renders (setup page, login, consent, EHR)."""

from __future__ import annotations

import base64
import hashlib

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'; base-uri 'none'",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


def script_hash(script: str) -> str:
    """CSP source for one static inline script. Unlike a nonce, a hash stays valid when the page is cached."""
    return "'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"


def page_headers(*, scripts: tuple[str, ...] = (), cache: str = "no-store", **extra: str) -> dict[str, str]:
    csp = SECURITY_HEADERS["Content-Security-Policy"]
    if scripts:
        csp += "; script-src " + " ".join(script_hash(s) for s in scripts)
    return {**SECURITY_HEADERS, "Content-Security-Policy": csp, "Cache-Control": cache, **extra}
