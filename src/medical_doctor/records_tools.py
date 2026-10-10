"""MCP tools for the personal health record, care-plan drafts (prescriptions, referrals) and prevention."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp_types import ToolAnnotations
from pydantic import Field

from . import orders, sources
from .identity import AuthRequired, Principal, current_principal
from .preventive import preventive_care
from .records.model import SECTIONS, validate_item
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
    "patient_create", "patient_update", "patient_delete_item", "patient_erase", "care_plan_update", "patient_import_fhir", "ehr_connect",
}
RECORD_TOOLS = RECORD_WRITE_TOOLS | {"my_chart", "patient_list", "patient_chart", "patient_export", "patient_access_log", "preventive_care_due"}


@dataclass
class ChartContext:
    """Which chart a conversation is about and what happened to it."""

    patient_id: str | None = None
    resolved_by: str = "none"  # given | only_profile | self_profile | created | ambiguous | disabled
    owner: str | None = None
    client_id: str | None = None
    profiles: list[dict[str, Any]] = field(default_factory=list)
    saved: dict[str, Any] | None = None
    reason: str = ""

    def public(self) -> dict[str, Any]:
        out: dict[str, Any] = {"patient_id": self.patient_id, "resolved_by": self.resolved_by}
        if self.resolved_by == "created":
            out["note"] = "First visit: a new chart was opened for this person. Tell them it is encrypted and private."
        if self.resolved_by == "ambiguous":
            out["profiles"] = self.profiles
            out["note"] = "This account has several profiles and none is 'self': pass patient_id to consult."
        if self.saved is not None:
            out["saved"] = self.saved
        if self.reason:
            out["note"] = self.reason
        return out


def chart_suggestions(structured: dict[str, Any]) -> list[dict[str, Any]]:
    """Facts the patient stated (from the consult summary), validated against the chart schema."""
    out = []
    for fact in structured.get("patient_facts") or []:
        section, data = fact.get("section"), fact.get("data")
        if section not in SECTIONS or section in ("care_plan", "notes") or not isinstance(data, dict):
            continue
        try:
            out.append({"section": section, "data": validate_item(section, data)})
        except ValueError:
            continue
    return out[:40]


def visit_note(case: str, question: str | None, structured: dict[str, Any]) -> dict[str, Any]:
    ddx = "; ".join(
        f"{d.get('diagnosis')} ({d.get('likelihood', '')}{', cannot miss' if d.get('cant_miss') else ''})"
        for d in (structured.get("differential") or [])[:6]
    )
    parts = [f"Patient reported:\n{case.strip()[:12000]}"]
    if question:
        parts.append(f"Question: {question.strip()[:2000]}")
    if structured.get("urgency"):
        parts.append(f"Urgency: {structured['urgency']}" + (f" ({structured.get('urgency_reason')})" if structured.get("urgency_reason") else ""))
    if ddx:
        parts.append(f"Differential: {ddx}")
    for label, key in (("Recommended evaluation", "recommended_evaluation"), ("Management", "management"),
                       ("Return precautions", "return_precautions")):
        if items := structured.get(key):
            parts.append(f"{label}: " + "; ".join(str(x) for x in items[:8]))
    title = (structured.get("problem_representation") or case.strip().split("\n")[0])[:150]
    return {"title": f"Consult: {title}", "text": "\n\n".join(parts)[:20000], "date": date.today().isoformat()}


def reported_case(note_text: str) -> str:
    """Only what the patient reported in a visit note: the text after 'Patient reported:' and before the
    note's own conclusions (Question/Urgency/Differential/...). Used to keep a second opinion blinded."""
    text = note_text.split("Patient reported:", 1)[-1]
    for marker in ("\n\nQuestion:", "\n\nUrgency:", "\n\nDifferential:", "\n\nRecommended evaluation:",
                   "\n\nManagement:", "\n\nReturn precautions:"):
        text = text.split(marker, 1)[0]
    return text.strip()


