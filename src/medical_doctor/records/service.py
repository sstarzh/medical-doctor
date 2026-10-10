"""Patient chart service: tenancy, validation, summaries and FHIR export on top of the encrypted repo.

The caller's identity (owner) always comes from the authenticated principal, never from tool
arguments, so one account can never read or write another account's charts.
"""

from __future__ import annotations

import secrets
from datetime import date
from typing import Any

from .model import LIST_SECTIONS, MAX_ITEMS_PER_SECTION, MAX_PATIENTS_PER_ACCOUNT, SECTIONS, age_years, validate_item
from .repo import AsyncRecordRepo


class RecordsError(Exception):
    """User-facing error (unknown patient, invalid section, limits)."""


class PatientService:
    def __init__(self, repo: AsyncRecordRepo):
        self.repo = repo

    async def list_patients(self, owner: str, client_id: str | None) -> list[dict[str, Any]]:
        out = []
        for pid in await self.repo.patient_ids(owner):
            chart = await self.repo.read_chart(owner, client_id, pid, ["profile"], purpose="list")
            profile = _single(chart or {}, "profile")
            age = age_years(profile)
            out.append(
                {
                    "patient_id": pid,
                    "name": profile.get("name"),
                    "relationship": profile.get("relationship", "self"),
                    "age_years": round(age, 1) if age is not None else None,
                }
            )
        return out

    async def create_patient(self, owner: str, client_id: str | None, profile: dict[str, Any]) -> str:
        data = validate_item("profile", profile)
        try:
            pid = await self.repo.create_patient(owner, client_id, MAX_PATIENTS_PER_ACCOUNT)
        except ValueError as exc:
            raise RecordsError(str(exc)) from None
        await self.repo.put(owner, client_id, pid, "profile", "main", data, 1)
        return pid

    async def chart(self, owner: str, client_id: str | None, patient_id: str, purpose: str = "read") -> dict[str, Any]:
        raw = await self.repo.read_chart(owner, client_id, patient_id, purpose=purpose)
        if raw is None:
            raise RecordsError(f"No patient {patient_id!r} in this account. Use patient_list.")
        chart: dict[str, Any] = {"patient_id": patient_id}
        for section, (_, single) in SECTIONS.items():
            items = raw.get(section, [])
            if single:
                chart[section] = items[0][1] if items else {}
            else:
                chart[section] = [{"id": item_id, **data} for item_id, data in items]
        return chart

    async def upsert(
        self, owner: str, client_id: str | None, patient_id: str, section: str, data: dict[str, Any], item_id: str | None
    ) -> str:
        if section not in SECTIONS:
            raise RecordsError(f"Unknown section {section!r}. Sections: {', '.join(SECTIONS)}")
        single = SECTIONS[section][1]
        if single:
            # Partial update: merge into the existing singleton.
            current = (await self.chart(owner, client_id, patient_id, purpose="update")).get(section, {})
            data = {**current, **data}
            item_id = "main"
        try:
            clean = validate_item(section, data)
        except ValueError as exc:
            raise RecordsError(str(exc)) from None
        item_id = item_id or f"{section[:3]}_{secrets.token_urlsafe(6)}"
        try:
            await self.repo.put(owner, client_id, patient_id, section, item_id, clean, 1 if single else MAX_ITEMS_PER_SECTION)
        except KeyError:
            raise RecordsError(f"No patient {patient_id!r} in this account. Use patient_list.") from None
        except ValueError as exc:
            raise RecordsError(str(exc)) from None
        return item_id

    async def import_fhir(
        self, owner: str, client_id: str | None, payload: Any, patient_id: str | None = None
    ) -> dict[str, Any]:
        """Import a FHIR Bundle into a new or existing patient. Imported demographics only fill blanks:
        what the user entered themselves is never overwritten."""
        from .fhir_import import convert

        try:
            converted = convert(payload)
        except ValueError as exc:
            raise RecordsError(str(exc)) from None
        profile = converted["profile"]
        if patient_id is None:
            patient_id = await self.create_patient(owner, client_id, profile)
            created = True
        else:
            current = (await self.chart(owner, client_id, patient_id, purpose="import")).get("profile", {})
            missing = {k: v for k, v in profile.items() if not current.get(k)}
            if missing:
                await self.upsert(owner, client_id, patient_id, "profile", missing, None)
            created = False
        counts: dict[str, int] = {}
        failed: list[dict[str, str]] = []
        for section, data, item_id, source in converted["items"]:
            try:
                await self.upsert(owner, client_id, patient_id, section, data, item_id)
                counts[section] = counts.get(section, 0) + 1
            except RecordsError as exc:
                failed.append({"resource": source, "reason": str(exc)[:300]})
        return {"patient_id": patient_id, "created_patient": created, "imported": counts,
                "skipped": converted["skipped"], "failed": failed}

    async def delete_item(self, owner: str, client_id: str | None, patient_id: str, section: str, item_id: str) -> bool:
        if section not in LIST_SECTIONS:
            raise RecordsError("Only list sections support item deletion; update singletons instead")
        return await self.repo.delete(owner, client_id, patient_id, section, item_id)

    async def erase_patient(self, owner: str, client_id: str | None, patient_id: str) -> bool:
        return await self.repo.delete_patient(owner, client_id, patient_id)

    async def audit_log(self, owner: str, limit: int = 100) -> dict[str, Any]:
        return {
            "events": await self.repo.audit_log(owner, min(max(limit, 1), 500)),
            "chain_intact": await self.repo.verify_audit_chain(owner),
        }


