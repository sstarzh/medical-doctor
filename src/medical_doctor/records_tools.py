"""MCP tools for the personal health record, care-plan drafts (prescriptions, referrals) and prevention."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from . import orders, sources
from .identity import AuthRequired, Principal, current_principal
from .preventive import preventive_care
from .records.model import SECTIONS
from .records.service import PatientService, RecordsError, summarize, to_fhir_bundle

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
ERASE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)
DRAFT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)

Section = Literal[
    "profile", "social_history", "conditions", "medications", "allergies", "immunizations",
    "procedures", "family_history", "observations", "notes", "care_plan",
]
PatientId = Annotated[str, Field(max_length=40, pattern=r"^pt_[A-Za-z0-9_-]+$")]

# Tools that write or erase patient data are never offered to the consult model.
RECORD_WRITE_TOOLS = {
    "patient_create", "patient_update", "patient_delete_item", "patient_erase", "care_plan_update", "patient_import_fhir",
}
RECORD_TOOLS = RECORD_WRITE_TOOLS | {"patient_list", "patient_chart", "patient_export", "patient_access_log", "preventive_care_due"}


class Records:
    """Holds the service and resolves the caller; shared with the consult tool."""

    def __init__(self, service: PatientService | None, local_mode: bool, disabled_reason: str = ""):
        self.service = service
        self.local_mode = local_mode
        self.disabled_reason = disabled_reason

    def who(self) -> Principal:
        if self.service is None:
            raise ToolError(self.disabled_reason or "Patient records are not enabled on this server.")
        try:
            p = current_principal(self.local_mode)
        except AuthRequired as exc:
            raise ToolError(str(exc)) from None
        if not p.personal:
            raise ToolError("Patient records need a personal sign-in; shared API keys cannot own charts.")
        return p

    async def chart(self, patient_id: str, purpose: str = "read") -> dict[str, Any]:
        p = self.who()
        try:
            return await self.service.chart(p.owner, p.client_id, patient_id, purpose)  # type: ignore[union-attr]
        except RecordsError as exc:
            raise ToolError(str(exc)) from None


def register_record_tools(mcp: MCPServer, records: Records) -> None:
    def svc() -> tuple[PatientService, Principal]:
        p = records.who()
        return records.service, p  # type: ignore[return-value]

    async def run(coro):
        try:
            return await coro
        except RecordsError as exc:
            raise ToolError(str(exc)) from None

    @mcp.tool(annotations=READ, title="List patient profiles")
    async def patient_list() -> list[dict[str, Any]]:
        """List the patient profiles in this account (you and family members you manage), with age."""
        s, p = svc()
        return await run(s.list_patients(p.owner, p.client_id))

    @mcp.tool(annotations=WRITE, title="Create patient profile")
    async def patient_create(profile: dict[str, Any]) -> dict[str, Any]:
        """Create a patient profile (yourself or a family member) for the personal-doctor chart.
        `profile` fields: name, relationship (self|child|partner|parent|other), date_of_birth (YYYY-MM-DD),
        sex_at_birth, gender_identity, pronouns, pregnancy_status, gestational_weeks, breastfeeding,
        weight_kg, height_cm, weight_date, blood_type, preferred_language, primary_clinician,
        preferred_pharmacy, emergency_contact. Data is encrypted and private to this account."""
        s, p = svc()
        pid = await run(s.create_patient(p.owner, p.client_id, profile))
        return {"patient_id": pid}

    @mcp.tool(annotations=READ, title="Read patient chart")
    async def patient_chart(patient_id: PatientId, summary_only: bool = False) -> dict[str, Any]:
        """The full chart for a patient profile (profile, social history, conditions, medications, allergies,
        immunizations, procedures, family history, observations, notes, care plan), or a compact clinical
        summary with summary_only=true. Read it at the start of every visit."""
        chart = await records.chart(patient_id)
        if summary_only:
            return {"patient_id": patient_id, "summary": summarize(chart)}
        return chart

    @mcp.tool(annotations=WRITE, title="Update patient chart")
    async def patient_update(
        patient_id: PatientId, section: Section, data: dict[str, Any], item_id: str | None = None
    ) -> dict[str, Any]:
        """Add or update one chart entry with facts the patient stated or confirmed (never hypotheses).
        Singleton sections (profile, social_history) merge `data` into the existing entry. List sections
        (conditions, medications, allergies, immunizations, procedures, family_history, observations, notes,
        care_plan) add a new entry, or replace entry `item_id`. Field names per section:
        conditions{name,code,status,onset,notes}; medications{name,dose,route,frequency,indication,status,started,prescriber,notes};
        allergies{substance,reaction,severity,category,type,notes}; immunizations{vaccine,date,dose_number,notes};
        procedures{name,date,notes}; family_history{relation,condition,age_at_onset,notes};
        observations{name,value,unit,date,category,reference_range,notes}; notes{title,text,date};
        social_history{tobacco,pack_years,quit_year,vaping,alcohol_drinks_per_week,substances,occupation,...}."""
        s, p = svc()
        item = await run(s.upsert(p.owner, p.client_id, patient_id, section, data, item_id))
        return {"patient_id": patient_id, "section": section, "item_id": item}

    @mcp.tool(annotations=WRITE, title="Import records (FHIR)")
    async def patient_import_fhir(bundle: dict[str, Any] | list[dict[str, Any]], patient_id: PatientId | None = None) -> dict[str, Any]:
        """Import health records exported from a hospital/clinic EHR or health app as FHIR R4 JSON (a Bundle, e.g.
        from a patient portal "download my records", Apple Health / Health Connect FHIR export, or a SMART on FHIR
        Patient/$everything): conditions, medications, allergies, labs and vitals, immunizations, procedures and
        family history. Without patient_id a new profile is created from the Patient resource; with it, records
        are added to that profile and demographics only fill blanks. Re-importing the same export updates entries
        rather than duplicating them. Returns counts per section and every skipped resource with the reason.
        Ask the user to review the imported chart afterwards."""
        s, p = svc()
        return await run(s.import_fhir(p.owner, p.client_id, bundle, patient_id))

    @mcp.tool(annotations=ERASE, title="Delete chart entry")
    async def patient_delete_item(patient_id: PatientId, section: Section, item_id: str) -> dict[str, Any]:
        """Delete one entry from a list section (e.g. a medication entered by mistake). To record that a
        medicine was stopped, update its status to "stopped" instead, which keeps the history."""
        s, p = svc()
        return {"deleted": await run(s.delete_item(p.owner, p.client_id, patient_id, section, item_id))}

    @mcp.tool(annotations=ERASE, title="Erase patient profile")
    async def patient_erase(patient_id: PatientId, confirm: Literal["ERASE"]) -> dict[str, Any]:
        """Permanently erase a patient profile and its whole chart (crypto-shredding: the profile's
        encryption key is destroyed, so no copy can be recovered). Requires confirm="ERASE".
        Ask the user to confirm explicitly before calling."""
        s, p = svc()
        return {"erased": await run(s.erase_patient(p.owner, p.client_id, patient_id))}

    @mcp.tool(annotations=READ, title="Export chart (FHIR)")
    async def patient_export(patient_id: PatientId) -> dict[str, Any]:
        """Export the chart as a FHIR R4 Bundle (JSON) to share with a clinician, EHR or health app."""
        return to_fhir_bundle(await records.chart(patient_id, purpose="export"))

    @mcp.tool(annotations=READ, title="Record access log")
    async def patient_access_log(limit: int = 100) -> dict[str, Any]:
        """Who accessed or changed records in this account (tamper-evident, hash-chained audit trail)."""
        s, p = svc()
        return await s.audit_log(p.owner, limit)

    @mcp.tool(annotations=READ, title="Preventive care due")
    async def preventive_care_due(patient_id: PatientId) -> dict[str, Any]:
        """Age-, sex- and risk-based preventive care checklist (screenings, vaccines, counseling) from
        USPSTF A/B, CDC/ACIP and AAP Bright Futures, personalized from the chart."""
        return preventive_care(await records.chart(patient_id, purpose="preventive"))

    @mcp.tool(annotations=WRITE, title="Update care plan item")
    async def care_plan_update(
        patient_id: PatientId, item_id: str, status: Literal["accepted", "completed", "declined", "cancelled"]
    ) -> dict[str, Any]:
        """Update the status of a care-plan item (e.g. the referral was made, the draft was declined)."""
        chart = await records.chart(patient_id, purpose="update")
        item = next((c for c in chart.get("care_plan", []) if c["id"] == item_id), None)
        if item is None:
            raise ToolError(f"No care-plan item {item_id}")
        s, p = svc()
        data = {k: v for k, v in item.items() if k != "id"} | {"status": status}
        await run(s.upsert(p.owner, p.client_id, patient_id, "care_plan", data, item_id))
        return {"item_id": item_id, "status": status}


def register_order_tools(mcp: MCPServer, records: Records) -> None:
    async def save(patient_id: str, kind: str, summary: str, result: dict[str, Any]) -> str:
        s, p = records.service, records.who()
        return await s.upsert(  # type: ignore[union-attr]
            p.owner, p.client_id, patient_id, "care_plan",
            {"kind": kind, "status": "proposed", "summary": summary[:500], "details": result}, None,
        )

    @mcp.tool(annotations=DRAFT, title="Draft prescription (call to action)")
    async def draft_prescription(
        drug: Annotated[str, Field(max_length=120)],
        dose: Annotated[str, Field(max_length=80)],
        route: Annotated[str, Field(max_length=40)],
        frequency: Annotated[str, Field(max_length=80)],
        indication: Annotated[str, Field(max_length=300)],
        duration: Annotated[str | None, Field(max_length=60)] = None,
        quantity: Annotated[str | None, Field(max_length=40)] = None,
        refills: Annotated[int, Field(ge=0, le=11)] = 0,
        instructions: Annotated[str | None, Field(max_length=500)] = None,
        patient_id: PatientId | None = None,
        save: bool = False,
    ) -> dict[str, Any]:
        """Prepare a PRESCRIPTION DRAFT for a licensed clinician to review — never a prescription.
        Runs deterministic safety checks against the patient's chart (allergies and cross-reactivity,
        duplicate therapy, label interactions with current medicines, pregnancy/lactation, pediatric age and
        weight, Beers criteria, kidney function) and refuses controlled substances. Returns a FHIR
        MedicationRequest (status=draft, intent=proposal), the checks, and calls to action for the patient
        and the clinician. Verify the dose against drug_label first. save=true adds it to the care plan."""
        try:
            normalized = await sources.normalize_drug(drug)
        except Exception:
            normalized = None
        chart = await records.chart(patient_id, purpose="prescription_check") if patient_id else None
        checks, blocked = await orders.check_prescription(drug, chart, normalized, sources.drug_interactions)
        result = orders.prescription_draft(
            drug=drug, dose=dose, route=route, frequency=frequency, duration=duration, quantity=quantity,
            refills=refills, indication=indication, instructions=instructions, normalized=normalized,
            checks=checks, blocked=blocked,
        )
        if save and patient_id and not blocked:
            result["care_plan_item_id"] = await save_(patient_id, "prescription_draft", f"{drug} {dose} {frequency} for {indication}", result)
        return result

    @mcp.tool(annotations=DRAFT, title="Draft referral (call to action)")
    async def draft_referral(
        specialty: Annotated[str, Field(max_length=80)],
        reason: Annotated[str, Field(max_length=2000)],
        urgency: Literal["urgent", "soon", "routine"] | None = None,
        clinical_question: Annotated[str | None, Field(max_length=1000)] = None,
        workup_before_visit: Annotated[list[Annotated[str, Field(max_length=200)]] | None, Field(max_length=15)] = None,
        patient_id: PatientId | None = None,
        save: bool = False,
    ) -> dict[str, Any]:
        """Prepare a REFERRAL DRAFT (FHIR ServiceRequest, status=draft) with a referral letter and calls to
        action. Emergencies are redirected to emergency care instead of a referral. With patient_id the
        pertinent history is included. save=true adds it to the care plan."""
        summary = summarize(await records.chart(patient_id, purpose="referral")) if patient_id else None
        result = orders.referral_draft(
            specialty=specialty, reason=reason, urgency=urgency, clinical_question=clinical_question,
            workup_before_visit=workup_before_visit, chart_summary=summary,
        )
        if save and patient_id and result["status"] == "draft_ready":
            result["care_plan_item_id"] = await save_(patient_id, "referral_draft", f"Referral to {specialty}: {reason}", result)
        return result

    save_ = save


SECTION_NAMES = list(SECTIONS)
