"""The can't-miss DIAGNOSES behind red-flag screen rules, matched by name.

`quality.CANT_MISS` lists words that show a note *addressed* a red flag (presentation and management words
like "safety" or "airway" included). This table is stricter and narrower: only rules whose danger is a
diagnosis that belongs in the differential, with names matched at word starts, so "tia" does not match
"partial seizure" and "infect" does not make "viral infection" the emergency. Used to require the diagnosis
in the differential and to set its `cant_miss` flag deterministically.
"""

from __future__ import annotations

import re
from typing import Any

CANT_MISS_DX: dict[str, tuple[str, ...]] = {
    "acs": ("acute coronary", "myocardial infarction", "nstemi", "stemi", "unstable angina", "acs"),
    "stroke": ("stroke", "transient ischemic", "tia", "intracerebral hemorrhage", "intracranial hemorrhage",
               "cerebral infarct", "cerebrovascular accident", "cva"),
    "thunderclap": ("subarachnoid", "sah", "aneurysm", "cerebral venous", "reversible cerebral vasoconstriction"),
    "meningitis": ("meningitis", "encephalitis"),
    "sepsis": ("sepsis", "septic shock"),
    "anaphylaxis": ("anaphyla",),
    "pregnancy": ("ectopic", "abruption", "eclampsia", "preeclampsia", "pre-eclampsia", "hellp", "miscarriage",
                  "spontaneous abortion", "previa", "preterm labor", "preterm labour", "rupture of membranes",
                  "pprom", "prom", "fetal compromise", "fetal distress", "fetal demise", "intrauterine fetal",
                  "placental insufficiency", "uterine rupture", "postpartum hemorrhage", "postpartum haemorrhage",
                  "chorioamnionitis"),
    "neonatal_fever": ("serious bacterial infection", "bacterial infection", "sepsis", "meningitis", "bacteremia",
                       "urinary tract infection", "uti", "herpes", "hsv"),
    "torsion": ("torsion",),
    "dka": ("ketoacidosis", "dka", "hyperosmolar"),
    "pe_dvt": ("pulmonary embol", "deep vein thromb", "deep venous thromb", "venous thromboembol", "dvt", "vte"),
    "giant_cell_arteritis": ("giant cell arteritis", "temporal arteritis", "gca"),
    "jaw_claudication": ("giant cell arteritis", "temporal arteritis", "gca"),
    "hot_joint": ("septic arthritis", "septic joint", "infectious arthritis", "bacterial arthritis", "gonococcal arthritis"),
    "neutropenic_fever": ("neutropen",),
    "severe_skin_reaction": ("stevens", "sjs", "toxic epidermal", "ten", "dress", "drug reaction with eosinophilia"),
    "bilious_vomiting": ("volvulus", "malrotation", "intussuscep", "obstruct"),
    "deep_neck_infection": ("peritonsillar", "retropharyngeal", "parapharyngeal", "abscess", "epiglottitis", "ludwig"),
    "eye_emergency": ("glaucoma", "angle-closure", "angle closure", "retinal detachment", "retinal tear",
                      "central retinal", "chemical injury", "chemical burn", "endophthalmitis", "orbital cellulitis"),
    "sudden_hearing_loss": ("sensorineural", "sudden hearing"),
}


def _pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    # Short names and abbreviations (tia, sah, ten, uti) must be whole words; longer ones are prefixes
    # ("anaphyla" -> anaphylaxis), always starting at a word boundary.
    parts = [rf"\b{re.escape(w)}\b" if len(w) <= 4 else rf"\b{re.escape(w)}" for w in words]
    return re.compile("|".join(parts), re.IGNORECASE)


_PATTERNS = {rule: _pattern(words) for rule, words in CANT_MISS_DX.items()}


def names(rule: str, text: str) -> bool | None:
    """Whether `text` names the rule's can't-miss diagnosis; None if the rule has no diagnosis table."""
    p = _PATTERNS.get(rule)
    return None if p is None else bool(p.search(text))


def flag(structured: dict[str, Any], triage: dict[str, Any] | None) -> None:
    """Set cant_miss on listed diagnoses that a red flag is about (the model lists ectopic pregnancy but
    forgets the flag), so every client highlights them, whichever tool produced the note."""
    rules = [h.get("id", "") for h in (triage or {}).get("red_flags") or []]
    for d in structured.get("differential") or []:
        if isinstance(d, dict) and any(names(r, str(d.get("diagnosis", ""))) for r in rules):
            d["cant_miss"] = True