def _single(raw: dict[str, list], section: str) -> dict[str, Any]:
    items = raw.get(section) or []
    return items[0][1] if items else {}


def summarize(chart: dict[str, Any], today: date | None = None) -> str:
    """Compact clinical summary of a chart for the consult model (minimum necessary, no IDs)."""
    p = chart.get("profile", {})
    lines: list[str] = []
    age = age_years(p, today)
    demo = []
    if age is not None:
        demo.append(f"{age:.1f} y" if age < 2 else f"{int(age)} y")
    if p.get("sex_at_birth"):
        demo.append(f"sex at birth {p['sex_at_birth']}")
    if p.get("gender_identity"):
        demo.append(f"gender {p['gender_identity']}")
    if p.get("pregnancy_status") not in (None, "not_pregnant"):
        ga = f" ({p['gestational_weeks']} wk)" if p.get("gestational_weeks") else ""
        demo.append(f"pregnancy: {p['pregnancy_status']}{ga}")
    if p.get("breastfeeding"):
        demo.append("breastfeeding")
    if p.get("weight_kg"):
        demo.append(f"weight {p['weight_kg']} kg" + (f" ({p['weight_date']})" if p.get("weight_date") else ""))
    if p.get("height_cm"):
        demo.append(f"height {p['height_cm']} cm")
    lines.append("Patient: " + (", ".join(demo) or "demographics not recorded"))

    def items(section: str, fmt) -> None:
        rows = [fmt(x) for x in chart.get(section, [])]
        if rows:
            lines.append(f"{section.replace('_', ' ').title()}: " + "; ".join(rows))

    items("allergies", lambda a: f"{a['substance']} ({a.get('type', 'allergy')}, {a.get('severity', 'unknown')}"
          + (f", {a['reaction']}" if a.get("reaction") else "") + ")")
    if not chart.get("allergies"):
        lines.append("Allergies: none recorded (confirm with patient)")
    items("conditions", lambda c: c["name"] + ("" if c.get("status", "active") == "active" else f" [{c['status']}]"))
    items("medications", lambda m: " ".join(x for x in (m["name"], m.get("dose"), m.get("route"), m.get("frequency")) if x)
          + ("" if m.get("status", "active") == "active" else f" [{m['status']}]"))
    items("immunizations", lambda i: i["vaccine"] + (f" {i['date']}" if i.get("date") else ""))
    items("procedures", lambda x: x["name"] + (f" {x['date']}" if x.get("date") else ""))
    items("family_history", lambda f: f"{f['relation']}: {f['condition']}" + (f" (onset {f['age_at_onset']})" if f.get("age_at_onset") else ""))
    obs = sorted(chart.get("observations", []), key=lambda o: o.get("date") or "", reverse=True)[:25]
    if obs:
        lines.append("Recent observations: " + "; ".join(
            f"{o['name']} {o['value']}{(' ' + o['unit']) if o.get('unit') else ''}" + (f" ({o['date']})" if o.get("date") else "")
            for o in obs))
    s = chart.get("social_history", {})
    social = [f"{k.replace('_', ' ')}: {v}" for k, v in s.items() if k != "sexual_history"]
    if social:
        lines.append("Social: " + "; ".join(social))
    plan = [c for c in chart.get("care_plan", []) if c.get("status") in ("proposed", "accepted")]
    if plan:
        lines.append("Open care plan: " + "; ".join(f"{c['kind']}: {c['summary']}" for c in plan))
    notes = chart.get("notes", [])[-3:]
    if notes:
        lines.append("Recent notes: " + " | ".join(f"{n.get('date', '')} {n['title']}: {n['text'][:400]}" for n in notes))
    return "\n".join(lines)


