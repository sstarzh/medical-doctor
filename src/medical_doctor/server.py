"""MCP server: 'Dr. MCP' — attending-grade clinical decision support."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import TextContent, ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import __version__, calculators, pediatric, physician, safety, sources
from .config import Settings
from .skills import load_skills

log = logging.getLogger("medical_doctor")
audit = logging.getLogger("medical_doctor.audit")

DISCLAIMER = (
    "Decision support only — not a diagnosis or a substitute for an in-person clinician. "
    "In an emergency call local emergency services."
)

INSTRUCTIONS = f"""\
Medical Doctor MCP: attending-physician-grade clinical decision support.

Workflow for any health question:
1. Call `red_flag_screen` on the user's words first. If urgency is "emergency", tell the user to get
   emergency care immediately before anything else.
2. For a full assessment call `consult` (returns a structured attending consult note).
   It covers adults and children (pediatric attending for patients under 18; give age and weight).
   If `consult` reports no model credentials, follow the returned `protocol` yourself, using the
   calculator, drug-label, PubMed and trials tools here, and the skills (`list_skills`/`get_skill`).
3. Never compute clinical scores or quote drug doses/interactions from memory: use the tools.
4. `search` / `fetch` find and read skills, calculators and PubMed articles.

{DISCLAIMER}
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
READ_ONLY_WEB = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

# Tools that the inner physician model must not call (recursion / meta tools).
_NOT_FOR_PHYSICIAN = {"consult", "list_skills", "get_skill", "search", "fetch", "whoami"}

ALL_CALCULATORS = {**calculators.CALCULATORS, **pediatric.PEDIATRIC_CALCULATORS}


def current_user() -> dict[str, Any]:
    token = get_access_token()
    if token is None:
        return {"authenticated": False}
    claims = token.claims or {}
    return {
        "authenticated": True,
        "subject": token.subject,
        "email": claims.get("email"),
        "name": claims.get("name"),
        "client_id": token.client_id,
        "scopes": token.scopes,
    }


class RateLimiter:
    """Sliding one-hour window per user, protecting the model budget behind `consult`.

    Counters live in the SQLite store (MEDICAL_DOCTOR_DB), so the limit holds across worker
    processes and restarts on a host, and expired hits are purged. Replicas on different hosts
    need a shared volume for the database or a per-replica limit.
    """

    def __init__(self, per_hour: int, store: Any = None):
        self.per_hour = per_hour
        self.store = store

    def check(self, key: str) -> None:
        if self.per_hour <= 0:
            return
        if self.store is None:
            from .auth.store import Store

            self.store = Store()
        if not self.store.hit(f"consult:{key}", self.per_hour, 3600):
            raise ToolError(f"Consult limit reached ({self.per_hour}/hour). Try again later.")


def auth_kwargs(settings: Settings, store: Any, http: Any = None) -> dict[str, Any]:
    """MCPServer keyword arguments for the configured authentication mode."""
    if settings.auth_mode == "none":
        return {}
    if settings.auth_mode == "apikey":
        from .auth.verifiers import ApiKeyVerifier

        return {
            "auth": AuthSettings(issuer_url=settings.public_url, resource_server_url=None, required_scopes=["mcp"]),
            "token_verifier": ApiKeyVerifier(settings),
        }
    if settings.auth_mode == "jwt":
        from .auth.verifiers import JwtVerifier

        return {
            "auth": AuthSettings(
                issuer_url=settings.jwt_issuer,
                resource_server_url=settings.resource_url,
                validate_token_resource=False,  # JwtVerifier checks the audience itself
                required_scopes=[settings.jwt_required_scope] if settings.jwt_required_scope else None,
                service_documentation_url=f"{settings.public_url}/",
            ),
            "token_verifier": JwtVerifier(settings),
        }
    from .auth.oauth import SCOPE, BrokerOAuthProvider
    from .auth.upstream import default_http

    provider = BrokerOAuthProvider(settings, store, http or default_http)
    return {
        "auth": AuthSettings(
            issuer_url=settings.public_url,
            resource_server_url=settings.resource_url,
            validate_token_resource=True,
            required_scopes=[SCOPE],
            client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
            revocation_options=RevocationOptions(enabled=True),
            service_documentation_url=f"{settings.public_url}/",
        ),
        "auth_server_provider": provider,
    }


def build_server(settings: Settings | None = None, *, store: Any = None, http: Any = None) -> MCPServer:
    settings = settings or Settings()
    if store is None and (settings.auth_mode == "oauth" or settings.consult_limit_per_hour > 0):
        from .auth.store import Store

        store = Store(settings.db_path or ":memory:")
    kwargs = auth_kwargs(settings, store, http)
    mcp = MCPServer(
        name="medical-doctor",
        title="Medical Doctor (Attending Consult)",
        instructions=INSTRUCTIONS,
        version=__version__,
        website_url=settings.public_url or None,
        **kwargs,
    )
    limiter = RateLimiter(settings.consult_limit_per_hour, store)
    _register_tools(mcp, limiter)
    _register_routes(mcp, settings)
    if settings.auth_mode == "oauth":
        from .auth.oauth import register_login_routes

        register_login_routes(mcp, kwargs["auth_server_provider"])
    return mcp


