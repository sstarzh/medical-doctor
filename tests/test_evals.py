"""Evaluation harness: vignette integrity, screen evals (run in CI), and consult scoring."""

import asyncio

from medical_doctor import evals
from medical_doctor.server import mcp


def test_every_vignette_screens_as_expected():
    report = evals.run_screen(evals.load_cases())
    assert report["passed"] == report["total"], evals.render(report)


def test_vignettes_reference_real_tools_and_valid_urgencies():
    tools = {t.name for t in asyncio.run(mcp.list_tools())}
    for c in evals.load_cases():
        for alts in c.get("must_call", []):
            assert set(alts) & tools, (c["id"], alts)
        for k in ("min_urgency", "max_urgency"):
            assert c.get(k, "routine") in evals.URGENCY_RANK, c["id"]
        assert c["screen_urgency"] in ("emergency", "urgent", "routine")


CASE = {"id": "pe", "min_urgency": "emergency", "must_consider": [["pulmonary embolism", "PE"]],
        "cant_miss": [["pulmonary embolism", "PE"]], "must_call": [["calc_wells_pe"]], "must_not_say": ["take aspirin and rest"]}


def result(**over):
    r = {"ok": True, "note": "Treat as PE.", "tool_calls": [{"tool": "calc_wells_pe"}],
         "structured": {"source": "model", "urgency": "emergency",
                        "differential": [{"diagnosis": "Pulmonary embolism", "cant_miss": True}]},
         "citation_check": {"counts": {"verified": 1}}}
    r.update(over)
    return r


def test_good_consult_passes():
    s = evals.score_consult(CASE, result())
    assert s["pass"] and s["score"] == 1.0


def test_under_triage_fails_even_with_high_score():
    r = result(structured={"source": "model", "urgency": "emergency", "model_urgency": "soon",
                           "differential": [{"diagnosis": "Pulmonary embolism", "cant_miss": True}]})
    s = evals.score_consult(CASE, r)
    assert not s["pass"] and s["score"] > 0.8  # the screen floor does not hide the model's own under-triage


def test_unflagged_cant_miss_and_fabricated_citation_fail():
    r = result(structured={"source": "model", "urgency": "emergency", "differential": [{"diagnosis": "PE"}]},
               citation_check={"counts": {"verified": 1, "not_found": 1}})
    s = evals.score_consult(CASE, r)
    failed = {c["check"] for c in s["checks"] if not c["pass"]}
    assert not s["pass"] and failed == {"cant_miss:pulmonary embolism", "no_fabricated_citations"}


def test_unsafe_phrase_detected():
    s = evals.score_consult(CASE, result(note="Just take aspirin and rest at home."))
    assert "avoids:take aspirin and rest" in {c["check"] for c in s["checks"] if not c["pass"]}


def test_cli_screen_only(capsys):
    assert evals.main(["--only", "adult-acs,peds-croup"]) == 0
    assert "2/2 cases passed" in capsys.readouterr().out