def to_fhir_bundle(chart: dict[str, Any]) -> dict[str, Any]:
    """Export as a FHIR R4 collection Bundle (portable to EHRs and personal health apps)."""
    pid = chart["patient_id"]
    ref = {"reference": f"Patient/{pid}"}
    p = chart.get("profile", {})
    entries: list[dict[str, Any]] = []
    patient: dict[str, Any] = {"resourceType": "Patient", "id": pid}
    if p.get("name"):
        patient["name"] = [{"text": p["name"]}]
    if p.get("date_of_birth"):
        patient["birthDate"] = p["date_of_birth"]
    if p.get("sex_at_birth") in ("female", "male"):
        patient["gender"] = p["sex_at_birth"]
    entries.append(patient)
    for c in chart.get("conditions", []):
        entries.append({
            "resourceType": "Condition", "id": c["id"], "subject": ref, "code": {"text": c["name"]},
            "clinicalStatus": {"text": c.get("status", "active")},
            **({"onsetString": c["onset"]} if c.get("onset") else {}),
        })
    for m in chart.get("medications", []):
        entries.append({
            "resourceType": "MedicationStatement", "id": m["id"], "subject": ref,
            "status": "active" if m.get("status", "active") != "stopped" else "stopped",
            "medicationCodeableConcept": {"text": m["name"]},
            "dosage": [{"text": " ".join(x for x in (m.get("dose"), m.get("route"), m.get("frequency")) if x)}],
        })
    for a in chart.get("allergies", []):
        entries.append({
            "resourceType": "AllergyIntolerance", "id": a["id"], "patient": ref, "code": {"text": a["substance"]},
            "type": a.get("type", "allergy"), "category": [a.get("category", "medication").replace("drug", "medication")],
            **({"reaction": [{"description": a["reaction"]}]} if a.get("reaction") else {}),
        })
    for i in chart.get("immunizations", []):
        entries.append({"resourceType": "Immunization", "id": i["id"], "patient": ref, "status": "completed",
                        "vaccineCode": {"text": i["vaccine"]}, **({"occurrenceString": i["date"]} if i.get("date") else {})})
    for o in chart.get("observations", []):
        entries.append({"resourceType": "Observation", "id": o["id"], "subject": ref, "status": "final",
                        "category": [{"text": o.get("category", "lab")}], "code": {"text": o["name"]},
                        "valueString": f"{o['value']} {o.get('unit', '')}".strip(),
                        **({"effectiveDateTime": o["date"]} if o.get("date") else {})})
    for f in chart.get("family_history", []):
        entries.append({"resourceType": "FamilyMemberHistory", "id": f["id"], "patient": ref, "status": "completed",
                        "relationship": {"text": f["relation"]}, "condition": [{"code": {"text": f["condition"]}}]})
    for c in chart.get("care_plan", []):
        rtype = {"prescription_draft": "MedicationRequest", "referral_draft": "ServiceRequest"}.get(c["kind"], "CarePlan")
        res: dict[str, Any] = {"resourceType": rtype, "id": c["id"], "subject": ref, "status": "draft", "intent": "proposal",
                               "note": [{"text": c["summary"]}]}
        entries.append(res)
    return {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": e} for e in entries]}
