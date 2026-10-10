"""Guideline freshness tracking for the clinical skills."""

from datetime import date

from medical_doctor.skills import cited_guidelines, freshness, freshness_report, load_skills


def test_every_skill_has_review_metadata():
    for s in load_skills().values():
        assert s.metadata.get("last_reviewed"), s.name
        assert s.metadata.get("clinical_review") in ("pending", "approved"), s.name


def test_overdue_after_interval_and_flagged_in_prompt_text():
    s = load_skills()["cardiology"]
    assert freshness(s, today=date.fromisoformat(s.metadata["last_reviewed"]))["status"] == "current"
    later = freshness(s, today=date(2099, 1, 1))
    assert later["status"] == "review_due" and later["older_guidelines"]
    assert freshness_report(today=date(2099, 1, 1))["review_due"]


def test_guideline_citation_extraction():
    found = cited_guidelines("Per ACC/AHA/HFSA 2022 and AAP (2011); GINA 2024. Not a guideline: page 2019.")
    assert ("ACC/AHA/HFSA", 2022) in found and ("AAP", 2011) in found and ("GINA", 2024) in found
    assert all(g != "page" for g, _ in found)


def test_cli_exit_code(monkeypatch, capsys):
    from medical_doctor import __main__ as cli

    monkeypatch.setattr("sys.argv", ["medical-doctor-mcp", "check-freshness"])
    assert cli.check_freshness() == 0
    assert "awaiting clinician sign-off" in capsys.readouterr().out
