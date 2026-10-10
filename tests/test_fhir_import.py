"""FHIR R4 import into the personal chart."""

import pytest
from mcp import Client

from medical_doctor.records.fhir_import import convert
from medical_doctor.server import mcp
from tests.test_records import make

SNOMED, LOINC, RXNORM = "http://snomed.info/sct", "http://loinc.org", "http://www.nlm.nih.gov/research/umls/rxnorm"
PT = {"reference": "Patient/p1"}

BUNDLE = {
    "resourceType": "Bundle", "type": "searchset",
    "entry": [{"resource": r} for r in [
        {"resourceType": "Patient", "id": "p1", "name": [{"use": "official", "given": ["Maria"], "family": "Lopez"}],
         "birthDate": "1961-04-12", "gender": "female"},
        {"resourceType": "Condition", "id": "c1", "subject": PT,
         "code": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "E11.9", "display": "Type 2 diabetes mellitus"}]},
         "clinicalStatus": {"coding": [{"code": "active"}]}, "onsetDateTime": "2015-06-01"},
        {"resourceType": "Condition", "id": "c2", "subject": PT, "code": {"text": "Lupus"},
         "verificationStatus": {"coding": [{"code": "refuted"}]}},
        {"resourceType": "Medication", "id": "m1", "code": {"coding": [{"system": RXNORM, "code": "860975", "display": "metformin 500 MG Oral Tablet"}]}},
        {"resourceType": "MedicationRequest", "id": "mr1", "subject": PT, "status": "active", "intent": "order",
         "medicationReference": {"reference": "Medication/m1"}, "authoredOn": "2024-01-10",
         "dosageInstruction": [{"text": "500 mg twice daily with meals"}], "requester": {"display": "Dr. Chen"}},
        {"resourceType": "MedicationStatement", "id": "ms1", "subject": PT, "status": "completed",
         "medicationCodeableConcept": {"text": "lisinopril 10 mg"}},
        {"resourceType": "MedicationRequest", "id": "mr2", "subject": PT, "status": "active", "intent": "proposal",
         "medicationCodeableConcept": {"text": "semaglutide"}},
        {"resourceType": "AllergyIntolerance", "id": "a1", "patient": PT, "code": {"text": "Penicillin"}, "category": ["medication"],
         "reaction": [{"manifestation": [{"text": "Anaphylaxis"}], "severity": "severe"}]},
        {"resourceType": "Observation", "id": "o1", "subject": PT, "status": "final",
         "category": [{"coding": [{"code": "laboratory"}]}], "code": {"coding": [{"system": LOINC, "code": "4548-4", "display": "Hemoglobin A1c"}]},
         "valueQuantity": {"value": 8.1, "unit": "%"}, "effectiveDateTime": "2025-08-01",
         "referenceRange": [{"low": {"value": 4.0}, "high": {"value": 5.6}}]},
        {"resourceType": "Observation", "id": "o2", "subject": PT, "status": "final",
         "category": [{"coding": [{"code": "vital-signs"}]}], "code": {"text": "Blood pressure panel"},
         "component": [{"code": {"coding": [{"system": LOINC, "code": "8480-6"}]}, "valueQuantity": {"value": 142, "unit": "mm[Hg]"}},
                       {"code": {"coding": [{"system": LOINC, "code": "8462-4"}]}, "valueQuantity": {"value": 88, "unit": "mm[Hg]"}}]},
        {"resourceType": "Observation", "id": "o3", "subject": PT, "status": "entered-in-error", "code": {"text": "Potassium"},
         "valueQuantity": {"value": 9.9}},
        {"resourceType": "Observation", "id": "o4", "subject": {"reference": "Patient/someone-else"}, "code": {"text": "eGFR"},
         "valueQuantity": {"value": 30}},
        {"resourceType": "Immunization", "id": "i1", "patient": PT, "status": "completed", "vaccineCode": {"text": "Influenza"},
         "occurrenceDateTime": "2025-10-01"},
        {"resourceType": "Immunization", "id": "i2", "patient": PT, "status": "not-done", "vaccineCode": {"text": "Zoster"}},
        {"resourceType": "Procedure", "id": "pr1", "subject": PT, "status": "completed", "code": {"text": "Colonoscopy"},
         "performedDateTime": "2022-03-03"},
        {"resourceType": "FamilyMemberHistory", "id": "f1", "patient": PT, "status": "completed", "relationship": {"text": "mother"},
         "condition": [{"code": {"text": "breast cancer"}, "onsetAge": {"value": 52}}]},
        {"resourceType": "Encounter", "id": "e1", "subject": PT},
    ]],
}