class AutoChart:
    """Server-side chart memory: resolve (and on first visit open) the patient's chart before a consult,
    and save what the patient stated plus a visit note after it. The client model cannot forget to."""

    def __init__(self, records: "Records", enabled: bool):
        self.records = records
        self.enabled = enabled

    async def resolve(self, patient_id: str | None, display_name: str | None) -> ChartContext:
        if self.records.service is None:
            if patient_id:  # asked for a chart that cannot be read: say so instead of silently skipping it
                raise ToolError(self.records.disabled_reason or "Patient records are not enabled on this server.")
            return ChartContext(resolved_by="disabled")
        if not (self.enabled or patient_id):
            return ChartContext(resolved_by="disabled")
        try:
            p = self.records.who()
        except ToolError:
            if patient_id:
                raise
            return ChartContext(resolved_by="disabled")
        svc = self.records.service
        if not self.enabled:  # explicit patient_id only
            return ChartContext(patient_id=patient_id, resolved_by="given", owner=p.owner, client_id=p.client_id)
        try:
            pid, how = await svc.resolve_patient(p.owner, p.client_id, patient_id, display_name)
        except RecordsError as exc:
            return ChartContext(resolved_by="disabled", reason=str(exc))
        ctx = ChartContext(patient_id=pid, resolved_by=how, owner=p.owner, client_id=p.client_id)
        if how == "ambiguous":
            ctx.profiles = await svc.list_patients(p.owner, p.client_id)
        return ctx

    async def save_note(self, ctx: ChartContext, structured: dict[str, Any], note: dict[str, Any]) -> None:
        """File a dated note (e.g. a second opinion) in the resolved chart, with the same someone-else guard."""
        if not (self.enabled and ctx.patient_id and ctx.owner):
            return
        if ctx.resolved_by != "given" and structured.get("patient_is_user") is False:
            ctx.reason = "About someone else: not filed in this chart. Pass that person's patient_id."
            return
        try:
            ctx.saved = await self.records.service.record_facts(  # type: ignore[union-attr]
                ctx.owner, ctx.client_id, ctx.patient_id, [], {**note, "date": date.today().isoformat()}
            )
        except RecordsError as exc:
            ctx.reason = f"Chart not updated: {exc}"

    async def save_visit(self, ctx: ChartContext, case: str, question: str | None, structured: dict[str, Any]) -> None:
        if not (self.enabled and ctx.patient_id and ctx.owner):
            return
        if ctx.resolved_by != "given" and structured.get("patient_is_user") is False:
            ctx.saved = {"saved": {}, "rejected": []}
            ctx.reason = ("This consult was about someone else (e.g. a child or parent), so it was not filed in this "
                          "chart. Create their profile with patient_create (relationship) and pass its patient_id.")
            return
        try:
            ctx.saved = await self.records.service.record_facts(  # type: ignore[union-attr]
                ctx.owner, ctx.client_id, ctx.patient_id, chart_suggestions(structured), visit_note(case, question, structured)
            )
        except RecordsError as exc:
            ctx.reason = f"Chart not updated: {exc}"


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


def register_ehr_tool(mcp: MCPServer, records: Records, connector: Any) -> None:
    from .ehr import VENDORS, EhrError

    @mcp.tool(annotations=WRITE, title="Connect patient portal (MyChart etc.)")
    async def ehr_connect(
        patient_id: PatientId,
        fhir_base_url: Annotated[str, Field(max_length=500)],
        portal: Literal["epic", "oracle", "athena", "smart"] = "epic",
    ) -> dict[str, Any]:
        """Import the patient's records straight from their health system's patient portal: Epic MyChart,
        Oracle Health (Cerner), athenahealth, or any SMART on FHIR portal. Returns a link the user opens in a
        browser to sign in to the portal and approve read access; conditions, medications, allergies, labs
        (including Labcorp/Quest results the health system received), vitals, immunizations and procedures are
        then imported into profile `patient_id`. fhir_base_url is the health system's FHIR R4 patient API base
        (from the health system or Epic's endpoint directory); use "sandbox" with portal="epic" to try Epic's
        test patients. Give the link to the user; never open it yourself."""
        p = records.who()
        chart = await records.chart(patient_id, purpose="ehr_connect")
        label = chart.get("profile", {}).get("name") or patient_id
        token = get_access_token()
        account = ((token.claims or {}).get("email") if token else None) or "this account"
        try:
            out = await connector.start(owner=p.owner, client_id=p.client_id, account=account, patient_id=patient_id,
                                        patient_label=label, vendor=portal, fhir_base_url=fhir_base_url)
        except EhrError as exc:
            raise ToolError(str(exc)) from None
        out["instructions"] = (f"Open connect_url in a browser within 10 minutes, sign in to {VENDORS[portal]} and approve "
                               "access. The page confirms what was imported; then review the chart with patient_chart.")
        return out