def _register_routes(mcp: MCPServer, settings: Settings) -> None:
    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> Response:
        return JSONResponse({"status": "ok", "version": __version__})

    @mcp.custom_route("/", methods=["GET"])
    async def index(request: Request) -> Response:
        return JSONResponse(
            {
                "name": "Medical Doctor MCP",
                "version": __version__,
                "mcp_endpoint": f"{settings.public_url}/mcp" if settings.public_url else "/mcp",
                "transport": "streamable-http",
                "auth": settings.auth_mode,
                "docs": "https://github.com/sstarzh/medical-doctor",
                "disclaimer": DISCLAIMER,
            }
        )


def _register_tools(mcp: MCPServer, limiter: RateLimiter) -> None:
    # --- Safety ----------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    def red_flag_screen(text: str) -> dict[str, Any]:
        """Deterministic emergency red-flag screen of free-text symptoms (stroke, ACS, sepsis, anaphylaxis,
        suicide risk, obstetric and pediatric emergencies, etc.). Run this FIRST on any patient narrative.
        Returns urgency (emergency|urgent|routine), matched red flags, and the immediate action to take."""
        return safety.screen(text).to_dict()

    # --- Consult ---------------------------------------------------------

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
    async def consult(
        case: str,
        ctx: Context,
        question: str | None = None,
        audience: Literal["clinician", "patient", "caregiver"] = "clinician",
    ) -> dict[str, Any]:
        """Full attending-physician consult for adults AND children (Med-Peds attending: internal medicine +
        pediatrics, incl. neonates and adolescents). Give the whole case: age (days/months for infants), weight
        for children, sex, pregnancy status, chief complaint, history of present illness, past history,
        medications, allergies, vitals, exam, labs/imaging.
        Returns a structured consult note: urgency, problem representation, ranked differential with
        can't-miss diagnoses, recommended workup, evidence-cited management, and a patient explanation.
        audience='patient' tailors language for a layperson; 'caregiver' for a parent of a sick child.
        Takes 30 s to a few minutes."""
        user = current_user()
        who = user.get("subject") or "anonymous"
        triage = safety.screen(" ".join(filter(None, [case, question]))).to_dict()
        # Audit who/when/urgency only — never the clinical text (PHI).
        audit.info("consult user=%s client=%s urgency=%s", who, user.get("client_id"), triage["urgency"])
        if not physician.has_credentials():
            return {
                "ok": False,
                "red_flag_screen": triage,
                "error": "No Anthropic credentials on the server (set ANTHROPIC_API_KEY). Falling back to protocol mode.",
                "protocol": physician.CHARTER,
                "how_to_proceed": "Act as the attending yourself: follow `protocol`, load relevant skills with "
                "get_skill, and call this server's calculator/drug/PubMed tools to verify every number and claim.",
                "disclaimer": DISCLAIMER,
            }
        limiter.check(who)

        async def progress(step: int, message: str) -> None:
            try:
                await ctx.report_progress(step, None, message)
            except Exception:  # progress is best-effort (client may not send a progress token)
                pass

        result = await physician.run_consult(
            case=case,
            question=question,
            audience=audience,
            triage=triage,
            tools=await physician_tools(),
            execute_tool=execute_for_physician,
            progress=progress,
        )
        result["red_flag_screen"] = triage
        result["disclaimer"] = DISCLAIMER
        return result

    async def physician_tools() -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in await mcp.list_tools()
            if t.name not in _NOT_FOR_PHYSICIAN
        ]

    async def execute_for_physician(name: str, arguments: dict[str, Any]) -> str:
        if name in _NOT_FOR_PHYSICIAN:
            raise ToolError(f"{name} is not available inside a consult")
        result = await mcp.call_tool(name, arguments)
        if getattr(result, "structured_content", None):
            text = json.dumps(result.structured_content)
        else:
            text = "\n".join(c.text for c in getattr(result, "content", []) if isinstance(c, TextContent))
        if getattr(result, "is_error", False):
            raise ToolError(text)
        return text

    # Exposed for tests and embedding.
    mcp.physician_tools = physician_tools  # type: ignore[attr-defined]
    mcp.execute_for_physician = execute_for_physician  # type: ignore[attr-defined]

    # --- Calculators -----------------------------------------------------

    for name, fn in ALL_CALCULATORS.items():
        mcp.add_tool(
            fn,
            name=f"calc_{name}",
            description=f"Clinical calculator: {(fn.__doc__ or name).strip()} Returns value, interpretation and reference.",
            annotations=READ_ONLY,
        )

    # --- Drugs & evidence ------------------------------------------------

    @mcp.tool(annotations=READ_ONLY_WEB)
    async def normalize_drug(name: str) -> dict[str, Any]:
        """Resolve a brand, misspelled, or combination drug name to RxNorm ingredients and brands (NLM RxNav)."""
        return await sources.normalize_drug(name)

    @mcp.tool(annotations=READ_ONLY_WEB)
    async def drug_label(drug: str, sections: list[str] | None = None) -> dict[str, Any]:
        """FDA-approved prescribing information (openFDA SPL) for a drug: boxed warning, indications, dosing,
        contraindications, warnings, interactions, specific populations (pregnancy, renal, hepatic), adverse
        reactions. Optionally restrict to `sections` (e.g. ["boxed_warning","dosage_and_administration"])."""
        return await sources.drug_label(drug, sections)

    @mcp.tool(annotations=READ_ONLY_WEB)
    async def drug_interaction_check(drugs: list[str]) -> dict[str, Any]:
        """Pairwise drug-drug interaction screen from each drug's FDA label Drug Interactions section.
        Provide 2+ generic or brand names. Absence of a finding does not prove safety."""
        return await sources.drug_interactions(drugs)

    @mcp.tool(annotations=READ_ONLY_WEB)
    async def pubmed_search(
        query: str, max_results: int = 8, high_quality_only: bool = True, since_year: int | None = None
    ) -> dict[str, Any]:
        """Search PubMed. high_quality_only restricts to systematic reviews, meta-analyses, RCTs and guidelines.
        Returns PMIDs, titles, journals, dates and URLs. Use pubmed_abstract to read one."""
        return await sources.pubmed_search(query, max_results, high_quality_only, since_year)

    @mcp.tool(annotations=READ_ONLY_WEB)
    async def pubmed_abstract(pmid: str) -> dict[str, Any]:
        """Fetch the abstract of a PubMed article by PMID."""
        return await sources.pubmed_abstract(pmid)

    @mcp.tool(annotations=READ_ONLY_WEB)
    async def clinical_trials_search(
        condition: str,
        intervention: str | None = None,
        location: str | None = None,
        recruiting_only: bool = True,
        max_results: int = 10,
    ) -> dict[str, Any]:
        """Search ClinicalTrials.gov for studies by condition, optional intervention and location."""
        return await sources.clinical_trials(condition, intervention, location, recruiting_only, max_results)

    # --- Skills ----------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    def list_skills() -> list[dict[str, str]]:
        """List the clinical skills (specialty playbooks) available on this server."""
        return [{"name": s.name, "description": s.description} for s in load_skills().values()]

    @mcp.tool(annotations=READ_ONLY)
    def get_skill(name: str) -> str:
        """Return the full text of a clinical skill playbook by name (see list_skills)."""
        skills = load_skills()
        if name not in skills:
            raise ToolError(f"Unknown skill '{name}'. Available: {', '.join(skills)}")
        return skills[name].full_text

    @mcp.resource(
        "skill://{name}", name="clinical-skill", description="Clinical skill playbook (SKILL.md)", mime_type="text/markdown"
    )
    def skill_resource(name: str) -> str:
        return get_skill(name)

    # --- search / fetch (OpenAI ChatGPT connectors & deep research convention) ---

    @mcp.tool(annotations=READ_ONLY_WEB)
    async def search(query: str) -> dict[str, Any]:
        """Search this server's clinical knowledge: specialty skills (playbooks), clinical calculators, and
        high-quality PubMed literature. Returns {"results": [{"id", "title", "url"}]}; read one with fetch(id)."""
        return {"results": await _search(query)}

    @mcp.tool(annotations=READ_ONLY_WEB)
    async def fetch(id: str) -> dict[str, Any]:
        """Fetch the full content of a search result by id ("skill:<name>", "calc:<name>" or "pubmed:<pmid>").
        Returns {"id", "title", "text", "url", "metadata"}."""
        return await _fetch(id)

    @mcp.tool(annotations=READ_ONLY)
    def whoami() -> dict[str, Any]:
        """Show which signed-in account and client this connection is using (useful to verify a connector)."""
        return current_user()

    # --- Prompts ---------------------------------------------------------

    @mcp.prompt(name="attending_consult", description="Run a structured attending-physician consult on a case using this server's tools.")
    def attending_consult_prompt(case: str, audience: str = "clinician") -> str:
        return (
            f"{physician.CHARTER}\n\nAudience: {audience}\n\n"
            "First call red_flag_screen on the case. Then reason through it, calling the calculator, drug_label, "
            "drug_interaction_check and pubmed_search tools to verify every score, dose and claim, and get_skill for "
            "the relevant specialty playbooks.\n\n"
            f"<case>\n{case}\n</case>"
        )

    @mcp.prompt(name="pediatric_consult", description="Pediatric attending consult for an infant, child or adolescent.")
    def pediatric_consult_prompt(case: str, age: str, weight_kg: str = "unknown", audience: str = "caregiver") -> str:
        return (
            f"{physician.CHARTER}\n\nYou are acting as the pediatric attending. Audience: {audience}\n\n"
            "First call red_flag_screen on the case and calc_pediatric_vital_signs with any vitals. Load the pediatric "
            "skills with get_skill (pediatrics, pediatric-emergency, neonatology, pediatric-infectious-disease, "
            "well-child-development). Use pediatric decision rules and calc_weight_based_dose for any dose; if weight is "
            "unknown, ask for it (or use calc_estimated_weight_by_age only in an emergency).\n\n"
            f"<age>{age}</age>\n<weight_kg>{weight_kg}</weight_kg>\n<case>\n{case}\n</case>"
        )

    @mcp.prompt(name="medication_review", description="Comprehensive medication review (interactions, renal dosing, Beers, deprescribing).")
    def medication_review_prompt(medications: str, patient_context: str = "") -> str:
        return (
            "Perform a clinical-pharmacist-grade medication review. Load the `pharmacotherapy` skill with get_skill. "
            "Normalize each drug with normalize_drug, run drug_interaction_check on the full list, compute renal "
            "function with calc_egfr_ckd_epi_2021 / calc_creatinine_clearance_cockcroft_gault if labs are given, and "
            "check each label's boxed warnings and dosing. Output: problems found (severity-ranked), recommended "
            "changes, monitoring, and questions for the prescriber.\n\n"
            f"<medications>\n{medications}\n</medications>\n<patient_context>\n{patient_context}\n</patient_context>"
        )


