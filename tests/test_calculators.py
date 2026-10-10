import pytest

from medical_doctor import calculators as c


def test_egfr_ckd_epi_2021_matches_published_values():
    # Reference values cross-checked against NKF/MDCalc CKD-EPI 2021 calculators.
    assert c.egfr_ckd_epi_2021(1.0, 50, "female")["value"] == pytest.approx(68.6, abs=0.5)
    assert c.egfr_ckd_epi_2021(1.0, 50, "male")["value"] == pytest.approx(91.7, abs=0.5)
    assert "G5" in c.egfr_ckd_epi_2021(6.0, 70, "male")["interpretation"]


def test_egfr_rejects_children_and_bad_input():
    with pytest.raises(ValueError):
        c.egfr_ckd_epi_2021(1.0, 12, "male")
    with pytest.raises(ValueError):
        c.egfr_ckd_epi_2021(0, 40, "male")


def test_cockcroft_gault():
    # (140-60)*70/(72*1.0) = 77.8 ; female x0.85 = 66.1
    assert c.creatinine_clearance_cockcroft_gault(1.0, 60, 70, "male")["value"] == 77.8
    assert c.creatinine_clearance_cockcroft_gault(1.0, 60, 70, "female")["value"] == 66.1


def test_cha2ds2_vasc():
    r = c.cha2ds2_vasc(age=76, sex="female", hypertension=True, diabetes=True)
    assert r["value"] == 5
    assert "recommended" in r["interpretation"]
    assert c.cha2ds2_vasc(age=50, sex="female")["value"] == 1  # sex alone
    assert "not recommended" in c.cha2ds2_vasc(age=50, sex="female")["interpretation"]


def test_heart_score_bands():
    assert c.heart_score(0, 0, 40, 0, 0)["value"] == 0
    assert c.heart_score(2, 1, 70, 2, 2)["value"] == 9
    with pytest.raises(ValueError):
        c.heart_score(3, 0, 40, 0, 0)


def test_wells_and_perc():
    assert c.wells_pe(clinical_signs_dvt=True, heart_rate_over_100=True)["value"] == 4.5
    assert "likely" in c.wells_pe(clinical_signs_dvt=True, heart_rate_over_100=True)["interpretation"]
    assert c.perc_rule()["value"] == 0


def test_curb65_qsofa_gcs():
    assert c.curb_65(True, True, True, False, True)["value"] == 4
    assert c.qsofa(True, True, False)["value"] == 2
    assert c.glasgow_coma_scale(1, 1, 1)["value"] == 3
    with pytest.raises(ValueError):
        c.glasgow_coma_scale(5, 5, 6)


def test_meld_na():
    # Cr 1.9, bili 4.2, INR 1.7, Na 130 -> MELD(i) 23.9 -> MELD-Na 28
    assert c.meld_na(1.9, 4.2, 1.7, 130)["value"] == 28
    # Floors: all normal -> MELD 6
    assert c.meld_na(0.8, 0.5, 1.0, 140)["value"] == 6
    # Dialysis forces creatinine to 4
    assert c.meld_na(1.0, 1.0, 1.0, 137, dialysis_twice_in_past_week=True)["value"] == 20


def test_chemistry():
    ag = c.anion_gap(140, 100, 14, albumin_g_dl=2.0)
    assert ag["value"] == 26 and ag["albumin_corrected"] == 31.0
    assert c.corrected_calcium(8.0, 2.0)["value"] == 9.6
    na = c.corrected_sodium_hyperglycemia(130, 600)
    assert na["katz"] == 138.0 and na["hillier"] == 142.0


def test_qtc_and_map_and_bmi():
    q = c.qtc(400, 60)
    assert q["bazett_ms"] == 400 and q["fridericia_ms"] == 400
    assert c.mean_arterial_pressure(120, 80)["value"] == 93.3
    assert c.bmi(70, 175)["value"] == 22.9


def test_centor_age_modifier():
    assert c.centor_mcisaac(10, True, True, True, True)["value"] == 5
    assert c.centor_mcisaac(50, False, False, False, True)["value"] == 0


# --- regressions from the correctness review --------------------------------


def test_qtc_rejects_seconds_and_uses_sex_thresholds():
    with pytest.raises(ValueError):
        c.qtc(0.44, 70)
    assert "Prolonged" in c.qtc(460, 60, "male")["interpretation"]
    assert c.qtc(460, 60, "female")["interpretation"].startswith("Normal")


def test_meld_rounds_half_up():
    # bilirubin 2.9, Cr 1, INR 1 -> MELD(i) 10.5 -> UNOS rounds half up to 11
    assert c.meld_na(1.0, 2.9, 1.0, 137)["value"] == 11


def test_meld_3():
    assert c.meld_3(1.0, 1.0, 1.0, 137, 3.5, "male")["value"] == 6
    assert c.meld_3(1.0, 1.0, 1.0, 137, 3.5, "female")["value"] == 7  # +1.33 for female sex
    sick = c.meld_3(2.5, 6.0, 2.2, 128, 2.4, "male")["value"]
    assert 30 <= sick <= 40