def register_chart_memory_tools(mcp: MCPServer, records: Records, charting: AutoChart) -> None:
    from .records.service import summarize

    def _display_name() -> str | None:
        token = get_access_token()
        return (token.claims or {}).get("name") if token else None

    @mcp.tool(annotations=WRITE, title="My chart (start every conversation here)")
    async def my_chart(patient_id: PatientId | None = None) -> dict[str, Any]:
        """Call this FIRST, automatically and without being asked, whenever the user mentions anything about
        their health, symptoms, medicines or results: this is their personal doctor's record of them. Returns the person's chart summary (history, medicines,
        allergies, results, recent visit notes, open care items) so you can treat them as a returning patient.
        On a first visit it opens a new encrypted chart for them. With several family profiles, pass patient_id
        (the list is returned). Then save everything they tell you with chart_record."""
        ctx = await charting.resolve(patient_id, _display_name())
        if ctx.resolved_by == "disabled":
            raise ToolError(ctx.reason or records.disabled_reason or "Patient records are not enabled for this sign-in.")
        if not ctx.patient_id:
            return ctx.public()
        chart = await records.chart(ctx.patient_id, purpose="read")
        notes = sorted(chart.get("notes", []), key=lambda n: n.get("date") or "", reverse=True)[:5]
        return {
            **ctx.public(),
            "first_visit": ctx.resolved_by == "created",
            "summary": summarize(chart),
            "recent_visits": [{"date": n.get("date"), "title": n.get("title")} for n in notes],
            "profiles": await records.service.list_patients(ctx.owner, ctx.client_id),  # type: ignore[union-attr]
        }

    @mcp.tool(annotations=WRITE, title="Record in chart")
    async def chart_record(
        facts: Annotated[list[dict[str, Any]], Field(max_length=60)],
        patient_id: PatientId | None = None,
        note: Annotated[str | None, Field(max_length=20_000)] = None,
    ) -> dict[str, Any]:
        """Call automatically, without being asked, every time the user shares anything about their health. One call
        can save many facts; each is {"section": ..., "data": {...}} using the patient_update field names
        (profile, social_history, conditions, medications, allergies, immunizations, procedures,
        family_history, observations). Facts about the same thing (same medicine, allergy, condition ...) update
        the existing entry instead of duplicating it. `note` adds a dated visit note (what they reported, in
        their words). Without patient_id the person's own chart is used (opened on first visit). Save only
        what they stated or confirmed, never your hypotheses."""
        ctx = await charting.resolve(patient_id, _display_name())
        if not ctx.patient_id or not ctx.owner:
            if ctx.resolved_by == "ambiguous":
                return ctx.public()
            raise ToolError(ctx.reason or records.disabled_reason or "Patient records are not enabled for this sign-in.")
        visit = {"title": "Patient update", "text": note, "date": date.today().isoformat()} if note else None
        try:
            saved = await records.service.record_facts(ctx.owner, ctx.client_id, ctx.patient_id, facts, visit)  # type: ignore[union-attr]
        except RecordsError as exc:
            raise ToolError(str(exc)) from None
        return {**ctx.public(), **saved}
