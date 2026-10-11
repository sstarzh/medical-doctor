"""Deterministic self-check of a consult note, and the escalation policy built on it.

Runs in milliseconds (no model call) after every consult:
  * can't-miss coverage: every emergency the red-flag screen found must appear in the differential;
  * urgency: the model's own call must not be below the screen;
  * allergies: no drug the patient is allergic to (or cross-reactive with) recommended in management/drafts;
  * pediatric dosing: children's doses need a weight, and mg/kg reasoning;
  * structure, empty differential, fabricated citations.

Only when a CRITICAL finding remains does the server spend one more model call: a focused revision
of the note with the findings (no new tool loop). Wide uncertainty does not auto-escalate (that
would double every hard consult); it returns `suggest_second_opinion` instead.
"""

from __future__ import annotations

import re
from typing import Any

from .cant_miss import names
from .consult_note import URGENCY_RANK
from .orders import BEERS, CLASSES, CONTROLLED, CROSS_REACTIVE, PEDIATRIC_LIMITS, _tokens
from .records.model import age_years

# Red-flag rule -> words one of which the differential (or note) must mention.
CANT_MISS: dict[str, tuple[str, ...]] = {
    "acs": ("coronary", "myocardial", "acs", "infarction", "angina"),
    "stroke": ("stroke", "ischemi", "transient ischemic", "tia", "hemorrhag"),
    "thunderclap": ("subarachnoid", "sah", "aneurysm"),
    "meningitis": ("meningitis", "encephalitis"),
    "sepsis": ("sepsis", "septic"),
    "anaphylaxis": ("anaphyla",),
    "suicide": ("suicid", "self-harm", "safety"),
    "pregnancy": ("ectopic", "abruption", "eclampsia", "miscarriage", "pregnan"),
    "neonatal_fever": ("bacterial", "sepsis", "meningitis", "urinary tract", "hsv", "herpes"),
    "torsion": ("torsion",),
    "dka": ("ketoacidosis", "dka"),
    "head_injury": ("intracranial", "brain injury", "hemorrhag", "hematoma", "tbi"),
    "pe_dvt": ("embol", "thrombo", "dvt"),
    "airway_breathing": ("airway", "respiratory failure", "hypox"),
    "hypoglycemia": ("hypoglyc",),
    "bilious_vomiting": ("volvulus", "malrotation", "intussuscep", "obstruct"),
    "severe_skin_reaction": ("stevens", "sjs", "epidermal necrolysis", "dress syndrome", "drug reaction with eosinophilia"),
    "electrolyte_emergency": ("hyperkal", "hyponatr", "potassium", "sodium"),
    "giant_cell_arteritis": ("giant cell", "temporal arteritis", "gca"),
    "jaw_claudication": ("giant cell", "temporal arteritis", "gca"),
    "hot_joint": ("septic arthritis", "infect"),
    "neutropenic_fever": ("neutropen",),
    "urinary_retention": ("retention",),
    "urinary_retention_named": ("retention",),
    "deep_neck_infection": ("abscess", "epiglottitis", "airway", "ludwig"),
    "sudden_hearing_loss": ("sensorineural", "sudden hearing"),
    "eye_emergency": ("glaucoma", "angle-closure", "angle closure", "retinal", "detachment", "chemical injury", "chemical burn"),
}

_NEGATING = re.compile(r"\b(avoid|not|no|never|contraindicat\w*|allerg\w*|instead of|rather than|do not|don't)\b", re.I)


def _known_drugs() -> set[str]:
    names: set[str] = set(CONTROLLED) | set(BEERS)
    for group in (*CROSS_REACTIVE.values(), *CLASSES.values()):
        names |= group
    names |= {lim[0] if isinstance(lim, tuple) else lim.get("drug", "") for lim in PEDIATRIC_LIMITS} - {""}
    return {n for n in names if len(n) > 3}


KNOWN_DRUGS = _known_drugs()


def _mentions(text: str, words: tuple[str, ...]) -> bool:
    t = text.lower()
    return any(w in t for w in words)


def _recommended_drugs(structured: dict[str, Any]) -> dict[str, str]:
    """Drug -> the sentence recommending it (sentences that avoid/contraindicate the drug are skipped)."""
    out: dict[str, str] = {}
    for item in [*(structured.get("management") or []), *(structured.get("drafts") or [])]:
        for sentence in re.split(r"(?<=[.;])\s+", str(item)):
            if _NEGATING.search(sentence):
                continue
            for tok in _tokens(sentence):
                if tok in KNOWN_DRUGS:
                    out.setdefault(tok, sentence.strip()[:200])
    return out


