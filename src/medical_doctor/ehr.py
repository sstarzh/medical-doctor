"""Patient-authorized EHR connector (SMART on FHIR standalone launch).

The patient signs in to their own portal — Epic MyChart, Oracle Health (Cerner), athenahealth, the VA
and any other SMART on FHIR server — approves read access, and their records (conditions,
medications, allergies, labs, vitals, immunizations, procedures) are imported into a chart
profile through the same mapper as `patient_import_fhir`.

Flow:
  ehr_connect (MCP tool, signed-in user)  -> returns /ehr/start?s=<state>
  /ehr/start   (browser)  consent page naming the account and profile; sets a SameSite cookie
  /ehr/go      (browser, POST from that page) -> redirect to the portal's authorize endpoint (PKCE)
  /ehr/callback (browser) code -> token -> FHIR reads -> import -> result page

Security:
  * The state is unguessable, single-use, expires in 10 minutes, and is bound to the browser that
    saw the consent page. So a link sent to someone else cannot pull their records into the
    sender's account without them seeing whose account it is.
  * Portal access tokens are used once for the import and never stored.
  * The FHIR base URL is user-supplied, so it must be https and resolve only to public addresses
    (no SSRF into the server's network). Redirects are not followed.
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import os
import secrets
import socket
import time
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx2 as httpx

from .auth.upstream import pkce_pair

STATE_TTL = 600
COOKIE = "md_ehr"
MAX_PAGES_PER_TYPE = 10
MAX_RESOURCES = 2000
EPIC_SANDBOX = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4"

# SMART v1 scopes: the most widely supported form (Epic, Oracle Health, athenahealth, VA).
DEFAULT_SCOPES = (
    "openid fhirUser launch/patient patient/Patient.read patient/Condition.read patient/MedicationRequest.read "
    "patient/AllergyIntolerance.read patient/Observation.read patient/Immunization.read patient/Procedure.read"
)

# (resource type, extra search params). Epic requires a category on Observation searches.
SEARCHES: list[tuple[str, dict[str, str]]] = [
    ("Condition", {}),
    ("MedicationRequest", {}),
    ("AllergyIntolerance", {}),
    ("Observation", {"category": "laboratory"}),
    ("Observation", {"category": "vital-signs"}),
    ("Immunization", {}),
    ("Procedure", {}),
]

VENDORS = {
    "epic": "Epic MyChart",
    "oracle": "Oracle Health (Cerner)",
    "athena": "athenahealth",
    "smart": "SMART on FHIR portal",
}


class EhrError(Exception):
    pass


def parse_pairs(value: str) -> dict[str, str]:
    """'epic=abc,oracle=def' -> {'epic': 'abc', 'oracle': 'def'}."""
    out = {}
    for item in filter(None, (x.strip() for x in value.split(","))):
        name, _, val = item.partition("=")
        if name and val:
            out[name.strip().lower()] = val.strip()
    return out


def check_public_https(url: str) -> str:
    """Reject anything but https to a hostname that resolves only to public addresses."""
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise EhrError("The FHIR server URL must be an https:// URL")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        raise EhrError(f"Cannot resolve {p.hostname}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise EhrError("The FHIR server URL must point to a public internet address")
    return url.rstrip("/")


class EhrConnector:
    def __init__(self, settings: Any, astore: Any, service_for: Any, http: Any = None):
        self.settings = settings
        self.astore = astore
        self.service_for = service_for  # () -> PatientService
        self.clients = parse_pairs(os.environ.get("MEDICAL_DOCTOR_SMART_CLIENTS", ""))
        self.secrets = parse_pairs(os.environ.get("MEDICAL_DOCTOR_SMART_SECRETS", ""))
        self.scopes = os.environ.get("MEDICAL_DOCTOR_SMART_SCOPES", DEFAULT_SCOPES)
        self._http = http  # factory returning an async HTTP client (tests inject one)
        self._resolve = check_public_https

    @property
    def redirect_uri(self) -> str:
        return f"{self.settings.public_url}/ehr/callback"

    def available(self) -> str | None:
        """None when usable, else the reason."""
        if not self.settings.public_url:
            return "EHR connections need the server's public https URL (MEDICAL_DOCTOR_PUBLIC_URL)."
        if self.astore is None:
            return "EHR connections need the server's sign-in store."
        if not self.clients:
            return ("No portal app is registered: set MEDICAL_DOCTOR_SMART_CLIENTS (e.g. epic=<client id from "
                    "fhir.epic.com>) with redirect URI " + self.redirect_uri)
        return None

    def http(self) -> Any:
        if self._http:
            return self._http()
        return httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=False,
                                               headers={"Accept": "application/fhir+json, application/json"})

    async def discover(self, base: str) -> dict[str, str]:
        async with self.http() as c:
            r = await c.get(f"{base}/.well-known/smart-configuration")
            if r.status_code == 200:
                cfg = r.json()
                if cfg.get("authorization_endpoint") and cfg.get("token_endpoint"):
                    return {"authorize": cfg["authorization_endpoint"], "token": cfg["token_endpoint"]}
            r = await c.get(f"{base}/metadata")
            if r.status_code == 200:
                for rest in r.json().get("rest", []):
                    for ext in (rest.get("security") or {}).get("extension", []):
                        if ext.get("url", "").endswith("oauth-uris"):
                            uris = {e["url"]: e.get("valueUri") for e in ext.get("extension", [])}
                            if uris.get("authorize") and uris.get("token"):
                                return {"authorize": uris["authorize"], "token": uris["token"]}
        raise EhrError(f"{base} does not advertise SMART on FHIR sign-in")

    async def start(self, *, owner: str, client_id: str | None, account: str, patient_id: str, patient_label: str,
                    vendor: str, fhir_base_url: str) -> dict[str, Any]:
        if reason := self.available():
            raise EhrError(reason)
        vendor = vendor.lower()
        if vendor not in self.clients:
            raise EhrError(f"No app registered for {vendor!r}. Configured: {', '.join(sorted(self.clients))}")
        if not fhir_base_url:
            raise EhrError("Give the FHIR base URL of the health system's patient API (or 'sandbox' to test with Epic's sandbox)")
        base = EPIC_SANDBOX if (vendor == "epic" and fhir_base_url.strip().lower() == "sandbox") else fhir_base_url
        base = await asyncio.to_thread(self._resolve, base)
        endpoints = await self.discover(base)
        for u in endpoints.values():
            await asyncio.to_thread(self._resolve, u)
        state = secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()
        authorize = endpoints["authorize"] + ("&" if "?" in endpoints["authorize"] else "?") + urlencode({
            "response_type": "code", "client_id": self.clients[vendor], "redirect_uri": self.redirect_uri,
            "scope": self.scopes, "state": state, "aud": base, "code_challenge": challenge, "code_challenge_method": "S256",
        })
        await self.astore.put("ehr", state, {
            "owner": owner, "client_id": client_id, "account": account, "patient_id": patient_id,
            "patient_label": patient_label, "vendor": vendor, "base": base, "token": endpoints["token"],
            "authorize": authorize, "verifier": verifier, "browser": None,
        }, STATE_TTL)
        return {"connect_url": f"{self.settings.public_url}/ehr/start?s={state}", "expires_in_seconds": STATE_TTL,
                "portal": VENDORS.get(vendor, vendor), "fhir_server": base}

    async def exchange(self, ctx: dict[str, Any], code: str) -> dict[str, Any]:
        vendor = ctx["vendor"]
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri,
                "client_id": self.clients[vendor], "code_verifier": ctx["verifier"]}
        auth = (self.clients[vendor], self.secrets[vendor]) if vendor in self.secrets else None
        async with self.http() as c:
            r = await c.post(ctx["token"], data=form, auth=auth, headers={"Accept": "application/json"})
        if r.status_code != 200:
            raise EhrError(f"The portal refused the sign-in (HTTP {r.status_code}).")
        tok = r.json()
        if not tok.get("access_token") or not tok.get("patient"):
            raise EhrError("The portal did not grant patient-level access.")
        return tok

    async def fetch(self, base: str, token: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
        pid = token["patient"]
        headers = {"Authorization": f"Bearer {token['access_token']}"}
        resources: list[dict[str, Any]] = []
        notes: dict[str, str] = {}
        base_host = urlparse(base).hostname
        async with self.http() as c:
            r = await c.get(f"{base}/Patient/{pid}", headers=headers)
            if r.status_code == 200:
                resources.append(r.json())

            async def search(rtype: str, extra: dict[str, str]) -> list[dict[str, Any]]:
                got: list[dict[str, Any]] = []
                url: str | None = f"{base}/{rtype}?" + urlencode({"patient": pid, **extra, "_count": "100"})
                label = rtype + (f"[{extra['category']}]" if "category" in extra else "")
                for _ in range(MAX_PAGES_PER_TYPE):
                    if not url or urlparse(url).hostname != base_host:  # never send the token elsewhere
                        break
                    resp = await c.get(url, headers=headers)
                    if resp.status_code != 200:
                        notes[label] = f"not available (HTTP {resp.status_code})"
                        break
                    bundle = resp.json()
                    got += [e["resource"] for e in bundle.get("entry", []) if isinstance(e.get("resource"), dict)]
                    url = next((ln.get("url") for ln in bundle.get("link", []) if ln.get("relation") == "next"), None)
                return got

            for found in await asyncio.gather(*(search(t, x) for t, x in SEARCHES)):
                resources += found
        # OperationOutcome entries (warnings) are not records.
        resources = [r for r in resources if r.get("resourceType") != "OperationOutcome"][:MAX_RESOURCES]
        return resources, notes

    async def complete(self, state: str, code: str, browser: str | None) -> dict[str, Any]:
        ctx = await self.astore.pop("ehr", state)
        if not ctx:
            raise EhrError("This connection link has expired. Start again from your AI app.")
        if not browser or not secrets.compare_digest(browser, ctx.get("browser") or ""):
            raise EhrError("Finish the connection in the same browser where you approved it.")
        token = await self.exchange(ctx, code)
        resources, notes = await self.fetch(ctx["base"], token)
        result = await self.service_for().import_fhir(ctx["owner"], ctx["client_id"], resources, ctx["patient_id"])
        result["unavailable"] = notes
        result["portal"] = VENDORS.get(ctx["vendor"], ctx["vendor"])
        result["patient_label"] = ctx["patient_label"]
        return result


def register_ehr_routes(mcp: Any, connector: EhrConnector) -> None:
    from starlette.requests import Request
    from starlette.responses import RedirectResponse, Response

    from .auth.oauth import _page

    secure = connector.settings.public_url.startswith("https://")

    def fail(message: str, status: int = 400) -> Response:
        return _page("Connection failed", f"<h1>Connection failed</h1><p>{html.escape(message)}</p>", status)

    @mcp.custom_route("/ehr/start", methods=["GET"])
    async def ehr_start(request: Request) -> Response:
        state = request.query_params.get("s", "")
        ctx = await connector.astore.get("ehr", state) if state else None
        if not ctx:
            return fail("This connection link has expired. Start again from your AI app.", 404)
        browser = secrets.token_urlsafe(24)
        ctx["browser"] = browser
        await connector.astore.put("ehr", state, ctx, STATE_TTL)
        body = (
            f"<h1>Connect {html.escape(VENDORS.get(ctx['vendor'], ctx['vendor']))}</h1>"
            f"<p>You will sign in to your patient portal at <code>{html.escape(urlparse(ctx['base']).hostname or '')}</code> "
            f"and approve read access. Your records will be imported into the profile "
            f"<strong>{html.escape(ctx['patient_label'])}</strong> of the account <strong>{html.escape(ctx['account'])}</strong>.</p>"
            "<p class='muted'>Only continue if that is your account and you started this yourself. "
            "The portal's access is used once for this import and is not stored.</p>"
            f'<form method="post" action="/ehr/go"><input type="hidden" name="s" value="{html.escape(state)}">'
            '<button class="btn" type="submit">Continue to the patient portal</button></form>'
        )
        response = _page("Connect health records", body)
        response.set_cookie(COOKIE, browser, max_age=STATE_TTL, httponly=True, secure=secure, samesite="lax", path="/ehr")
        return response

    @mcp.custom_route("/ehr/go", methods=["POST"])
    async def ehr_go(request: Request) -> Response:
        form = await request.form()
        state = str(form.get("s", ""))
        ctx = await connector.astore.get("ehr", state) if state else None
        cookie = request.cookies.get(COOKIE, "")
        if not ctx or not ctx.get("browser") or not secrets.compare_digest(cookie, ctx["browser"]):
            return fail("This connection link has expired or was opened in another browser.", 404)
        return RedirectResponse(ctx["authorize"], status_code=303)

    @mcp.custom_route("/ehr/callback", methods=["GET"])
    async def ehr_callback(request: Request) -> Response:
        q = request.query_params
        if q.get("error"):
            await connector.astore.pop("ehr", q.get("state", ""))
            return fail(f"The portal did not grant access ({q.get('error')}).")
        started = time.monotonic()
        try:
            result = await connector.complete(q.get("state", ""), q.get("code", ""), request.cookies.get(COOKIE))
        except EhrError as exc:
            return fail(str(exc))
        except Exception as exc:  # upstream portal or network failure
            return fail(f"Import failed: {type(exc).__name__}. Nothing was changed if no records were listed.")
        rows = "".join(f"<li>{html.escape(k.replace('_', ' '))}: {v}</li>" for k, v in sorted(result["imported"].items()))
        skipped = len(result["skipped"]) + len(result["failed"])
        missing = ", ".join(result["unavailable"]) if result["unavailable"] else ""
        body = (
            f"<h1>Records imported</h1><p>From {html.escape(result['portal'])} into "
            f"<strong>{html.escape(result['patient_label'])}</strong>:</p><ul>{rows or '<li>nothing new</li>'}</ul>"
            + (f"<p class='muted'>{skipped} entries were skipped (entered in error, refuted, or incomplete).</p>" if skipped else "")
            + (f"<p class='muted'>Not shared by this portal: {html.escape(missing)}.</p>" if missing else "")
            + f"<p class='muted'>Done in {time.monotonic() - started:.1f} s. You can close this tab and go back to your AI app; "
            "ask it to review the imported chart.</p>"
        )
        response = _page("Records imported", body)
        response.delete_cookie(COOKIE, path="/ehr")
        return response
