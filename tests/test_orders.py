"""Prescription / referral drafts: deterministic safety checks and calls to action."""

from datetime import date, timedelta

import pytest

from medical_doctor import orders


def dob(years: float) -> str:
    return (date.today() - timedelta(days=int(years * 365.25))).isoformat()


def chart(**over):
    base = {
        "profile": {"date_of_birth": dob(40), "sex_at_birth": "female"},
        "allergies": [], "medications": [], "observations": [], "conditions": [],
    }
    for k, v in over.items():
        base[k] = {**base[k], **v} if isinstance(v, dict) else v
    return base


async def no_interactions(drugs):
    return {"interactions": []}


async def checks_for(drug, c, normalized=None, interactions=no_interactions):
    checks, blocked = await orders.check_prescription(drug, c, normalized, interactions)
    return {x["check"]: x["result"] for x in checks}, blocked, checks


@pytest.mark.parametrize("drug", ["oxycodone 5 mg", "Adderall", "alprazolam", "testosterone cypionate", "zolpidem"])
async def test_controlled_substances_are_never_drafted(drug):
    by, blocked, _ = await checks_for(drug, chart())
    assert blocked and by["controlled_substance"] == "fail"


async def test_controlled_detected_via_rxnorm_ingredients():
    by, blocked, _ = await checks_for("Percocet", chart(), {"found": True, "rxcui": "1", "ingredients": ["acetaminophen", "oxycodone"]})
    assert blocked


async def test_penicillin_allergy_blocks_amoxicillin_and_warns_cephalosporin():
    c = chart(allergies=[{"substance": "Penicillin", "severity": "anaphylaxis", "reaction": "throat swelling"}])
    _, blocked, _ = await checks_for("amoxicillin", c)
    assert blocked
    by, blocked, _ = await checks_for("cephalexin", c)
    assert not blocked and by["allergy_cross_reactivity"] == "warn"


async def test_sulfa_allergy_blocks_combination_product():
    c = chart(allergies=[{"substance": "sulfa drugs", "severity": "severe"}])
    _, blocked, _ = await checks_for("trimethoprim-sulfamethoxazole", c)
    assert blocked


async def test_intolerance_only_warns():
    c = chart(allergies=[{"substance": "ibuprofen", "type": "intolerance", "severity": "mild", "reaction": "stomach upset"}])
    by, blocked, _ = await checks_for("naproxen", c)
    assert not blocked and by["allergy"] == "warn"


async def test_allergy_to_member_drug_blocks_its_class():
    c = chart(allergies=[{"substance": "amoxicillin", "severity": "moderate", "reaction": "hives"}])
    _, blocked, _ = await checks_for("ampicillin", c)
    assert blocked


async def test_pregnancy_teratogen_blocked_and_ace_warned():
    c = chart(profile={"pregnancy_status": "pregnant"})
    _, blocked, _ = await checks_for("isotretinoin", c)
    assert blocked
    by, blocked, _ = await checks_for("lisinopril", c)
    assert not blocked and by["pregnancy"] == "warn"


async def test_child_without_weight_blocked_and_aspirin_blocked():
    _, blocked, _ = await checks_for("amoxicillin", chart(profile={"date_of_birth": dob(4)}))
    assert blocked
    by, blocked, _ = await checks_for("aspirin", chart(profile={"date_of_birth": dob(10), "weight_kg": 30}))
    assert blocked and by["pediatric_contraindication"] == "fail"
    by, blocked, _ = await checks_for("ciprofloxacin", chart(profile={"date_of_birth": dob(10), "weight_kg": 30}))
    assert not blocked and by["pediatric_contraindication"] == "warn"


async def test_beers_and_renal_and_duplicates():
    older = chart(profile={"date_of_birth": dob(78)}, observations=[{"name": "eGFR", "value": "24", "date": "2025-09-01"}],
                  medications=[{"name": "sertraline 50 mg", "status": "active"}])
    by, _, _ = await checks_for("diphenhydramine", older)
    assert by["beers_criteria"] == "warn"
    _, blocked, _ = await checks_for("metformin", older)
    assert blocked
    by, _, _ = await checks_for("escitalopram", older)
    assert by["duplicate_class"] == "warn"


async def test_interaction_source_failure_is_not_a_pass():
    async def down(drugs):
        raise ConnectionError("openFDA unreachable")

    c = chart(medications=[{"name": "warfarin"}])
    by, _, _ = await checks_for("fluconazole", c, interactions=down)
    assert by["interactions"] == "not_checked"


async def test_no_chart_means_not_checked():
    by, blocked, _ = await checks_for("amoxicillin", None)
    assert not blocked and by["patient_chart"] == "not_checked"


def test_prescription_draft_is_a_proposal_with_ctas():
    d = orders.prescription_draft(drug="amoxicillin", dose="500 mg", route="oral", frequency="three times daily",
                                  duration="5 days", quantity="15", refills=0, indication="community-acquired pneumonia",
                                  instructions=None, normalized=None, checks=[], blocked=False)
    assert d["draft"]["status"] == "draft" and d["draft"]["intent"] == "proposal"
    assert "not a prescription" in d["disclaimer"].lower()
    assert "clinician" in d["call_to_action"]["for_patient"]


def test_referral_emergency_redirect_cannot_be_downgraded():
    r = orders.referral_draft(specialty="cardiology", reason="crushing chest pain radiating to left arm", urgency="routine",
                              clinical_question=None, workup_before_visit=None, chart_summary=None)
    assert r["status"] == "emergency_redirect" and r["urgency"] == "emergency"
    assert "emergency" in r["call_to_action"]["for_patient"].lower()


def test_referral_letter_includes_history_and_question():
    r = orders.referral_draft(specialty="endocrinology", reason="HbA1c 9.8% despite metformin", urgency="soon",
                              clinical_question="Add GLP-1 RA vs basal insulin?", workup_before_visit=["UACR", "lipids"],
                              chart_summary="Patient: 55 y")
    assert r["draft"]["priority"] == "asap" and "Add GLP-1" in r["referral_letter"] and "Patient: 55 y" in r["referral_letter"]
