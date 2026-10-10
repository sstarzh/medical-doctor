"""Machine-readable consult notes.

The attending writes its Markdown note and ends it with a fenced ```json block holding a
`ConsultSummary`. We split that block out, validate it, and return it as `structured` next to the
Markdown, so calling agents can route on urgency, list can't-miss diagnoses, or file drafts without
parsing prose. If the model omits or garbles the block we fall back to what can be read reliably
from the Markdown (urgency line, PMIDs), and say so.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Urgency = Literal["emergency", "urgent", "soon", "routine"]
URGENCY_RANK = {"routine": 0, "soon": 1, "urgent": 2, "emergency": 3}


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class Diagnosis(_M):
    diagnosis: str = Field(max_length=300)
    likelihood: Literal["likely", "possible", "unlikely"] = "possible"
    probability_pct: float | None = Field(default=None, ge=0, le=100)
    cant_miss: bool = False
    key_for: str | None = Field(default=None, max_length=1000)
    key_against: str | None = Field(default=None, max_length=1000)


class Citation(_M):
    label: str = Field(max_length=500)  # e.g. "IDSA/ATS CAP guideline 2019"
    pmid: str | None = Field(default=None, pattern=r"^\d{1,9}$")
    url: str | None = Field(default=None, max_length=1000)
    supports: str | None = Field(default=None, max_length=500)  # the claim it is cited for


class ConsultSummary(_M):
    urgency: Urgency
    urgency_reason: str = Field(default="", max_length=1000)
    immediate_action: str | None = Field(default=None, max_length=1000)
    problem_representation: str = Field(default="", max_length=2000)
    differential: list[Diagnosis] = Field(default_factory=list, max_length=25)
    recommended_evaluation: list[str] = Field(default_factory=list, max_length=40)
    management: list[str] = Field(default_factory=list, max_length=40)
    drafts: list[str] = Field(default_factory=list, max_length=20)  # prescription/referral drafts proposed
    missing_information: list[str] = Field(default_factory=list, max_length=30)
    patient_explanation: str = Field(default="", max_length=5000)
    return_precautions: list[str] = Field(default_factory=list, max_length=20)
    citations: list[Citation] = Field(default_factory=list, max_length=40)
    patient_facts: list[dict[str, Any]] = Field(default_factory=list, max_length=40)  # see OUTPUT_CONTRACT
    patient_is_user: bool | None = None  # is the case about the person talking (vs a child, parent, friend)?


OUTPUT_CONTRACT = """\
Machine-readable summary
After the closing line, append ONE fenced ```json block (nothing after it) with this shape — same \
content as the note, no new claims:
{"urgency": "emergency|urgent|soon|routine", "urgency_reason": "...", "immediate_action": "...",
 "problem_representation": "...",
 "differential": [{"diagnosis": "...", "likelihood": "likely|possible|unlikely", "probability_pct": 40,
                   "cant_miss": false, "key_for": "...", "key_against": "..."}],
 "recommended_evaluation": ["..."], "management": ["..."], "drafts": ["..."],
 "missing_information": ["..."], "patient_explanation": "...", "return_precautions": ["..."],
 "citations": [{"label": "...", "pmid": "12345678", "url": "...", "supports": "the claim it backs"}],
 "patient_is_user": true,
 "patient_facts": [{"section": "profile|conditions|medications|allergies|procedures|family_history|observations|social_history|immunizations",
                    "data": {...chart fields, e.g. {"name": "lisinopril", "dose": "10 mg", "frequency": "daily"}}}]}
Only list PMIDs/URLs you actually retrieved with a tool this consult. patient_facts holds only facts the case
STATES about the patient (history, medicines, allergies, measurements) and that are not already in the
<patient_chart>; never diagnoses you are proposing. Include every stated fact (age or date of birth, sex,
weight, height, conditions, medicines with doses, allergies with reactions, surgeries, family history, vitals and
results with dates, smoking/alcohol). patient_is_user is false when the case is about someone other than the
person writing (e.g. "my son")."""

_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```\s*$", re.S)
_URGENCY_LINE = re.compile(r"##\s*Urgency\s*\n+\s*\**\s*(EMERGENCY|URGENT|SOON|ROUTINE)", re.I)


def split_note(note: str) -> tuple[str, dict[str, Any] | None]:
    """Return (markdown without the trailing JSON block, parsed JSON or None)."""
    m = _FENCE.search(note or "")
    if not m:
        return note, None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return note, None
    return note[: m.start()].rstrip(), data if isinstance(data, dict) else None


def structure(note: str, triage: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """Split and validate the summary; reconcile urgency with the deterministic red-flag screen."""
    markdown, raw = split_note(note)
    out: dict[str, Any] = {"source": "model"}
    summary: dict[str, Any] | None = None
    if raw is not None:
        try:
            summary = ConsultSummary.model_validate(raw).model_dump(mode="json", exclude_none=True)
        except ValidationError as exc:
            out["parse_error"] = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5])
    else:
        out["parse_error"] = "the note had no machine-readable summary block"
    if summary is None:
        m = _URGENCY_LINE.search(markdown)
        summary = {"urgency": m.group(1).lower() if m else None}
        out["source"] = "markdown_fallback"
    out.update(summary)

    screen = (triage or {}).get("urgency")
    model_rank = URGENCY_RANK.get(out.get("urgency") or "", -1)
    if screen in URGENCY_RANK and URGENCY_RANK[screen] > model_rank:
        # The deterministic screen is a floor for routing: agents acting on `urgency` must never get
        # less than it. The model's own call stays visible for the clinician.
        out["model_urgency"] = out.get("urgency")
        out["urgency"] = screen
        out["urgency_overridden_by"] = "red_flag_screen"
    return markdown, out


def merge_reported_pmids(structured: dict[str, Any], markdown: str) -> list[str]:
    """PMIDs cited anywhere in the note (body or summary), de-duplicated in order."""
    found = re.findall(r"PMID[:\s#]*?(\d{5,9})", markdown, re.I)
    found += [c["pmid"] for c in structured.get("citations", []) if c.get("pmid")]
    return list(dict.fromkeys(found))