# --- search / fetch implementation --------------------------------------

REPO = "https://github.com/sstarzh/medical-doctor"


def _score(query_terms: list[str], text: str) -> int:
    t = text.lower()
    return sum(t.count(q) for q in query_terms)


async def _search(query: str, limit: int = 10) -> list[dict[str, str]]:
    terms = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2] or [query.lower()]
    scored: list[tuple[int, dict[str, str]]] = []
    for s in load_skills().values():
        score = 5 * _score(terms, s.name + " " + s.description) + _score(terms, s.body)
        if score:
            scored.append((score, {"id": f"skill:{s.name}", "title": f"Clinical skill: {s.name}", "url": f"{REPO}/blob/main/skills/{s.name}/SKILL.md"}))
    for name, fn in ALL_CALCULATORS.items():
        score = 3 * _score(terms, name.replace("_", " ") + " " + (fn.__doc__ or ""))
        if score:
            scored.append((score, {"id": f"calc:{name}", "title": f"Calculator: {name}", "url": f"{REPO}#tools"}))
    results = [r for _, r in sorted(scored, key=lambda x: -x[0])][: limit - 3]
    try:
        pub = await sources.pubmed_search(query, max_results=3, reviews_and_trials_only=True)
        results += [{"id": f"pubmed:{a['pmid']}", "title": a["title"] or a["pmid"], "url": a["url"]} for a in pub["articles"]]
    except Exception as exc:  # literature search is best-effort
        log.info("pubmed search unavailable: %s", exc)
    return results


