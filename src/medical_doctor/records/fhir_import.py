"""Import a FHIR R4 Bundle (an EHR export, a SMART on FHIR $everything result, or a health-app export)
into the personal chart.

Maps Patient, Condition, MedicationStatement / MedicationRequest, AllergyIntolerance, Observation
(incl. blood-pressure components), Immunization, Procedure and FamilyMemberHistory. Records that are
entered-in-error, refuted or not-done are skipped. Item IDs derive from the source resource, so
importing the same export twice updates entries instead of duplicating them. Every skipped resource
is reported with a reason; nothing is silently dropped.
"""

from __future__ import annotations

import hashlib
from typing import Any

MAX_RESOURCES = 2000

_BP = {"8480-6": "systolic", "8462-4": "diastolic"}


def _clip(value: Any, n: int) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s[: n - 1] + "…" if len(s) > n else (s or None)


def _concept(cc: Any) -> str | None:
    if not isinstance(cc, dict):
        return None
    if cc.get("text"):
        return cc["text"]
    for c in cc.get("coding") or []:
        if c.get("display"):
            return c["display"]
    for c in cc.get("coding") or []:
        if c.get("code"):
            return c["code"]
    return None


def _code(cc: Any) -> str | None:
    """Prefer ICD-10, then SNOMED CT, then any coding, as 'SYSTEM:code'."""
    codings = (cc or {}).get("coding") or [] if isinstance(cc, dict) else []
    for marker, label in (("icd-10", "ICD-10"), ("snomed", "SNOMED"), ("loinc", "LOINC"), ("rxnorm", "RxNorm"), ("cvx", "CVX")):
        for c in codings:
            if marker in (c.get("system") or "").lower() and c.get("code"):
                return f"{label}:{c['code']}"
    return None


def _status_code(cc: Any) -> str | None:
    if isinstance(cc, dict):
        for c in cc.get("coding") or []:
            if c.get("code"):
                return c["code"]
        return (cc.get("text") or "").lower() or None
    return cc if isinstance(cc, str) else None


def _date(r: dict[str, Any], *keys: str) -> str | None:
    for k in keys:
        v = r.get(k)
        if isinstance(v, str):
            return v[:40]
        if isinstance(v, dict) and isinstance(v.get("start"), str):
            return v["start"][:40]
    return None


def _item_id(resource: dict[str, Any], suffix: str = "") -> str | None:
    rid = resource.get("id")
    if not rid:
        return None
    digest = hashlib.sha256(f"{resource.get('resourceType')}/{rid}{suffix}".encode()).hexdigest()[:16]
    return f"fx_{digest}"


def _quantity(q: Any) -> tuple[str | None, str | None]:
    if not isinstance(q, dict) or q.get("value") is None:
        return None, None
    v = q["value"]
    return (str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)), q.get("unit") or q.get("code")


