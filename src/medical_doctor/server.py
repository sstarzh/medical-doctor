"""MCP server: 'Dr. MCP' — attending-grade clinical decision support."""

from __future__ import annotations

import json
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import TextContent, ToolAnnotations

from . import calculators, physician, safety, sources
from .skills import load_skills

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
   If `consult` reports no model credentials, follow the returned `protocol` yourself, using the
   calculator, drug-label, PubMed and trials tools here, and the skills (`list_skills`/`get_skill`).
3. Never compute clinical scores or quote drug doses/interactions from memory: use the tools.

{DISCLAIMER}
"""

mcp = MCPServer(name="medical-doctor", title="Medical Doctor (Attending Consult)", instructions=INSTRUCTIONS, version="0.1.0")

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
READ_ONLY_WEB = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)

# Tools that the inner physician model must not call (recursion / meta tools).
_NOT_FOR_PHYSICIAN = {"consult", "list_skills", "get_skill"}


# --- Safety --------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
def red_flag_screen(text: str) -> dict[str, Any]:
    """Deterministic emergency red-flag screen of free-text symptoms (stroke, ACS, sepsis, anaphylaxis,
    suicide risk, obstetric and pediatric emergencies, etc.). Run this FIRST on any patient narrative.
    Returns urgency (emergency|urgent|routine), matched red flags, and the immediate action to take."""
    return safety.screen(text).to_dict()


# --- Consult -------------------------------------------------------------


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def consult(
    case: str,
    question: str | None = None,
    audience: Literal["clinician", "patient"] = "clinician",
) -> dict[str, Any]:
    """Full attending-physician consult. Give the whole case: age, sex, pregnancy status, chief complaint,
    history of present illness, past history, medications, allergies, vitals, exam, labs/imaging.
    Returns a structured consult note: urgency, problem representation, ranked differential with
    can't-miss diagnoses, recommended workup, evidence-cited management, and a patient explanation.
    audience='patient' tailors language for a layperson."""
    triage = safety.screen(" ".join(filter(None, [case, question]))).to_dict()
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
    result = await physician.run_consult(
        case=case,
        question=question,
        audience=audience,
        triage=triage,
        tools=await _physician_tools(),
        execute_tool=_execute_for_physician,
    )
    result["red_flag_screen"] = triage
    result["disclaimer"] = DISCLAIMER
    return result


async def _physician_tools() -> list[dict[str, Any]]:
    tools = []
    for t in await mcp.list_tools():
        if t.name in _NOT_FOR_PHYSICIAN:
            continue
        tools.append({"name": t.name, "description": t.description or "", "input_schema": t.input_schema})
    return tools


async def _execute_for_physician(name: str, arguments: dict[str, Any]) -> str:
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


# --- Calculators ---------------------------------------------------------

for _name, _fn in calculators.CALCULATORS.items():
    mcp.add_tool(
        _fn,
        name=f"calc_{_name}",
        description=f"Clinical calculator: {(_fn.__doc__ or _name).strip()} Returns value, interpretation and reference.",
        annotations=READ_ONLY,
    )


# --- Drugs & evidence ----------------------------------------------------


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


# --- Skills (as tools, resources and prompts) ----------------------------


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


@mcp.resource("skill://{name}", name="clinical-skill", description="Clinical skill playbook (SKILL.md)", mime_type="text/markdown")
def skill_resource(name: str) -> str:
    return get_skill(name)


@mcp.prompt(name="attending_consult", description="Run a structured attending-physician consult on a case using this server's tools.")
def attending_consult_prompt(case: str, audience: str = "clinician") -> str:
    return (
        f"{physician.CHARTER}\n\nAudience: {audience}\n\n"
        "First call red_flag_screen on the case. Then reason through it, calling the calculator, drug_label, "
        "drug_interaction_check and pubmed_search tools to verify every score, dose and claim, and get_skill for "
        "the relevant specialty playbooks.\n\n"
        f"<case>\n{case}\n</case>"
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