def test_convert_maps_resources_and_reports_skips():
    out = convert(BUNDLE)
    assert out["profile"] == {"name": "Maria Lopez", "date_of_birth": "1961-04-12", "sex_at_birth": "female"}
    by = {}
    for section, data, _, _ in out["items"]:
        by.setdefault(section, []).append(data)
    assert by["conditions"] == [{"name": "Type 2 diabetes mellitus", "code": "ICD-10:E11.9", "status": "active", "onset": "2015-06-01"}]
    meds = {m["name"]: m for m in by["medications"]}
    assert meds["metformin 500 MG Oral Tablet"]["dose"] == "500 mg twice daily with meals"
    assert meds["metformin 500 MG Oral Tablet"]["prescriber"] == "Dr. Chen" and meds["lisinopril 10 mg"]["status"] == "stopped"
    assert by["allergies"][0]["severity"] == "anaphylaxis" and by["allergies"][0]["category"] == "drug"
    obs = {o["name"]: o for o in by["observations"]}
    assert obs["Hemoglobin A1c"]["value"] == "8.1" and obs["Hemoglobin A1c"]["reference_range"] == "4-5.6"
    assert obs["blood pressure"]["value"] == "142/88" and obs["blood pressure"]["category"] == "vital"
    assert by["family_history"][0] == {"relation": "mother", "condition": "breast cancer", "age_at_onset": 52}
    reasons = {s["resource"]: s["reason"] for s in out["skipped"]}
    assert set(reasons) == {"Condition/c2", "MedicationRequest/mr2", "Observation/o3", "Observation/o4", "Immunization/i2", "Encounter/e1"}
    assert reasons["Observation/o4"] == "belongs to a different patient"


def test_multiple_patients_refused():
    with pytest.raises(ValueError, match="more than one Patient"):
        convert([{"resourceType": "Patient", "id": "a"}, {"resourceType": "Patient", "id": "b"}])


async def test_import_is_idempotent_and_never_overwrites_user_demographics():
    _, s = make()
    first = await s.import_fhir("alice", "c", BUNDLE)
    pid = first["patient_id"]
    assert first["created_patient"] and first["imported"]["medications"] == 2 and not first["failed"]
    again = await s.import_fhir("alice", "c", BUNDLE, pid)
    chart = await s.chart("alice", "c", pid)
    assert len(chart["medications"]) == 2 and len(chart["observations"]) == 2 and not again["created_patient"]
    await s.upsert("alice", "c", pid, "profile", {"name": "Mari"}, None)
    await s.import_fhir("alice", "c", BUNDLE, pid)
    assert (await s.chart("alice", "c", pid))["profile"]["name"] == "Mari"


async def test_import_tool_end_to_end():
    async with Client(mcp) as c:
        r = await c.call_tool("patient_import_fhir", {"bundle": BUNDLE})
        out = r.structured_content
        pid = out["patient_id"]
        chart = (await c.call_tool("patient_chart", {"patient_id": pid, "summary_only": True})).structured_content
        assert "metformin" in chart["summary"] and "Penicillin" in chart["summary"]
        await c.call_tool("patient_erase", {"patient_id": pid, "confirm": "ERASE"})
