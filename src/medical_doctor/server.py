"""MCP server: 'Dr. MCP' — attending-grade clinical decision support."""

from __future__ import annotations

import json
import logging
import time
import re
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
from pydantic import Field

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import TextContent, ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import __version__, calculators, consult_note, pediatric, physician, safety, sources
from .config import Settings
from .identity import AuthRequired, current_principal
from .records_tools import RECORD_TOOLS, Records, register_order_tools, register_record_tools
from .observability import ToolTimingMiddleware, consult_span, record_consult
from .skills import freshness as skill_freshness, load_skills

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
5. Personal doctor: the user can keep an encrypted chart for themselves and family members
   (`patient_list`, `patient_create`, `patient_chart`, `patient_update`, or `patient_import_fhir` for records
   exported from a hospital or health app). Pass `patient_id` to `consult`
   so the attending sees the history. Use the `personal_doctor_visit` / `patient_intake` prompts.
6. Treatment and referrals are DRAFTS for a licensed clinician (`draft_prescription`, `draft_referral`),
   presented as calls to action — never as prescriptions or orders.

{DISCLAIMER}
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
READ_ONLY_WEB = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

# Tools that the inner physician model must not call: recursion, meta tools, and anything that
# reads or writes the patient record (the consult gets the chart summary explicitly instead).
_NOT_FOR_PHYSICIAN = {"consult", "list_skills", "get_skill", "search", "fetch", "whoami", "care_plan_update"} | RECORD_TOOLS
MAX_TOOL_OUTPUT_CHARS = 12_000
MAX_CASE_CHARS = 50_000

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

    async def check(self, key: str) -> None:
        if self.per_hour <= 0:
            return
        if self.store is None:
            from .auth.store import Store

            self.store = Store()
        if not await anyio.to_thread.run_sync(self.store.hit, f"consult:{key}", self.per_hour, 3600):
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


def open_records(settings: Settings, *, local_mode: bool, store: Any = None) -> Records:
    """Build the encrypted patient-record service (or explain why it is disabled)."""
    from .records.crypto import Cipher, DataKeyError, local_key_file
    from .records.repo import AsyncRecordRepo, RecordRepo
    from .records.service import PatientService

    if settings.auth_mode == "none" and not local_mode:
        return Records(None, local_mode, "Patient records need sign-in: enable MEDICAL_DOCTOR_AUTH on network deployments.")
    persistent = bool(settings.database_url or settings.db_path)
    try:
        if settings.data_keys:
            kek = Cipher.from_spec(settings.data_keys)
        elif not persistent:
            kek = Cipher.ephemeral()  # in-memory database (tests, demos): nothing outlives the process
        elif local_mode:
            kek = Cipher.from_spec(local_key_file(Path(settings.db_path or "~/.medical-doctor/x").expanduser().parent / "data.key"))
        else:
            return Records(None, local_mode, "Patient records are disabled: set MEDICAL_DOCTOR_DATA_KEYS (medical-doctor-mcp gen-data-key).")
    except DataKeyError as exc:
        return Records(None, local_mode, f"Patient records are disabled: {exc}")
    if settings.database_url:
        pool = getattr(store, "pool", None)
        if pool is None:
            from .auth.pgstore import PostgresStore

            pool = PostgresStore(settings.database_url).pool
        repo = RecordRepo(kek, pg_pool=pool)
    else:
        path = str(Path(settings.db_path).expanduser().with_name("records.db")) if settings.db_path else ":memory:"
        repo = RecordRepo(kek, sqlite_path=path)
    return Records(PatientService(AsyncRecordRepo(repo)), local_mode)


def build_server(
    settings: Settings | None = None,
    *,
    store: Any = None,
    http: Any = None,
    local_mode: bool = False,
    records: Records | None = None,
) -> MCPServer:
    settings = settings or Settings()
    if store is None and (settings.auth_mode == "oauth" or settings.consult_limit_per_hour > 0):
        from .auth.store import open_store

        store = open_store(settings)
    if records is None:
        records = open_records(settings, local_mode=local_mode, store=store)
    kwargs = auth_kwargs(settings, store, http)
    mcp = MCPServer(
        name="medical-doctor",
        title="Medical Doctor (Attending Consult)",
        instructions=INSTRUCTIONS,
        version=__version__,
        website_url=settings.public_url or None,
        **kwargs,
    )
    mcp.middleware.append(ToolTimingMiddleware())
    limiter = RateLimiter(settings.consult_limit_per_hour, store)
    _register_tools(mcp, limiter, settings, records)
    register_record_tools(mcp, records)
    register_order_tools(mcp, records)
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


