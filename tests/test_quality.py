"""Deterministic self-check, one-shot revision, and auto-escalation."""

import json

import pytest

from medical_doctor import physician, quality
from medical_doctor.config import Settings
from medical_doctor.safety import screen
from tests.test_providers import StubEngine, echo


def note(structured):
    return "## Urgency\nURGENT\n\nbody\n\n```json\n" + json.dumps(structured) + "\n```"


def test_missed_cant_miss_and_allergy_conflict_are_critical():
    triage = screen("crushing chest pain radiating to the left arm").to_dict()
    structured = {"source": "model", "urgency": "emergency",
                  "differential": [{"diagnosis": "GERD", "likelihood": "likely"}],
                  "management": ["Start amoxicillin 500 mg three times daily.", "Avoid penicillin given allergy."]}
    chart = {"profile": {"date_of_birth": "1970-01-01"}, "allergies": [{"substance": "penicillin", "severity": "anaphylaxis"}]}
    r = quality.review(structured, "GERD likely", triage, chart)
    checks = {f["check"]: f["severity"] for f in r["findings"]}
    assert checks == {"cant_miss": "critical", "allergy_conflict": "critical"} and r["critical"] == 2
    assert r["suggest_second_opinion"]


def test_clean_note_passes_and_negated_drug_is_not_flagged():
    triage = screen("cough for three days").to_dict()
    structured = {"source": "model", "urgency": "routine", "differential": [{"diagnosis": "viral bronchitis", "probability_pct": 70}],
                  "management": ["Do not use amoxicillin; antibiotics are not indicated.", "Honey and fluids."]}
    chart = {"profile": {}, "allergies": [{"substance": "amoxicillin", "severity": "moderate"}]}
    r = quality.review(structured, "viral", triage, chart)
    assert r["findings"] == [] and not r["suggest_second_opinion"]


def test_pediatric_dose_without_weight_and_uncertainty():
    structured = {"source": "model", "urgency": "soon", "management": ["Ibuprofen 200 mg every 6 hours"],
                  "differential": [{"diagnosis": d, "likelihood": "possible"} for d in "abcd"]}
    r = quality.review(structured, "", {"urgency": "routine"}, {"profile": {"date_of_birth": "2022-01-01"}})
    assert {f["check"] for f in r["findings"]} == {"pediatric_dose_without_weight"} and r["uncertain"]


class Reviser(StubEngine):
    def __init__(self, first, revised):
        super().__init__("anthropic", first)
        self.revised = revised

    async def complete(self, system, prompt, max_tokens=8000):
        self.completed = (system, prompt)
        return self.revised


async def test_critical_finding_triggers_one_revision():
    triage = screen("crushing chest pain radiating to the left arm").to_dict()
    bad = note({"urgency": "emergency", "differential": [{"diagnosis": "GERD"}]})
    good = note({"urgency": "emergency", "differential": [{"diagnosis": "Acute coronary syndrome", "cant_miss": True}]})
    eng = Reviser(bad, good)
    res = await physician.run_consult(case="chest pain", question=None, audience="clinician", triage=triage, tools=[],
                                      execute_tool=echo, settings=Settings(verify_citations=False), engine=eng)
    assert res["self_check"]["revision"] == "revised" and res["self_check"]["critical"] == 0
    assert "coronary" in res["note"] + json.dumps(res["structured"]) and "cant_miss" in eng.completed[0] + eng.completed[1]


async def test_revision_kept_out_when_it_does_not_help_and_skipped_without_time():
    triage = screen("crushing chest pain radiating to the left arm").to_dict()
    bad = note({"urgency": "emergency", "differential": [{"diagnosis": "GERD"}]})
    res = await physician.run_consult(case="x", question=None, audience="clinician", triage=triage, tools=[], execute_tool=echo,
                                      settings=Settings(verify_citations=False), engine=Reviser(bad, bad))
    assert res["self_check"]["revision"].startswith("attempted") and "GERD" in json.dumps(res["structured"])
    res = await physician.run_consult(case="x", question=None, audience="clinician", triage=triage, tools=[], execute_tool=echo,
                                      settings=Settings(verify_citations=False, consult_timeout=20), engine=Reviser(bad, bad))
    assert res["self_check"]["revision"].startswith("skipped")


def test_urgent_cases_escalate_effort_unless_depth_was_chosen():
    s = Settings()
    assert physician.depth_profile(s, None, "urgent") | {} == physician.depth_profile(s, None, "urgent")
    assert physician.depth_profile(s, None, "urgent")["effort"] == "high" and physician.depth_profile(s, None, "urgent")["escalated"]
    assert physician.depth_profile(s, None, "emergency")["effort"] == "medium"  # speed first
    assert physician.depth_profile(s, "quick", "urgent")["effort"] == "low"  # caller's choice wins
    assert physician.make_engine(s, "anthropic", None, "urgent").effort == "high"


def test_reviewer_engine_is_the_strongest_model_with_fallback():
    e = physician.reviewer_engine(Settings())
    assert e.model == "claude-fable-5-1" and e.effort == "high" and e.fallback_model == "claude-opus-5-5" and not e.web_search
    assert physician.reviewer_engine(Settings(second_opinion_model="claude-opus-5-5")).model == "claude-opus-5-5"
