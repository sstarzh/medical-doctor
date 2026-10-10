"""Structured consult output and citation verification."""

import json

from medical_doctor import citations, consult_note, physician
from medical_doctor.config import Settings
from tests.test_providers import StubEngine, echo

SUMMARY = {
    "urgency": "urgent", "urgency_reason": "possible pneumonia with hypoxemia",
    "problem_representation": "68M with 3 days of fever, productive cough and SpO2 91%",
    "differential": [
        {"diagnosis": "Community-acquired pneumonia", "likelihood": "likely", "probability_pct": 70},
        {"diagnosis": "Pulmonary embolism", "likelihood": "unlikely", "cant_miss": True},
    ],
    "management": ["CURB-65 then site of care"],
    "citations": [{"label": "IDSA/ATS community-acquired pneumonia guideline", "pmid": "31573350",
                   "supports": "empiric antibiotics for pneumonia"}],
}
NOTE = "## Urgency\nURGENT — same day\n\nText citing PMID: 31573350.\n\nThis is decision support.\n\n```json\n" + json.dumps(SUMMARY) + "\n```\n"


def test_summary_block_is_split_and_validated():
    md, s = consult_note.structure(NOTE, {"urgency": "routine"})
    assert "```json" not in md and md.endswith("This is decision support.")
    assert s["source"] == "model" and s["urgency"] == "urgent"
    assert s["differential"][1]["cant_miss"] is True and s["citations"][0]["pmid"] == "31573350"


def test_red_flag_screen_is_an_urgency_floor():
    _, s = consult_note.structure(NOTE, {"urgency": "emergency"})
    assert s["urgency"] == "emergency" and s["model_urgency"] == "urgent" and s["urgency_overridden_by"] == "red_flag_screen"


def test_missing_or_invalid_block_falls_back_to_markdown():
    _, s = consult_note.structure("## Urgency\n**EMERGENCY** call 911", None)
    assert s["source"] == "markdown_fallback" and s["urgency"] == "emergency" and "parse_error" in s
    bad = NOTE.replace('"urgent"', '"whenever"', 1)
    _, s = consult_note.structure(bad, None)
    assert s["source"] == "markdown_fallback" and "urgency" in s["parse_error"] and s["urgency"] == "urgent"


async def fake_pubmed(pmids):
    db = {"31573350": {"title": "Diagnosis and Treatment of Adults with Community-acquired Pneumonia. An Official Clinical Practice Guideline"},
          "11111111": {"title": "Zebrafish fin regeneration under hypoxia"}}
    return {p: db[p] for p in pmids if p in db}


async def test_citations_verified_mismatched_and_missing():
    md = NOTE + "\nAlso PMID 11111111 shows antibiotics in pneumonia work; PMID 99999999 too. See https://www.idsociety.org/x and https://evil.example/y"
    md, s = consult_note.structure(md)
    r = await citations.verify(md, s, summaries=fake_pubmed)
    by = {x["pmid"]: x["status"] for x in r["pmids"]}
    assert by == {"31573350": "verified", "11111111": "mismatch", "99999999": "not_found"}
    assert not r["all_verified"]
    trusted = {x["url"]: x["trusted_publisher"] for x in r["links"]}
    assert trusted["https://www.idsociety.org/x"] and not trusted["https://evil.example/y"]


async def test_pubmed_down_means_unchecked_not_verified():
    async def down(pmids):
        raise ConnectionError("eutils unreachable")

    md, s = consult_note.structure(NOTE)
    r = await citations.verify(md, s, summaries=down)
    assert r["pmids"][0]["status"] == "unchecked" and not r["all_verified"]


async def test_run_consult_returns_structured_and_citation_check(monkeypatch):
    real = citations.verify

    async def fake_verify(markdown, structured, summaries=None):
        return await real(markdown, structured, summaries=fake_pubmed)

    monkeypatch.setattr(citations, "verify", fake_verify)
    res = await physician.run_consult(case="cough", question=None, audience="clinician", triage={"urgency": "routine"},
                                      tools=[], execute_tool=echo, settings=Settings(), engine=StubEngine("anthropic", NOTE))
    assert res["structured"]["urgency"] == "urgent" and "```json" not in res["note"]
    assert res["citation_check"]["all_verified"]


def test_charter_asks_for_the_summary_block():
    assert "```json" in physician.CHARTER and "citations" in physician.CHARTER