def resources_of(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        res = [r for r in payload if isinstance(r, dict)]
    elif isinstance(payload, dict) and payload.get("resourceType") == "Bundle":
        res = [e.get("resource") for e in payload.get("entry") or [] if isinstance(e, dict) and isinstance(e.get("resource"), dict)]
    elif isinstance(payload, dict) and payload.get("resourceType"):
        res = [payload]
    else:
        raise ValueError("Expected a FHIR Bundle, a resource, or a list of resources")
    if len(res) > MAX_RESOURCES:
        raise ValueError(f"Too many resources ({len(res)}); import at most {MAX_RESOURCES} at a time")
    return res


def _patient_profile(p: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    names = p.get("name") or []
    official = next((n for n in names if n.get("use") == "official"), names[0] if names else None)
    if official:
        text = official.get("text") or " ".join([*(official.get("given") or []), official.get("family") or ""]).strip()
        out["name"] = _clip(text, 200)
    if isinstance(p.get("birthDate"), str) and len(p["birthDate"]) == 10:
        out["date_of_birth"] = p["birthDate"]
    if p.get("gender") in ("female", "male"):
        out["sex_at_birth"] = p["gender"]
    elif p.get("gender") in ("other", "unknown"):
        out["sex_at_birth"] = "unknown"
    for c in p.get("communication") or []:
        if lang := _concept(c.get("language")):
            out["preferred_language"] = _clip(lang, 200)
            break
    return {k: v for k, v in out.items() if v}


def convert(payload: Any) -> dict[str, Any]:
    """Return {"profile": {...}, "items": [(section, data, item_id, source)], "skipped": [...]}."""
    resources = resources_of(payload)
    patients = [r for r in resources if r.get("resourceType") == "Patient"]
    if len({p.get("id") for p in patients}) > 1:
        raise ValueError("The bundle contains more than one Patient; import one person's records at a time")
    patient_id = patients[0].get("id") if patients else None
    meds_by_ref = {f"Medication/{r.get('id')}": r for r in resources if r.get("resourceType") == "Medication"}
    items: list[tuple[str, dict[str, Any], str | None, str]] = []
    skipped: list[dict[str, str]] = []

    def skip(r: dict[str, Any], why: str) -> None:
        skipped.append({"resource": f"{r.get('resourceType')}/{r.get('id', '?')}", "reason": why})

    def other_patient(r: dict[str, Any]) -> bool:
        ref = ((r.get("subject") or r.get("patient")) or {}).get("reference") or ""
        return bool(patient_id and ref and ref.split("/")[-1] != patient_id and ref.startswith("Patient/"))

    for r in resources:
        rtype, src = r.get("resourceType"), f"{r.get('resourceType')}/{r.get('id', '?')}"
        if rtype in ("Patient", "Medication"):
            continue
        if other_patient(r):
            skip(r, "belongs to a different patient")
            continue
        status = (r.get("status") or "").lower()
        if status == "entered-in-error":
            skip(r, "entered in error")
            continue

        if rtype == "Condition":
            verification = _status_code(r.get("verificationStatus"))
            if verification in ("refuted", "entered-in-error"):
                skip(r, f"verification status {verification}")
                continue
            clinical = _status_code(r.get("clinicalStatus")) or "active"
            mapped = {"inactive": "resolved", "resolved": "resolved", "remission": "remission"}.get(clinical, "active")
            if verification in ("provisional", "differential", "unconfirmed"):
                mapped = "suspected"
            name = _concept(r.get("code"))
            if not name:
                skip(r, "no condition name")
                continue
            items.append(("conditions", {"name": _clip(name, 200), "code": _clip(_code(r.get("code")), 40), "status": mapped,
                                         "onset": _date(r, "onsetDateTime", "onsetString", "onsetPeriod", "recordedDate")}, _item_id(r), src))

        elif rtype in ("MedicationStatement", "MedicationRequest"):
            if rtype == "MedicationRequest" and (r.get("intent") or "order") in ("proposal", "plan"):
                skip(r, "a proposal, not a medication the patient takes")
                continue
            concept = r.get("medicationCodeableConcept") or (r.get("medication") or {}).get("concept")
            ref = (r.get("medicationReference") or (r.get("medication") or {}).get("reference") or {}).get("reference")
            if not concept and ref:
                if ref.startswith("#"):
                    concept = next((c.get("code") for c in r.get("contained") or [] if f"#{c.get('id')}" == ref), None)
                else:
                    concept = (meds_by_ref.get(ref) or {}).get("code")
            # Epic often gives only medicationReference.display.
            name = _concept(concept) or (r.get("medicationReference") or {}).get("display")
            if not name:
                skip(r, "no medication name")
                continue
            dosages = r.get("dosage") or r.get("dosageInstruction") or []
            dose_text = next((d.get("text") for d in dosages if d.get("text")), None)
            as_needed = any(d.get("asNeededBoolean") or d.get("asNeeded") or d.get("asNeededCodeableConcept") for d in dosages)
            stopped = status in ("stopped", "completed", "cancelled", "not-taken", "ended")
            items.append(("medications", {
                "name": _clip(name, 200), "dose": _clip(dose_text, 100),
                "status": "stopped" if stopped else ("as_needed" if as_needed else "active"),
                "started": _date(r, "effectiveDateTime", "effectivePeriod", "authoredOn"),
                "prescriber": _clip((r.get("requester") or r.get("informationSource") or {}).get("display"), 200),
                "indication": _clip(next((_concept(x) for x in r.get("reasonCode") or [] if _concept(x)), None), 200),
            }, _item_id(r), src))

        elif rtype == "AllergyIntolerance":
            verification = _status_code(r.get("verificationStatus"))
            if verification in ("refuted", "entered-in-error"):
                skip(r, f"verification status {verification}")
                continue
            substance = _concept(r.get("code"))
            if not substance:
                skip(r, "no substance")
                continue
            reactions = r.get("reaction") or []
            manifest = ", ".join(filter(None, (_concept(m) for rx in reactions for m in rx.get("manifestation") or [])))
            sev = next((rx.get("severity") for rx in reactions if rx.get("severity")), None)
            severity = "anaphylaxis" if "anaphyla" in manifest.lower() else (
                "severe" if sev == "severe" or r.get("criticality") == "high" else {"mild": "mild", "moderate": "moderate"}.get(sev or "", "unknown"))
            category = {"medication": "drug", "food": "food", "environment": "environment"}.get((r.get("category") or ["other"])[0], "other")
            items.append(("allergies", {"substance": _clip(substance, 200), "reaction": _clip(manifest, 200), "severity": severity,
                                        "category": category, "type": "intolerance" if r.get("type") == "intolerance" else "allergy"},
                          _item_id(r), src))

        elif rtype == "Observation":
            name = _concept(r.get("code"))
            if not name:
                skip(r, "no observation name")
                continue
            value, unit = _quantity(r.get("valueQuantity"))
            if value is None:
                value = r.get("valueString") or _concept(r.get("valueCodeableConcept"))
                if r.get("valueBoolean") is not None:
                    value = str(r["valueBoolean"]).lower()
            if value is None and r.get("component"):
                parts = {}
                for comp in r["component"]:
                    loinc = next((c.get("code") for c in (comp.get("code") or {}).get("coding") or [] if c.get("code") in _BP), None)
                    v, u = _quantity(comp.get("valueQuantity"))
                    if loinc and v:
                        parts[_BP[loinc]], unit = v, u
                if "systolic" in parts and "diastolic" in parts:
                    value, name = f"{parts['systolic']}/{parts['diastolic']}", "blood pressure"
            if value is None:
                skip(r, "no value")
                continue
            cats = {_status_code(c) for c in r.get("category") or []}
            category = "vital" if "vital-signs" in cats else "imaging" if "imaging" in cats else "lab" if "laboratory" in cats else "other"
            rr = (r.get("referenceRange") or [{}])[0]
            ref_range = rr.get("text") or (
                f"{_quantity(rr.get('low'))[0] or ''}-{_quantity(rr.get('high'))[0] or ''}" if rr.get("low") or rr.get("high") else None)
            items.append(("observations", {"name": _clip(name, 120), "value": _clip(value, 60), "unit": _clip(unit, 30),
                                           "date": _date(r, "effectiveDateTime", "effectivePeriod", "issued"), "category": category,
                                           "reference_range": _clip(ref_range, 60)}, _item_id(r), src))

        elif rtype == "Immunization":
            if status == "not-done":
                skip(r, "not done")
                continue
            vaccine = _concept(r.get("vaccineCode"))
            if not vaccine:
                skip(r, "no vaccine name")
                continue
            dose = next((p.get("doseNumberPositiveInt") for p in r.get("protocolApplied") or [] if p.get("doseNumberPositiveInt")), None)
            items.append(("immunizations", {"vaccine": _clip(vaccine, 200), "date": _date(r, "occurrenceDateTime", "occurrenceString"),
                                            "dose_number": dose if isinstance(dose, int) and 1 <= dose <= 20 else None}, _item_id(r), src))

        elif rtype == "Procedure":
            if status in ("not-done", "preparation"):
                skip(r, f"status {status}")
                continue
            name = _concept(r.get("code"))
            if not name:
                skip(r, "no procedure name")
                continue
            items.append(("procedures", {"name": _clip(name, 200), "date": _date(r, "performedDateTime", "performedPeriod", "occurrenceDateTime")},
                          _item_id(r), src))

        elif rtype == "FamilyMemberHistory":
            relation = _concept(r.get("relationship")) or "relative"
            for n, cond in enumerate(r.get("condition") or []):
                if name := _concept(cond.get("code")):
                    onset = (cond.get("onsetAge") or {}).get("value")
                    items.append(("family_history", {
                        "relation": _clip(relation, 60), "condition": _clip(name, 200),
                        "age_at_onset": int(onset) if isinstance(onset, (int, float)) and 0 <= onset <= 120 else None,
                    }, _item_id(r, f"#{n}"), src))
        else:
            skip(r, "resource type not imported")

    items = [(s, {k: v for k, v in d.items() if v is not None}, i, src) for s, d, i, src in items]
    return {"profile": _patient_profile(patients[0]) if patients else {}, "items": items, "skipped": skipped}
