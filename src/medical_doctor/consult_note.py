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

from .cant_miss import flag as flag_cant_miss

Urgency = Literal["emergency", "urgent", "soon", "routine"]
URGENCY_RANK = {"routine": 0, "soon": 1, "urgent": 2, "emergency": 3}


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class Diagnosis(_M):
    diagnosis: str = Field(min_length=1, max_length=300)
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
After the closing line, append ONE fenced ```json block as the very last thing in your reply (valid JSON:
no comments, no trailing commas; use exactly the enum values shown) with this shape — same \
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
Set "cant_miss": true on EVERY dangerous diagnosis you list, including unlikely ones (e.g. aortic dissection or
PE in chest pain, meningitis and HSV in a febrile neonate, septic arthritis in a hot joint).
Only list PMIDs/URLs you actually retrieved with a tool this consult. patient_facts holds only facts the case
STATES about the patient (history, medicines, allergies, measurements) and that are not already in the
<patient_chart>; never diagnoses you are proposing. Include every stated fact (age or date of birth, sex,
weight, height, conditions, medicines with doses, allergies with reactions, surgeries, family history, vitals and
results with dates, smoking/alcohol). patient_is_user is false when the case is about someone other than the
person writing (e.g. "my son")."""

_FENCE = re.compile(r"```[ \t]*(?:json|JSON)?[ \t]*\n?(\{.*?\})\s*```", re.S)
_URGENCY_LINE = re.compile(r"##\s*Urgency\s*\n+\s*\**\s*(EMERGENCY|URGENT|SOON|ROUTINE)", re.I)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def split_note(note: str) -> tuple[str, dict[str, Any] | None]:
    """Return (markdown without the JSON summary block, parsed JSON or None).

    Tolerant by design (measured on live consults): the block may be followed by a closing line, may sit
    anywhere in the note, and may contain trailing commas. The LAST fenced JSON object in the note wins."""
    note = note or ""
    matches = list(_FENCE.finditer(note))
    for m in reversed(matches):
        raw = m.group(1)
        for candidate in (raw, _TRAILING_COMMA.sub(r"\1", raw)):
            try:
                data = json.loads(candidate)
            except ValueError:
                continue
            if isinstance(data, dict):
                markdown = (note[: m.start()].rstrip() + "\n\n" + note[m.end():].strip()).strip()
                return markdown, data
    return note, None


_URGENCY_WORDS = {"emergency": "emergency", "emergent": "emergency", "urgent": "urgent", "same day": "urgent",
                  "soon": "soon", "days": "soon", "routine": "routine", "non-urgent": "routine", "nonurgent": "routine"}
_LIKELIHOOD = {"likely": "likely", "high": "likely", "probable": "likely", "most likely": "likely", "very likely": "likely",
               "possible": "possible", "moderate": "possible", "medium": "possible", "intermediate": "possible",
               "unlikely": "unlikely", "low": "unlikely", "less likely": "unlikely", "rare": "unlikely", "very low": "unlikely"}


def _urgency(value: Any) -> str | None:
    text = str(value or "").lower()
    # The earliest urgency word is the call: "ROUTINE: ... go to the emergency department if worse" is routine.
    hits = [(text.find(word), mapped) for word, mapped in _URGENCY_WORDS.items() if word in text]
    return min(hits)[1] if hits else None


def _num(value: Any) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", str(value)) if value is not None else None
    return float(m.group()) if m and 0 <= float(m.group()) <= 100 else None


def _salvage(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Coerce the common near-misses models produce and keep every field that can be kept."""
    notes: list[str] = []
    data = dict(raw)
    # Always normalize: a list/dict urgency would otherwise reach a set lookup unhashed. None falls back
    # to the note's "## Urgency" line.
    data["urgency"] = _urgency(data.get("urgency"))
    def as_list(value: Any) -> list[Any]:  # a lone string/object is one item, never one item per character
        return [value] if isinstance(value, (str, dict)) else list(value) if isinstance(value, list) else []

    ddx = []
    for d in as_list(data.get("differential")):
        if isinstance(d, str):
            d = {"diagnosis": d}
        if not isinstance(d, dict):
            continue
        d = dict(d)
        d.setdefault("diagnosis", d.pop("name", None) or d.pop("dx", None))
        lk = _LIKELIHOOD.get(str(d.get("likelihood", "")).strip().lower())
        if lk:
            d["likelihood"] = lk
        elif "likelihood" in d:
            d.pop("likelihood")
        if "probability_pct" in d:
            d["probability_pct"] = _num(d["probability_pct"])
        if isinstance(d.get("cant_miss"), str):
            d["cant_miss"] = d["cant_miss"].strip().lower() in ("true", "yes", "y", "1")
        try:
            ddx.append(Diagnosis.model_validate(d).model_dump(mode="json", exclude_none=True))
        except ValidationError:
            notes.append(f"dropped differential item {str(d.get('diagnosis'))[:40]!r}")
    if "differential" in data:
        data["differential"] = ddx[:25]
    cites = []
    for c in as_list(data.get("citations")):
        if isinstance(c, str):
            m = re.search(r"PMID\D{0,3}(\d{5,9})", c, re.I)
            c = {"label": c, "pmid": m.group(1) if m else None}
        if isinstance(c, dict):
            c = {**c, "pmid": (str(c["pmid"]).strip() or None) if c.get("pmid") is not None else None}
            if c.get("pmid") and not str(c["pmid"]).isdigit():
                c["pmid"] = re.sub(r"\D", "", str(c["pmid"])) or None
            try:
                cites.append(Citation.model_validate({**c, "label": str(c.get("label") or c.get("title") or "citation")[:500]})
                             .model_dump(mode="json", exclude_none=True))
            except ValidationError:
                notes.append("dropped a citation")
    if "citations" in data:
        data["citations"] = cites[:40]
    for key, limit in (("recommended_evaluation", 40), ("management", 40), ("drafts", 20),
                       ("missing_information", 30), ("return_precautions", 20)):  # the schema's own limits
        v = data.get(key)
        if isinstance(v, str):
            data[key] = [v]
        elif isinstance(v, list):
            data[key] = [x if isinstance(x, str) else json.dumps(x) for x in v][:limit]
    for key, limit in (("urgency_reason", 1000), ("immediate_action", 1000), ("problem_representation", 2000),
                       ("patient_explanation", 5000)):
        if isinstance(data.get(key), str):
            data[key] = data[key][:limit]
        elif key in data and data[key] is not None:
            data[key] = str(data[key])[:limit]
    if isinstance(data.get("patient_facts"), list):
        data["patient_facts"] = [f for f in data["patient_facts"] if isinstance(f, dict)][:40]
    else:
        data.pop("patient_facts", None)
    if isinstance(data.get("patient_is_user"), str):
        data["patient_is_user"] = data["patient_is_user"].strip().lower() in ("true", "yes")
    return data, notes


def structure(note: str, triage: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """Split and validate the summary; reconcile urgency with the deterministic red-flag screen."""
    markdown, raw = split_note(note)
    out: dict[str, Any] = {"source": "model"}
    summary: dict[str, Any] | None = None
    if raw is not None:
        data, salvage_notes = _salvage(raw)
        if data.get("urgency") not in URGENCY_RANK:
            m = _URGENCY_LINE.search(markdown)
            data["urgency"] = m.group(1).lower() if m else None
        try:
            summary = ConsultSummary.model_validate(data).model_dump(mode="json", exclude_none=True)
            if salvage_notes:
                out["repaired"] = salvage_notes[:10]
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
    flag_cant_miss(out, triage)
    return markdown, out


def merge_reported_pmids(structured: dict[str, Any], markdown: str) -> list[str]:
    """PMIDs cited anywhere in the note (body or summary), de-duplicated in order."""
    found = re.findall(r"PMID[:\s#]*?(\d{5,9})", markdown, re.I)
    found += [c["pmid"] for c in structured.get("citations", []) if c.get("pmid")]
    return list(dict.fromkeys(found))
