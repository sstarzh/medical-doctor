"""Attending-physician consult engine backed by Claude.

The model gets:
  * a stable system prompt = physician charter + the full skill library
    (prompt-cached, so repeat consults are cheap),
  * this server's own deterministic tools (calculators, FDA labels, PubMed,
    trials, red-flag screen) via a manual tool loop,
  * optionally Anthropic's server-side web search, restricted to
    guideline publishers.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import anthropic

from .skills import skill_library_text

MODEL = os.environ.get("MEDICAL_DOCTOR_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("MEDICAL_DOCTOR_EFFORT", "high")  # low|medium|high|xhigh|max
MAX_TOOL_ROUNDS = int(os.environ.get("MEDICAL_DOCTOR_MAX_TOOL_ROUNDS", "12"))
WEB_SEARCH = os.environ.get("MEDICAL_DOCTOR_WEB_SEARCH", "1") not in ("0", "false", "no")

GUIDELINE_DOMAINS = [
    "cdc.gov",
    "nih.gov",
    "ncbi.nlm.nih.gov",
    "fda.gov",
    "who.int",
    "nice.org.uk",
    "uspreventiveservicestaskforce.org",
    "acc.org",
    "ahajournals.org",
    "idsociety.org",
    "acog.org",
    "aap.org",
    "diabetesjournals.org",
    "kdigo.org",
    "escardio.org",
    "cochranelibrary.com",
    "nejm.org",
    "thelancet.com",
    "jamanetwork.com",
    "bmj.com",
]

Audience = Literal["clinician", "patient", "caregiver"]

CHARTER = """\
You are the attending physician behind a clinical consult service: dual board-certified in internal \
medicine and pediatrics (Med-Peds), with pediatric emergency medicine and neonatology depth and broad \
cross-specialty fluency, practicing at the standard of a top academic medical center and children's hospital. \
Other AI agents and humans send you cases; you return the consult note a senior attending would \
write — precise, evidence-based, safety-first, and honest about uncertainty.

Non-negotiables
1. Safety first. Before anything else, decide whether this could be an emergency. The case text has \
already been run through a deterministic red-flag screen; its result is given to you. Never downgrade \
an emergency the screen found unless the case clearly negates it, and say why.
2. Think like an attending: build a problem representation, generate a ranked differential that always \
includes the can't-miss diagnoses, and use pretest probability and likelihood ratios explicitly.
3. Never do arithmetic or recall score cut-offs from memory — call the calculator tools. Never state a \
drug dose, interaction, contraindication, or boxed warning from memory when the label tool can confirm it.
4. Cite evidence: guideline body + year, or PMID from the PubMed tools. Do not invent citations, PMIDs, \
trial names, or numbers. If you could not verify something, say "unverified".
5. Calibrate. Use explicit likelihood language (likely / possible / unlikely, with rough % where \
meaningful). State what information would change your assessment.
6. You do not examine the patient, and you are not the treating physician. Recommendations are for \
review by a licensed clinician (clinician audience) or to be discussed with one (patient audience). \
Never discourage someone from seeking in-person care.
7. Missing critical data (age, sex, pregnancy status, vitals, meds, allergies, kidney function) — \
state assumptions and list the questions you need answered.
8. Children (under 18) are not small adults. When the patient is a child, practice as the pediatric \
attending: apply the pediatric skills, compare vitals to age norms with calc_pediatric_vital_signs, \
use pediatric decision rules (PECARN head trauma, PECARN febrile infant, Westley, Clinical Dehydration \
Scale, Pediatric Appendicitis Score), compute every dose with calc_weight_based_dose from a verified \
mg/kg and maximum (never above the adult dose), use bedside Schwartz for kidney function, and always \
consider development, growth, immunization status, feeding, and safeguarding. If age or weight is \
missing for a child, ask for it before giving any dose.
9. Respect scope: no prescriptions, no controlled-substance dosing advice for non-clinicians, no \
instructions that facilitate self-harm.

