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
        # misses found by the evals/ vignettes
        ("LMP 7 weeks ago, with left lower quadrant pain and spotting", "pregnancy"),
        ("missed her period and now has pelvic pain and feels lightheaded", "pregnancy"),
        ("84-year-old with 2 days of new confusion and agitation", "altered_mental_status"),
        ("toddler with diarrhea, sunken eyes, very sleepy and hard to wake", "hard_to_wake"),
        ("fell off the monkey bars, brief loss of consciousness, then vomited", "head_injury"),
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


# --- Regression corpus (misses and false alarms found in the adversarial review) ----------

EMERGENCY = [
    "He has no history of heart disease and now has chest pain.",
    "Pt with no prior hx presenting w/ R-sided weakness and aphasia",
    "Patient denies fever, has crushing chest pain.",
    "His face is drooping on one side.",
    "Her left arm and leg suddenly went weak.",
    "I can't move my right arm suddenly.",
    "I think I'm having a stroke",
    "I'm having a heart attack",
    "65M with STEMI on ECG",
    "She is not breathing",
    "He collapsed and isn't breathing.",
    "cardiac arrest",
    "he has no pulse",
    "He cannot breathe",
    "I'm short of breath at rest and my lips are blue.",
    "His lips turned blue and he stopped breathing.",
    "He's not responding",
    "My father is not making sense and has a fever of 39",
    "He fell and hit his head and is now confused.",
    "I took 30 tylenol pills.",
    "took a bunch of pills to end it all",
    "I don't want to live anymore",
    "I'm thinking about ending my life.",
    "his throat is closing",
    "my tongue is swelling up after eating peanuts",
    "blood sugar is 600",
    "glucose 1200 mg/dl",
    "blood sugar 35 and shaky",
    "my baby is 6 weeks old and has a temperature of 38.5",
    "my 10 week old is hot, 100.6F",
    "My child swallowed a battery.",
    "My toddler drank bleach.",
    "My testicle hurts suddenly and it's swollen.",
]
URGENT = [
    "glucose 450",
    "Sugar of 55.",
    "sats are 91%.",
    "I passed out.",
    "BP 250/140.",
    "my son hasn't peed in 12 hours",
]
ROUTINE = [
    "I'm not suicidal.",
    "No chest pain, no shortness of breath.",
    "I have no history of stroke.",
    "I ruled out pregnancy; ectopic is not a concern",
    "History of seizures as a child, now well, here for med refill.",
    "Patient on aspirin for prior stroke; well today.",
    "I have type 1 diabetes and want advice on carb counting with ketones testing.",
    "I ate 2 tablets of my vitamins as prescribed.",
    "Overdue for a colonoscopy.",
    "He is responding well to the new inhaler.",
]


@pytest.mark.parametrize("text", EMERGENCY)
def test_corpus_emergency(text):
    assert screen(text).urgency == "emergency", screen(text).to_dict()


@pytest.mark.parametrize("text", URGENT)
def test_corpus_urgent(text):
    assert screen(text).urgency == "urgent", screen(text).to_dict()


@pytest.mark.parametrize("text", ROUTINE)
def test_corpus_routine(text):
    assert screen(text).urgency == "routine", screen(text).to_dict()


def test_screen_is_bounded_on_huge_input():
    import time

    start = time.time()
    screen("chest discomfort " * 200_000)
    assert time.time() - start < 2


def test_eval_gap_rules_have_sane_negation_and_tiers():
    assert screen("he is not hard to wake, playing normally").urgency == "routine"
    assert screen("her period is late but no pain or bleeding").urgency == "routine"
    r = screen("periumbilical pain moving to the right lower quadrant with fever")
    assert r.urgency == "urgent" and "acute_abdomen" in {h.id for h in r.hits}