def _register_tools(mcp: MCPServer, limiter: RateLimiter, settings: Settings, records: Records) -> None:
    # --- Safety ----------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY, title="Emergency red-flag screen")
    async def red_flag_screen(text: Annotated[str, Field(max_length=MAX_CASE_CHARS)]) -> dict[str, Any]:
        """Deterministic emergency red-flag screen of free-text symptoms (stroke, ACS, cardiac arrest, sepsis,
        anaphylaxis, overdose, suicide risk, obstetric and pediatric emergencies, etc.). Run this FIRST on any
        patient narrative. Returns urgency (emergency|urgent|routine), matched red flags, and the immediate action."""
        return (await anyio.to_thread.run_sync(safety.screen, text)).to_dict()

    # --- Consult ---------------------------------------------------------

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=True),
        title="Attending physician consult",
    )
    async def consult(
        case: Annotated[str, Field(max_length=MAX_CASE_CHARS)],
        ctx: Context,
        question: Annotated[str | None, Field(max_length=5_000)] = None,
        audience: Literal["clinician", "patient", "caregiver"] = "clinician",
        patient_id: Annotated[str | None, Field(max_length=40, pattern=r"^pt_[A-Za-z0-9_-]+$")] = None,
        provider: Literal["anthropic", "openai", "meta", "llama"] | None = None,
        second_opinion: bool = False,
    ) -> dict[str, Any]:
        """Full attending-physician consult for adults AND children (Med-Peds attending: internal medicine +
        pediatrics, incl. neonates and adolescents). Give the whole case: age (days/months for infants), weight
        for children, sex, pregnancy status, chief complaint, history of present illness, past history,
        medications, allergies, vitals, exam, labs/imaging.
        Returns a structured consult note: urgency, problem representation, ranked differential with
        can't-miss diagnoses, recommended workup, evidence-cited management, and a patient explanation.
        audience='patient' tailors language for a layperson; 'caregiver' for a parent of a sick child.
        patient_id: include that patient's encrypted chart (history, medicines, allergies, results) — the
        personal-doctor mode. provider: which model family runs the consult (anthropic|openai|meta|llama; default
        is the server's). second_opinion=true runs the case past every configured provider and reconciles
        their notes, listing disagreements. Takes 30 s to a few minutes.
        Result: `note` (Markdown), `structured` (urgency — never below the red-flag screen —, differential with
        can't-miss flags, evaluation, management, drafts, missing information, citations) and `citation_check`
        (each PMID looked up in PubMed: verified / mismatch / not_found / unchecked)."""
        try:
            who = current_principal(records.local_mode or settings.auth_mode == "none").owner
        except AuthRequired:
            who = "anonymous"
        user = current_user()
        triage = (await anyio.to_thread.run_sync(safety.screen, " ".join(filter(None, [case, question])))).to_dict()
        # Audit who/when/urgency only — never the clinical text (PHI).
        audit.info("consult owner=%s client=%s urgency=%s chart=%s", who, user.get("client_id"), triage["urgency"], bool(patient_id))
        chosen = provider or settings.llm_provider
        if not physician.credentials_for(chosen):
            return {
                "ok": False,
                "red_flag_screen": triage,
                "error": f"No {chosen} credentials on the server. Falling back to protocol mode.",
                "protocol": physician.CHARTER,
                "how_to_proceed": "Act as the attending yourself: follow `protocol`, load relevant skills with "
                "get_skill, and call this server's calculator/drug/PubMed tools to verify every number and claim.",
                "disclaimer": DISCLAIMER,
            }
        chart_summary = None
        if patient_id:
            from .records.service import summarize

            chart_summary = summarize(await records.chart(patient_id, purpose="consult"))
        await limiter.check(who)

        async def progress(step: int, message: str) -> None:
            try:
                await ctx.report_progress(step, None, message)
            except Exception:  # progress is best-effort (client may not send a progress token)
                pass

        engine = physician.make_engine(settings, chosen)
        started = time.monotonic()
        with consult_span(engine.provider, engine.model, **{"medical_doctor.chart": bool(patient_id),
                                                             "medical_doctor.second_opinion": second_opinion}) as span:
            result = await physician.run_consult(
                case=case,
                question=question,
                audience=audience,
                triage=triage,
                tools=await physician_tools(),
                execute_tool=execute_for_physician,
                settings=settings,
                chart_summary=chart_summary,
                progress=progress,
                engine=engine,
                second_opinion=second_opinion,
            )
            record_consult(span, result, time.monotonic() - started)
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
        if name in ("draft_prescription", "draft_referral"):
            arguments = {**arguments, "save": False}  # the model may draft, only the user saves to the chart
        result = await mcp.call_tool(name, arguments)
        if getattr(result, "structured_content", None):
            text = json.dumps(result.structured_content)
        else:
            text = "\n".join(c.text for c in getattr(result, "content", []) if isinstance(c, TextContent))
        if getattr(result, "is_error", False):
            raise ToolError(text[:2_000])
        if len(text) > MAX_TOOL_OUTPUT_CHARS:
            text = text[:MAX_TOOL_OUTPUT_CHARS] + " …[truncated]"
        # Third-party content (FDA labels, abstracts) is evidence, never instructions.
        text = text.replace("</tool_output>", "</ tool_output>")
        return f'<tool_output tool="{name}" trust="untrusted">\n{text}\n</tool_output>'


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
    async def drug_interaction_check(
        drugs: Annotated[list[Annotated[str, Field(max_length=120)]], Field(min_length=2, max_length=sources.MAX_INTERACTION_DRUGS)],
    ) -> dict[str, Any]:
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

    @mcp.tool(annotations=READ_ONLY_WEB, title="Verify citations")
    async def verify_citations(text: Annotated[str, Field(max_length=MAX_CASE_CHARS)]) -> dict[str, Any]:
        """Check the references in a clinical text (e.g. a consult note you or another model wrote): every
        PMID is looked up in PubMed and marked verified / mismatch (real article on a different topic —
        likely a wrong or invented ID) / not_found / unchecked; links are marked by whether they point to a
        recognised guideline or journal publisher. Use before relying on a model-written reference."""
        from . import citations

        markdown, structured = consult_note.structure(text)
        return await citations.verify(markdown, structured)

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
    def list_skills() -> list[dict[str, Any]]:
        """List the clinical skills (specialty playbooks) on this server, with review freshness:
        status current / review_due, last_reviewed date, clinical_review (pending|approved), and cited
        guideline editions older than 5 years that should be checked for an update."""
        out = []
        for s in load_skills().values():
            f = skill_freshness(s)
            out.append({"name": s.name, "description": s.description, "freshness": {k: v for k, v in f.items() if k != "skill"}})
        return out

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

    @mcp.prompt(name="personal_doctor_visit", description="A visit with your personal doctor: chart review, focused history, updates, plan and calls to action.")
    def personal_doctor_visit_prompt(patient_id: str, concern: str = "") -> str:
        return (
            "You are this patient's personal doctor. Load the `personal-doctor` and `prescribing-and-referral` skills "
            "with get_skill. 1) red_flag_screen the concern. 2) patient_chart (summary_only=false) and review it: "
            "allergies, medicines, problems, recent results, open care-plan items. 3) Ask focused follow-up questions "
            "a few at a time. 4) Record confirmed new facts with patient_update and add a short visit note. "
            "5) consult with patient_id for the assessment. 6) For treatment or specialist care, create drafts with "
            "draft_prescription / draft_referral (patient_id, save=true) and present them as calls to action for a "
            "licensed clinician. 7) Close with safety-net advice and preventive_care_due items that are due.\n\n"
            f"<patient_id>{patient_id}</patient_id>\n<concern>\n{concern}\n</concern>"
        )

    @mcp.prompt(name="patient_intake", description="Build a new patient chart (you or a family member) through a guided intake interview.")
    def patient_intake_prompt(who: str = "myself") -> str:
        return (
            "Run a warm, efficient new-patient intake following the `personal-doctor` skill (get_skill). "
            "Create the profile with patient_create, then collect allergies (with reactions), medicines, conditions, "
            "surgeries, family history, social history, immunizations and recent results, a few questions at a time, "
            "saving each confirmed answer with patient_update. Explain that the chart is encrypted, private to this "
            "account, and can be exported or erased at any time. Finish with preventive_care_due.\n\n"
            f"<patient>{who}</patient>"
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


# Default in-process server (no auth, single local user, in-memory records): tests and embedding.
# The CLI builds its own from environment settings.
mcp = build_server(local_mode=True)
