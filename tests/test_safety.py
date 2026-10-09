import pytest

from medical_doctor.safety import screen


@pytest.mark.parametrize(
    "text,flag",
    [
        ("58M with crushing chest pain radiating to the left arm and sweating", "acs"),
        ("My mom suddenly has facial droop and slurred speech", "stroke"),
        ("worst headache of my life came on while lifting", "thunderclap"),
        ("after eating peanuts my throat swelling and hives", "anaphylaxis"),
        ("I don't see the point anymore, I want to kill myself", "suicide"),
        ("fever, stiff neck and a non-blanching rash", "meningitis"),
        ("my 6-week-old has a fever of 38.4", "neonatal_fever"),
        ("8 weeks pregnant with bleeding and severe pelvic pain", "pregnancy"),
        ("oxygen saturation 84% and gasping", "airway_breathing"),
        ("back pain with saddle numbness", "cauda_equina"),
    ],
)
def test_emergencies_detected(text, flag):
    r = screen(text)
    assert r.urgency == "emergency"
    assert flag in {h.id for h in r.hits}


def test_suicide_hit_includes_crisis_line():
    r = screen("thinking about suicide")
    assert "988" in r.message


def test_negation_suppresses():
    r = screen("Patient denies chest pain. No shortness of breath.")
    assert r.urgency == "routine"


def test_negation_is_clause_scoped():
    r = screen("No fever, but now has chest pressure for an hour")
    assert r.urgency == "emergency"


def test_urgent_tier():
    r = screen("BP 192/118 at the pharmacy, feels fine")
    assert r.urgency == "urgent"


def test_routine():
    r = screen("mild seasonal allergies with sneezing")
    assert r.urgency == "routine" and not r.hits


@pytest.mark.parametrize(
    "text",
    [
        "headache for 2 days, mild, no fever, no neck stiffness",
        "denies chest pain; no shortness of breath",
        "patient is not suicidal",
        "34F, 7 weeks pregnant, mild nausea",
        "knee pain after running",
    ],
)
def test_no_false_alarm(text):
    assert screen(text).urgency == "routine"


def test_negation_does_not_hide_later_positive_clause():
    r = screen("no headache. Now has fever and stiff neck")
    assert "meningitis" in {h.id for h in r.hits}