def review(
    structured: dict[str, Any], note: str, triage: dict[str, Any], chart: dict[str, Any] | None,
    citation_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []

    def add(severity: str, check: str, detail: str) -> None:
        findings.append({"severity": severity, "check": check, "detail": detail})

    if structured.get("source") != "model":
        add("warning", "structured_summary", "The note had no valid machine-readable summary; checks are partial.")
    ddx = structured.get("differential") or []
    ddx_text = " ".join(str(d.get("diagnosis", "")) for d in ddx)

    # Can't-miss coverage of every emergency pattern the screen found.
    for hit in triage.get("red_flags") or []:
        rule = hit.get("id", "")
        # Rules whose danger is a diagnosis (cant_miss.CANT_MISS_DX) need it named IN the differential:
        # a prose mention ("ectopic unlikely") is how one silently drops off the list. Presentation-type
        # rules (suicide risk, airway) only need the note to address them.
        named = names(rule, ddx_text) if ddx else names(rule, note)
        if named is not None:
            gap = None if named else "the differential does not include it" if ddx else "the note never names it"
        elif words := CANT_MISS.get(rule):
            gap = None if _mentions(ddx_text, words) or _mentions(note, words) else "the note never addresses it"
        else:
            continue
        if gap:
            add("critical", "cant_miss", f"The red-flag screen found '{hit.get('condition')}' but {gap}.")

    # Under-triage relative to the deterministic screen (the floor is applied for routing, but the
    # note's own words must not tell the patient it can wait).
    screen, model = triage.get("urgency"), structured.get("model_urgency")
    if model and URGENCY_RANK.get(screen or "", -1) > URGENCY_RANK.get(model, -1):
        add("critical", "under_triage", f"The note says '{model}' but the red-flag screen says '{screen}'.")

    if not ddx and URGENCY_RANK.get(structured.get("urgency") or "", 0) > 0:
        add("warning", "differential", "No differential diagnosis for a non-routine presentation.")

    recommended = _recommended_drugs(structured)
    if chart:
        # Allergy conflicts (direct, class, or member drug).
        for a in chart.get("allergies", []):
            sub = str(a.get("substance", "")).lower()
            sub_tokens = _tokens(sub)
            group = next((g for g in CROSS_REACTIVE if g in sub or sub_tokens & CROSS_REACTIVE[g]), None)
            related = (CROSS_REACTIVE.get(group) or set()) | sub_tokens
            for drug, sentence in recommended.items():
                if drug in related:
                    sev = "critical" if a.get("type") != "intolerance" else "warning"
                    add(sev, "allergy_conflict", f"Recommends {drug} but the chart lists {a.get('substance')} "
                        f"({a.get('type', 'allergy')}, {a.get('severity', 'unknown')}): \"{sentence}\"")
        # Pediatric dosing safety.
        profile = chart.get("profile", {})
        age = age_years(profile)
        if age is not None and age < 18 and re.search(r"\d+(\.\d+)?\s*mg\b(?!\s*/\s*kg)", " ".join(map(str, structured.get("management") or []))):
            if not profile.get("weight_kg"):
                add("critical", "pediatric_dose_without_weight", "A dose is given for a child whose weight is not recorded.")
            elif "mg/kg" not in note.lower():
                add("warning", "pediatric_dose_basis", "Child's dose given without the mg/kg basis.")

    counts = (citation_check or {}).get("counts") or {}
    bad = counts.get("not_found", 0) + counts.get("mismatch", 0)
    if bad:
        add("warning", "citations", f"{bad} cited PMID(s) did not check out against PubMed; treat those references as unreliable.")

    # Diagnostic uncertainty: wide differential with no dominant diagnosis.
    probs = [d.get("probability_pct") for d in ddx if isinstance(d.get("probability_pct"), (int, float))]
    uncertain = (bool(probs) and max(probs) < 40) or sum(d.get("likelihood") == "possible" for d in ddx) >= 4
    return {
        "findings": findings,
        "critical": sum(f["severity"] == "critical" for f in findings),
        "uncertain": uncertain,
        "suggest_second_opinion": uncertain or any(f["severity"] == "critical" for f in findings),
    }


REVISION_PROMPT = """\
A deterministic safety review found problems in the consult note below. Rewrite the COMPLETE note \
(same sections, same machine-readable JSON block at the end) fixing every finding. Do not drop correct \
content. If a finding is a false alarm, keep the content and say briefly why in the relevant section.

<findings>
{findings}
</findings>
"""
