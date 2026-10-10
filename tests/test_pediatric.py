import pytest

from medical_doctor import pediatric as p
from medical_doctor.safety import screen


def test_vital_signs_normal_and_abnormal():
    assert p.pediatric_vital_signs(0.5, heart_rate=130, respiratory_rate=35)["value"] == 0
    r = p.pediatric_vital_signs(0.5, heart_rate=190)
    assert r["age_band"].startswith("infant") and "Tachycardia" in r["interpretation"]


def test_hypotension_threshold_by_age():
    r = p.pediatric_vital_signs(4, systolic_bp=70)  # threshold 70 + 2*4 = 78
    assert r["hypotension_sbp_below"] == 78 and r["interpretation"].startswith("EMERGENCY")
    assert p.pediatric_vital_signs(15, systolic_bp=95)["value"] == 0


def test_fever_in_young_infant_is_emergency():
    r = p.pediatric_vital_signs(2 / 12, temperature_c=38.2)
    assert r["interpretation"].startswith("EMERGENCY")


def test_vitals_reject_adults():
    with pytest.raises(ValueError):
        p.pediatric_vital_signs(30)


def test_apls_weight():
    assert p.estimated_weight_by_age(0.5)["value"] == 7.0
    assert p.estimated_weight_by_age(3)["value"] == 14.0
    assert p.estimated_weight_by_age(8)["value"] == 31.0


def test_weight_based_dose_volume_and_caps():
    r = p.weight_based_dose(12, 15, concentration_mg_per_ml=32)  # acetaminophen 160 mg/5 mL
    assert r["single_dose_mg"] == 180 and r["volume_ml"] == 5.62
    capped = p.weight_based_dose(50, 15, max_single_dose_mg=650)
    assert capped["value"] == 650 and any("capped" in w for w in capped["warnings"])
    daily = p.weight_based_dose(20, 15, doses_per_day=5, max_daily_dose_mg=1000)
    assert daily["daily_dose_mg"] == 1000 and daily["single_dose_mg"] == 200
    assert any("kg vs lb" in w for w in p.weight_based_dose(180, 1)["warnings"])


def test_holliday_segar():
    r = p.maintenance_fluids_holliday_segar(25)
    assert r["value"] == 65 and r["ml_per_day"] == 1600
    assert p.maintenance_fluids_holliday_segar(8)["value"] == 32
    assert "ISOTONIC" in r["interpretation"]


def test_bedside_schwartz():
    assert p.bedside_schwartz_egfr(0.5, 120, 7)["value"] == 99.1
    with pytest.raises(ValueError):
        p.bedside_schwartz_egfr(1.0, 170, 25)


def test_pecarn_head_trauma_tiers():
    assert p.pecarn_head_trauma(1, True, False)["value"] == "high"
    assert p.pecarn_head_trauma(1, False, False, non_frontal_scalp_hematoma=True)["value"] == "intermediate"
    # vomiting is an intermediate predictor only for >=2 years
    assert p.pecarn_head_trauma(1, False, False, vomiting=True)["value"] == "very low"
    assert p.pecarn_head_trauma(6, False, False, vomiting=True)["value"] == "intermediate"
    assert p.pecarn_head_trauma(6, False, False)["value"] == "very low"


def test_pecarn_febrile_infant():
    low = p.pecarn_febrile_infant(40, True, False, 3000, 0.2)
    assert low["value"] == "low risk"
    assert p.pecarn_febrile_infant(40, True, False, 3000, 2.0)["value"] == "high risk"
    assert p.pecarn_febrile_infant(40, True, True, 3000, 0.2)["value"] == "high risk"
    assert "8-21 days" in p.pecarn_febrile_infant(14, True, False, 3000, 0.2)["interpretation"]
    assert p.pecarn_febrile_infant(40, False, False, 3000, 0.2)["value"] == "not applicable"
    with pytest.raises(ValueError):
        p.pecarn_febrile_infant(90, True, False, 3000, 0.2)


def test_westley_and_dehydration_and_pas():
    assert p.westley_croup_score("normal", "none", "at_rest", "decreased", "mild")["value"] == 4
    assert p.westley_croup_score("disoriented", "at_rest", "at_rest", "markedly_decreased", "severe")["value"] == 17
    assert p.clinical_dehydration_scale("normal", "slightly_sunken", "sticky", "decreased")["value"] == 3
    assert p.pediatric_appendicitis_score(True, True, True, True, True, True, True, True)["value"] == 10


@pytest.mark.parametrize(
    "text,flag,urgency",
    [
        ("my 4 month old is grunting and her ribs are sucking in", "peds_respiratory_distress", "emergency"),
        ("toddler swallowed a button battery", "ingestion", "emergency"),
        ("the baby is floppy and won't wake for feeds", "sick_infant", "emergency"),
        ("2 week old vomiting green", "bilious_vomiting", "emergency"),
        ("9 month old, no wet diaper since this morning", "peds_dehydration", "urgent"),
        ("fever for 5 days with red eyes and rash", "kawasaki_prolonged_fever", "urgent"),
        ("my 3 day old baby looks yellow", "neonatal_jaundice", "urgent"),
    ],
)
def test_pediatric_red_flags(text, flag, urgency):
    r = screen(text)
    assert flag in {h.id for h in r.hits} and r.urgency == urgency


@pytest.mark.parametrize("text", ["child has a mild runny nose", "my son has a tummy ache after dinner", "teething and drooling a lot"])
def test_pediatric_routine(text):
    assert screen(text).urgency == "routine"


def test_weight_dose_daily_max_requires_frequency():
    with pytest.raises(ValueError):
        p.weight_based_dose(20, 15, max_daily_dose_mg=200)
    with pytest.raises(ValueError):
        p.weight_based_dose(20, 15, concentration_mg_per_ml=0)


def test_apls_age_5_to_6_uses_preschool_formula():
    assert p.estimated_weight_by_age(5.5)["value"] == 19.0


def test_pals_svt_and_bradycardia_escalation():
    assert p.pediatric_vital_signs(0.5, heart_rate=250)["interpretation"].startswith("EMERGENCY")
    assert not p.pediatric_vital_signs(15, heart_rate=55)["interpretation"].startswith("EMERGENCY")
    assert p.pediatric_vital_signs(3, heart_rate=50)["interpretation"].startswith("EMERGENCY")


def test_holliday_segar_daily_matches_hourly_cap():
    r = p.maintenance_fluids_holliday_segar(100)
    assert r["value"] == 120 and r["ml_per_day"] == 2880 and r["formula_ml_per_day"] == 3100
