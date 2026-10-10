"""Prescription and referral DRAFTS, delivered as calls to action.

Nothing here prescribes, orders, or transmits anything. A draft is a FHIR-shaped
MedicationRequest / ServiceRequest with status "draft" and intent "proposal" that a licensed
clinician must review, change, and sign in their own system. Safety checks are deterministic
code run against the patient's chart; the language model cannot skip them.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from .records.model import age_years
from .safety import screen

NOT_A_PRESCRIPTION = (
    "DRAFT FOR CLINICIAN REVIEW — not a prescription or order. It has not been sent to any pharmacy "
    "or provider. A licensed clinician must evaluate the patient, confirm, and issue it themselves."
)

Check = dict[str, str]  # {"check", "result": pass|warn|fail|not_checked, "detail"}

# US DEA-scheduled ingredients (plus common combination names). Drafts are refused for these:
# they require an in-person/telehealth evaluation under the Ryan Haight Act and state rules.
CONTROLLED = {
    "oxycodone", "hydrocodone", "morphine", "fentanyl", "hydromorphone", "oxymorphone", "methadone",
    "buprenorphine", "codeine", "tramadol", "tapentadol", "meperidine", "levorphanol", "butorphanol",
    "alprazolam", "lorazepam", "clonazepam", "diazepam", "temazepam", "midazolam", "chlordiazepoxide",
    "triazolam", "oxazepam", "clobazam", "clorazepate", "estazolam", "zolpidem", "zaleplon", "eszopiclone",
    "suvorexant", "lemborexant", "daridorexant", "amphetamine", "dextroamphetamine", "lisdexamfetamine",
    "methamphetamine", "methylphenidate", "dexmethylphenidate", "modafinil", "armodafinil", "solriamfetol",
    "phentermine", "diethylpropion", "phendimetrazine", "phenobarbital", "butalbital", "pregabalin",
    "lacosamide", "brivaracetam", "cenobamate", "carisoprodol", "testosterone", "nandrolone", "oxandrolone",
    "ketamine", "esketamine", "dronabinol", "nabilone", "sodium oxybate", "oxybate", "diphenoxylate",
    "pentazocine", "nalbuphine", "lomotil", "adderall", "ritalin", "xanax", "valium", "ativan", "klonopin",
    "ambien", "percocet", "vicodin", "norco", "oxycontin", "suboxone",
}

# Allergy label -> drugs that share the allergenic class (cross-reactivity).
CROSS_REACTIVE: dict[str, set[str]] = {
    "penicillin": {"penicillin", "amoxicillin", "ampicillin", "piperacillin", "nafcillin", "oxacillin",
                   "dicloxacillin", "augmentin", "amoxicillin-clavulanate"},
    "cephalosporin": {"cephalexin", "cefazolin", "cefuroxime", "ceftriaxone", "cefdinir", "cefpodoxime",
                      "cefixime", "cefepime", "ceftazidime", "cefadroxil", "cefprozil"},
    "sulfa": {"sulfamethoxazole", "trimethoprim-sulfamethoxazole", "bactrim", "sulfadiazine", "sulfasalazine"},
    "nsaid": {"ibuprofen", "naproxen", "ketorolac", "diclofenac", "meloxicam", "indomethacin", "aspirin",
              "celecoxib", "nabumetone", "etodolac", "piroxicam"},
    "aspirin": {"aspirin", "ibuprofen", "naproxen", "ketorolac", "diclofenac"},
    "macrolide": {"azithromycin", "clarithromycin", "erythromycin"},
    "fluoroquinolone": {"ciprofloxacin", "levofloxacin", "moxifloxacin", "ofloxacin"},
    "tetracycline": {"doxycycline", "minocycline", "tetracycline"},
    "codeine": {"codeine", "morphine", "hydrocodone", "oxycodone"},
    "opioid": {"codeine", "morphine", "hydrocodone", "oxycodone", "tramadol", "hydromorphone", "fentanyl"},
    "ace inhibitor": set(),  # matched by the "-pril" suffix below
}
PENICILLIN_CEPH_NOTE = (
    "Penicillin allergy: cephalosporin cross-reactivity is low (~1-2%) and lowest for dissimilar side chains "
    "(e.g. cefazolin, ceftriaxone); avoid if the penicillin reaction was anaphylaxis or severe without specialist advice."
)

CLASSES: dict[str, set[str]] = {
    "SSRI": {"sertraline", "fluoxetine", "citalopram", "escitalopram", "paroxetine", "fluvoxamine"},
    "SNRI": {"venlafaxine", "desvenlafaxine", "duloxetine", "levomilnacipran"},
    "NSAID": {"ibuprofen", "naproxen", "ketorolac", "diclofenac", "meloxicam", "indomethacin", "celecoxib", "nabumetone"},
    "statin": {"atorvastatin", "rosuvastatin", "simvastatin", "pravastatin", "lovastatin", "pitavastatin"},
    "ARB": {"losartan", "valsartan", "irbesartan", "olmesartan", "candesartan", "telmisartan"},
    "PPI": {"omeprazole", "esomeprazole", "pantoprazole", "lansoprazole", "rabeprazole", "dexlansoprazole"},
    "anticoagulant": {"warfarin", "apixaban", "rivaroxaban", "dabigatran", "edoxaban", "enoxaparin"},
}

# Contraindicated in pregnancy (fail) / needs specialist review (warn).
TERATOGENS_FAIL = {"isotretinoin", "acitretin", "thalidomide", "lenalidomide", "mycophenolate", "methotrexate",
                   "misoprostol", "leflunomide", "ribavirin", "warfarin", "valproate", "valproic acid",
                   "divalproex", "finasteride", "dutasteride", "bosentan", "ambrisentan", "macitentan", "riociguat"}
PREGNANCY_WARN = {"lisinopril", "enalapril", "ramipril", "losartan", "valsartan", "atorvastatin", "rosuvastatin",
                  "simvastatin", "doxycycline", "minocycline", "tetracycline", "lithium", "topiramate", "carbamazepine",
                  "phenytoin", "fluconazole", "ibuprofen", "naproxen", "diclofenac", "trimethoprim-sulfamethoxazole",
                  "ciprofloxacin", "levofloxacin", "spironolactone", "amiodarone", "tamoxifen"}

# Pediatric: (ingredient, minimum age in years, reason)
PEDIATRIC_LIMITS = [
    ("aspirin", 18, "Reye syndrome risk with viral illness (exception: Kawasaki disease under specialist care)"),
    ("codeine", 12, "FDA contraindication under 12 (ultra-rapid metabolizer deaths)"),
    ("tramadol", 12, "FDA contraindication under 12"),
    ("promethazine", 2, "boxed warning: fatal respiratory depression under 2"),
    ("loperamide", 2, "contraindicated under 2"),
    ("benzocaine", 2, "methemoglobinemia risk under 2"),
    ("tetracycline", 8, "tooth discoloration under 8 (doxycycline short courses are acceptable)"),
    ("minocycline", 8, "tooth discoloration under 8"),
    ("ciprofloxacin", 18, "fluoroquinolones generally avoided in children (musculoskeletal risk) unless no alternative"),
    ("levofloxacin", 18, "fluoroquinolones generally avoided in children unless no alternative"),
    ("ceftriaxone", 0.08, "avoid in neonates with hyperbilirubinemia or IV calcium"),
]

# AGS Beers Criteria 2023 (potentially inappropriate in adults >= 65), common entries.
BEERS = {"diphenhydramine", "hydroxyzine", "chlorpheniramine", "promethazine", "meclizine", "amitriptyline",
         "doxepin", "imipramine", "nortriptyline", "paroxetine", "oxybutynin", "cyclobenzaprine", "methocarbamol",
         "carisoprodol", "glyburide", "glimepiride", "meperidine", "indomethacin", "ketorolac", "zolpidem",
         "alprazolam", "lorazepam", "diazepam", "clonazepam", "megestrol", "nitrofurantoin", "dicyclomine",
         "hyoscyamine", "scopolamine", "metoclopramide", "sliding scale insulin"}


def _tokens(name: str) -> set[str]:
    """Lower-cased words of a drug name, keeping hyphenated combinations and their parts."""
    name = name.lower()
    words = set(re.findall(r"[a-z][a-z\-]+", name))
    parts = {p for w in words for p in w.split("-") if p}
    return words | parts | {name.strip()}


def _ingredients(drug: str, normalized: dict[str, Any] | None) -> set[str]:
    out = _tokens(drug)
    if normalized and normalized.get("found"):
        for ing in normalized.get("ingredients", []):
            out |= _tokens(ing)
    return out


def _is_ace_inhibitor(names: set[str]) -> bool:
    return any(n.endswith("pril") for n in names)


def _latest_obs(chart: dict[str, Any], *names: str) -> float | None:
    found = [o for o in chart.get("observations", []) if o.get("name", "").lower() in names]
    found.sort(key=lambda o: o.get("date") or "", reverse=True)
    for o in found:
        m = re.search(r"-?\d+(\.\d+)?", str(o.get("value", "")))
        if m:
            return float(m.group())
    return None


async def check_prescription(
    drug: str,
    chart: dict[str, Any] | None,
    normalized: dict[str, Any] | None,
    interactions: Callable[[list[str]], Awaitable[dict[str, Any]]] | None,
) -> tuple[list[Check], bool]:
    """Run deterministic safety checks. Returns (checks, blocked)."""
    checks: list[Check] = []
    names = _ingredients(drug, normalized)
    blocked = False

    if names & CONTROLLED:
        checks.append({"check": "controlled_substance", "result": "fail",
                       "detail": "DEA-scheduled medicine: no draft. Requires direct evaluation by a licensed prescriber."})
        return checks, True
    checks.append({"check": "controlled_substance", "result": "pass", "detail": "Not on the controlled-substance list."})

    if not chart:
        checks.append({"check": "patient_chart", "result": "not_checked",
                       "detail": "No patient chart supplied: allergies, interactions, pregnancy, age, and kidney function NOT checked."})
        return checks, blocked

    profile = chart.get("profile", {})
    age = age_years(profile)

    # Allergies (direct match + cross-reactivity)
    allergy_hits = []
    for a in chart.get("allergies", []):
        sub = a.get("substance", "").lower()
        sub_tokens = _tokens(sub)
        # Class named directly ("penicillin allergy") or via a member drug ("amoxicillin rash").
        group = next((g for g in CROSS_REACTIVE if g in sub or sub_tokens & CROSS_REACTIVE[g]), None)
        related = CROSS_REACTIVE.get(group, set()) if group else set()
        ace = group == "ace inhibitor" and _is_ace_inhibitor(names)
        if sub_tokens & names or names & related or ace:
            severe = a.get("severity") in ("severe", "anaphylaxis")
            allergy_hits.append((a, severe))
        elif "penicillin" in sub and any(n.startswith("cef") or n.startswith("ceph") for n in names):
            checks.append({"check": "allergy_cross_reactivity", "result": "warn", "detail": PENICILLIN_CEPH_NOTE})
    for a, severe in allergy_hits:
        if a.get("type") == "intolerance" and not severe:
            checks.append({"check": "allergy", "result": "warn",
                           "detail": f"Recorded intolerance to {a['substance']} ({a.get('reaction', 'reaction not recorded')})."})
        else:
            blocked = True
            checks.append({"check": "allergy", "result": "fail",
                           "detail": f"Recorded allergy to {a['substance']} ({a.get('severity', 'unknown')}"
                                     f"{', ' + a['reaction'] if a.get('reaction') else ''}) matches this drug or its class."})
    if not chart.get("allergies"):
        checks.append({"check": "allergy", "result": "warn", "detail": "No allergies recorded: confirm 'no known drug allergies' with the patient."})
    elif not allergy_hits:
        checks.append({"check": "allergy", "result": "pass", "detail": "No match against recorded allergies."})

    # Duplicate therapy
    active = [m for m in chart.get("medications", []) if m.get("status", "active") != "stopped"]
    for m in active:
        m_names = _tokens(m["name"])
        if m_names & names:
            checks.append({"check": "duplicate_therapy", "result": "warn", "detail": f"Already taking {m['name']}."})
        for cls, members in CLASSES.items():
            if m_names & members and names & members and not m_names & names:
                checks.append({"check": "duplicate_class", "result": "warn",
                               "detail": f"Same class ({cls}) as current medication {m['name']}."})

    # Interactions against the medication list (FDA label text)
    if active and interactions:
        try:
            res = await interactions([drug] + [m["name"] for m in active])
            hits = res.get("interactions", [])
            if hits:
                checks.append({"check": "interactions", "result": "warn",
                               "detail": "; ".join(f"{h['label_of']} label mentions {h['mentions']}: {h['excerpts'][0][:240]}" for h in hits[:4])})
            else:
                checks.append({"check": "interactions", "result": "pass",
                               "detail": "No label-documented interaction found (not proof of safety; pharmacist review advised)."})
        except Exception as exc:  # network/source failure must not look like a pass
            checks.append({"check": "interactions", "result": "not_checked", "detail": f"Interaction source unavailable ({type(exc).__name__})."})

    # Pregnancy / breastfeeding
    preg = profile.get("pregnancy_status")
    if preg in ("pregnant", "possible"):
        if names & TERATOGENS_FAIL:
            blocked = True
            checks.append({"check": "pregnancy", "result": "fail", "detail": "Contraindicated / known teratogen in pregnancy."})
        elif names & PREGNANCY_WARN or _is_ace_inhibitor(names):
            checks.append({"check": "pregnancy", "result": "warn", "detail": "Pregnancy risk: review label section 8.1 and alternatives."})
        else:
            checks.append({"check": "pregnancy", "result": "warn", "detail": "Patient is or may be pregnant: confirm safety in label section 8.1."})
    if profile.get("breastfeeding"):
        checks.append({"check": "lactation", "result": "warn", "detail": "Breastfeeding: confirm with label section 8.2 / LactMed."})

    # Age
    if age is not None and age < 18:
        weight = profile.get("weight_kg")
        if not weight:
            blocked = True
            checks.append({"check": "pediatric_weight", "result": "fail",
                           "detail": "Child without a recorded weight: weight-based dose cannot be verified. Record weight first."})
        else:
            checks.append({"check": "pediatric_weight", "result": "warn",
                           "detail": f"Child, {weight} kg: verify mg/kg dose with calc_weight_based_dose and never exceed the adult maximum."})
        for ing, min_age, why in PEDIATRIC_LIMITS:
            if ing in names and age < min_age:
                hard = ing in {"aspirin", "codeine", "tramadol", "promethazine", "loperamide", "benzocaine"}
                blocked = blocked or hard
                checks.append({"check": "pediatric_contraindication", "result": "fail" if hard else "warn", "detail": f"{ing}: {why}."})
    if age is not None and age >= 65 and names & BEERS:
        checks.append({"check": "beers_criteria", "result": "warn",
                       "detail": "Potentially inappropriate in older adults (AGS Beers Criteria 2023): consider a safer alternative."})

    # Kidney function
    egfr = _latest_obs(chart, "egfr", "estimated gfr", "gfr")
    if egfr is not None:
        if "metformin" in names and egfr < 30:
            blocked = True
            checks.append({"check": "renal", "result": "fail", "detail": f"eGFR {egfr:g}: metformin contraindicated below 30."})
        elif names & CLASSES["NSAID"] and egfr < 30:
            checks.append({"check": "renal", "result": "warn", "detail": f"eGFR {egfr:g}: avoid NSAIDs in severe CKD."})
        elif egfr < 60:
            checks.append({"check": "renal", "result": "warn",
                           "detail": f"eGFR {egfr:g}: check renal dose adjustment (calc_creatinine_clearance_cockcroft_gault)."})
        else:
            checks.append({"check": "renal", "result": "pass", "detail": f"eGFR {egfr:g}."})
    else:
        checks.append({"check": "renal", "result": "not_checked", "detail": "No recent eGFR/creatinine in the chart."})
    return checks, blocked


def prescription_draft(
    *,
    drug: str,
    dose: str,
    route: str,
    frequency: str,
    duration: str | None,
    quantity: str | None,
    refills: int,
    indication: str,
    instructions: str | None,
    normalized: dict[str, Any] | None,
    checks: list[Check],
    blocked: bool,
) -> dict[str, Any]:
    sig = " ".join(x for x in (dose, route, frequency, f"for {duration}" if duration else None) if x)
    med_text = drug
    rx_code = []
    if normalized and normalized.get("found"):
        rx_code = [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": normalized["rxcui"], "display": drug}]
    draft = {
        "resourceType": "MedicationRequest",
        "status": "draft",
        "intent": "proposal",
        "medicationCodeableConcept": {"text": med_text, **({"coding": rx_code} if rx_code else {})},
        "dosageInstruction": [{"text": sig + (f". {instructions}" if instructions else "")}],
        "reasonCode": [{"text": indication}],
        "dispenseRequest": {
            **({"quantity": {"value": quantity}} if quantity else {}),
            "numberOfRepeatsAllowed": refills,
        },
        "note": [{"text": NOT_A_PRESCRIPTION}],
    }
    fails = [c for c in checks if c["result"] == "fail"]
    warns = [c for c in checks if c["result"] in ("warn", "not_checked")]
    if blocked:
        patient_cta = (f"Do not start {drug} on your own. The safety checks found a problem: "
                       + "; ".join(c["detail"] for c in fails)
                       + " Discuss alternatives with your clinician.")
        clinician_cta = "Draft blocked by safety checks: " + "; ".join(c["detail"] for c in fails)
    else:
        patient_cta = (f"Share this draft for {drug} with your doctor, nurse practitioner, or a telehealth clinician. "
                       "They need to evaluate you and decide whether to prescribe it. Do not use leftover or someone else's medicine.")
        clinician_cta = (f"Proposed: {drug} {sig} for {indication}"
                         + (f"; qty {quantity}" if quantity else "") + f"; refills {refills}. "
                         + ("Review: " + "; ".join(f"{c['check']}: {c['detail']}" for c in warns) if warns else "All automated checks passed."))
    return {
        "status": "blocked" if blocked else "draft_ready",
        "draft": draft,
        "checks": checks,
        "call_to_action": {"for_patient": patient_cta, "for_clinician": clinician_cta},
        "disclaimer": NOT_A_PRESCRIPTION,
    }


Urgency = Literal["emergency", "urgent", "soon", "routine"]


def referral_draft(
    *,
    specialty: str,
    reason: str,
    urgency: Urgency | None,
    clinical_question: str | None,
    workup_before_visit: list[str] | None,
    chart_summary: str | None,
) -> dict[str, Any]:
    triage = screen(reason)
    level: Urgency = urgency or {"emergency": "emergency", "urgent": "urgent"}.get(triage.urgency, "routine")  # type: ignore[assignment]
    if triage.urgency == "emergency":
        level = "emergency"  # never let a stated "routine" downgrade a red flag
    timing = {
        "emergency": "Now — this is not a referral situation.",
        "urgent": "Within 24-72 hours.",
        "soon": "Within 1-4 weeks.",
        "routine": "Next available routine appointment.",
    }[level]
    if level == "emergency":
        patient_cta = f"{triage.message} Go to the nearest emergency department or call emergency services now; do not wait for a referral."
    else:
        patient_cta = (f"Ask your primary care clinician (or a telehealth service) to refer you to {specialty} — timing: {timing.lower()} "
                       "Bring your medication list, allergy list and any recent test results. Check whether your insurance needs a referral or pre-authorization.")
    draft = {
        "resourceType": "ServiceRequest",
        "status": "draft",
        "intent": "proposal",
        "category": [{"text": "referral"}],
        "code": {"text": f"Referral to {specialty}"},
        "priority": {"emergency": "stat", "urgent": "urgent", "soon": "asap", "routine": "routine"}[level],
        "reasonCode": [{"text": reason}],
        "note": [{"text": NOT_A_PRESCRIPTION.replace("prescription", "referral")}],
    }
    letter = "\n".join(
        x for x in (
            f"Referral to {specialty} — {level.upper()} ({timing})",
            f"Reason: {reason}",
            f"Clinical question: {clinical_question}" if clinical_question else None,
            f"Pertinent history:\n{chart_summary}" if chart_summary else None,
            ("Suggested before the visit: " + "; ".join(workup_before_visit)) if workup_before_visit else None,
        ) if x
    )
    return {
        "status": "emergency_redirect" if level == "emergency" else "draft_ready",
        "urgency": level,
        "red_flags": [h.condition for h in triage.hits],
        "draft": draft,
        "referral_letter": letter,
        "call_to_action": {"for_patient": patient_cta, "for_clinician": letter},
        "disclaimer": NOT_A_PRESCRIPTION.replace("prescription", "referral"),
    }