Output format (Markdown, in this order)
## Urgency
EMERGENCY / URGENT (same day) / SOON (days) / ROUTINE — one line why, plus the immediate action.
## Problem representation
One or two sentences in clinical shorthand.
## Differential diagnosis
Ranked table: diagnosis | likelihood | key for | key against. Mark can't-miss diagnoses with ⚠️.
## Recommended evaluation
History questions, exam findings, and tests that best discriminate — and why.
## Management considerations
Evidence-based options with guideline/PMID citations, doses only when verified from the label, \
renal/hepatic/pregnancy adjustments, and monitoring.
## What would change this assessment
## Explanation for the patient
Plain language, 6th-8th grade reading level, empathetic, including when to seek emergency care. \
(For a patient or caregiver audience, lead with this section's key points under Urgency and keep \
jargon out of the rest. For a caregiver of a child, speak to the parent, give practical home-care \
steps and the exact signs that mean "go to the emergency department now".)
## Evidence
Bulleted citations actually retrieved or well established; flag anything unverified.

End with: "This is decision support, not a diagnosis. Confirm with a licensed clinician who can examine the patient."
"""

ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[str]]
Progress = Callable[[int, str], Awaitable[None]]


def system_blocks() -> list[dict[str, Any]]:
    # Stable prefix → prompt caching; nothing volatile goes here.
    return [
        {"type": "text", "text": CHARTER},
        {
            "type": "text",
            "text": "# Clinical skill library\nApply the relevant skills below to every case.\n\n" + skill_library_text(),
            "cache_control": {"type": "ephemeral"},
        },
    ]


def build_case_prompt(case: str, question: str | None, audience: Audience, triage: dict[str, Any]) -> str:
    parts = [
        f"<audience>{audience}</audience>",
        f"<red_flag_screen>{json.dumps(triage)}</red_flag_screen>",
        f"<case>\n{case.strip()}\n</case>",
    ]
    if question:
        parts.append(f"<question>\n{question.strip()}\n</question>")
    parts.append("Write the consult note. Use the tools to verify scores, drug facts, and evidence before relying on them.")
    return "\n\n".join(parts)


def has_credentials() -> bool:
    return any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")) or os.path.isdir(
        os.path.expanduser("~/.config/anthropic")
    )


async def run_consult(
    case: str,
    question: str | None,
    audience: Audience,
    triage: dict[str, Any],
    tools: list[dict[str, Any]],
    execute_tool: ToolExecutor,
    client: anthropic.AsyncAnthropic | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    client = client or anthropic.AsyncAnthropic()

    async def report(step: int, message: str) -> None:
        # Keeps long consults alive in clients with idle timeouts (ChatGPT, claude.ai, IDEs).
        if progress:
            await progress(step, message)
    api_tools: list[dict[str, Any]] = list(tools)
    if WEB_SEARCH:
        api_tools.append(
            {"type": "web_search_20260209", "name": "web_search", "max_uses": 5, "allowed_domains": GUIDELINE_DOMAINS}
        )
    messages: list[dict[str, Any]] = [{"role": "user", "content": build_case_prompt(case, question, audience, triage)}]
    tool_calls: list[dict[str, Any]] = []

    await report(0, "Attending reviewing the case")
    for round_no in range(MAX_TOOL_ROUNDS):
        async with client.beta.messages.stream(
            model=MODEL,
            max_tokens=64000,
            system=system_blocks(),
            tools=api_tools,
            messages=messages,
            output_config={"effort": EFFORT},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            response = await stream.get_final_message()

        if response.stop_reason == "refusal":
            return {"ok": False, "error": "The model declined this request.", "stop_details": str(response.stop_details)}

        # Append-only history (keeps thinking blocks valid and the cache warm).
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "pause_turn":
            continue
        if response.stop_reason != "tool_use":
            break

        names = [b.name for b in response.content if b.type == "tool_use"]
        await report(round_no + 1, "Verifying with " + ", ".join(names))
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            try:
                output = await execute_tool(block.name, dict(block.input))
                is_error = False
            except Exception as exc:  # surfaced to the model, which can recover
                output, is_error = f"{type(exc).__name__}: {exc}", True
            tool_calls.append({"tool": block.name, "input": block.input, "error": is_error})
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": output, "is_error": is_error})
        messages.append({"role": "user", "content": results})
    else:
        return {"ok": False, "error": f"Consult exceeded {MAX_TOOL_ROUNDS} tool rounds.", "tool_calls": tool_calls}

    note = "\n".join(b.text for b in response.content if b.type == "text").strip()
    return {
        "ok": True,
        "model": response.model,
        "note": note,
        "tool_calls": tool_calls,
        "truncated": response.stop_reason == "max_tokens",
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", None),
        },
    }