async def _fetch(id: str) -> dict[str, Any]:
    kind, _, key = id.partition(":")
    if kind == "skill":
        skills = load_skills()
        if key not in skills:
            raise ToolError(f"Unknown skill id {id}")
        s = skills[key]
        return {"id": id, "title": f"Clinical skill: {s.name}", "text": s.full_text, "url": f"{REPO}/blob/main/skills/{s.name}/SKILL.md", "metadata": {"type": "skill"}}
    if kind == "calc":
        fn = ALL_CALCULATORS.get(key)
        if not fn:
            raise ToolError(f"Unknown calculator id {id}")
        import inspect

        text = f"Tool name: calc_{key}\nSignature: {key}{inspect.signature(fn)}\n\n{(fn.__doc__ or '').strip()}"
        return {"id": id, "title": f"Calculator: {key}", "text": text, "url": f"{REPO}#tools", "metadata": {"type": "calculator", "tool": f"calc_{key}"}}
    if kind == "pubmed":
        a = await sources.pubmed_abstract(key)
        return {"id": id, "title": f"PubMed {key}", "text": a["abstract"], "url": a["url"], "metadata": {"type": "pubmed"}}
    raise ToolError("id must start with skill:, calc: or pubmed:")


# Default server (no auth): used by stdio, tests, and in-process embedding.
mcp = build_server()
