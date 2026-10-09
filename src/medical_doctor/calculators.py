"""Deterministic, validated clinical calculators.

LLMs are unreliable at arithmetic and at remembering exact cut-offs, so every
score the physician relies on is computed here, in plain code, with the
primary reference attached to the result.
"""

from __future__ import annotations

import math
from typing import Any, Literal

Sex = Literal["female", "male"]


def _result(name: str, value: float | int, interpretation: str, reference: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"calculator": name, "value": value, "interpretation": interpretation}
    out.update(extra)
    out["reference"] = reference
    return out


def _require_positive(**values: float) -> None:
    for key, val in values.items():
        if val is None or val <= 0:
            raise ValueError(f"{key} must be a positive number (got {val!r})")


# --- Renal ---------------------------------------------------------------


def egfr_ckd_epi_2021(creatinine_mg_dl: float, age: int, sex: Sex) -> dict[str, Any]:
    """Race-free CKD-EPI 2021 creatinine equation (mL/min/1.73m²)."""
    _require_positive(creatinine_mg_dl=creatinine_mg_dl, age=age)
    if age < 18:
        raise ValueError("CKD-EPI is validated for adults (age >= 18); use calc_bedside_schwartz_egfr for children")
    female = sex == "female"
    kappa = 0.7 if female else 0.9
    alpha = -0.241 if female else -0.302
    ratio = creatinine_mg_dl / kappa
    egfr = 142 * min(ratio, 1) ** alpha * max(ratio, 1) ** -1.200 * 0.9938**age * (1.012 if female else 1.0)
    egfr = round(egfr, 1)
    if egfr >= 90:
        stage = "G1 (normal/high; CKD only if other kidney damage markers)"
    elif egfr >= 60:
        stage = "G2 (mildly decreased)"
    elif egfr >= 45:
        stage = "G3a (mild-moderate)"
    elif egfr >= 30:
        stage = "G3b (moderate-severe)"
    elif egfr >= 15:
        stage = "G4 (severe)"
    else:
        stage = "G5 (kidney failure)"
    return _result(
        "eGFR (CKD-EPI 2021)",
        egfr,
        f"KDIGO stage {stage}. Requires stable creatinine; not valid in AKI.",
        "Inker LA et al. N Engl J Med 2021;385:1737-49",
        units="mL/min/1.73m²",
    )


def creatinine_clearance_cockcroft_gault(
    creatinine_mg_dl: float, age: int, weight_kg: float, sex: Sex
) -> dict[str, Any]:
    """Cockcroft-Gault CrCl (mL/min); still the reference for many drug-dosing labels."""
    _require_positive(creatinine_mg_dl=creatinine_mg_dl, age=age, weight_kg=weight_kg)
    crcl = ((140 - age) * weight_kg) / (72 * creatinine_mg_dl)
    if sex == "female":
        crcl *= 0.85
    crcl = round(crcl, 1)
    return _result(
        "Creatinine clearance (Cockcroft-Gault)",
        crcl,
        "Used for renal drug-dose adjustment per most FDA labels. Uses actual body weight as given; "
        "consider ideal/adjusted body weight in obesity.",
        "Cockcroft DW, Gault MH. Nephron 1976;16:31-41",
        units="mL/min",
    )


# --- Cardiology ----------------------------------------------------------


def cha2ds2_vasc(
    age: int,
    sex: Sex,
    congestive_heart_failure: bool = False,
    hypertension: bool = False,
    diabetes: bool = False,
    stroke_tia_thromboembolism: bool = False,
    vascular_disease: bool = False,
) -> dict[str, Any]:
    """Stroke risk in non-valvular atrial fibrillation."""
    score = 0
    score += 1 if congestive_heart_failure else 0
    score += 1 if hypertension else 0
    score += 2 if age >= 75 else (1 if age >= 65 else 0)
    score += 1 if diabetes else 0
    score += 2 if stroke_tia_thromboembolism else 0
    score += 1 if vascular_disease else 0
    score += 1 if sex == "female" else 0
    non_sex_score = score - (1 if sex == "female" else 0)
    if non_sex_score == 0:
        rec = "Low risk: anticoagulation generally not recommended."
    elif non_sex_score == 1:
        rec = "Intermediate risk: oral anticoagulation reasonable (shared decision; 2023 ACC/AHA: class 2a)."
    else:
        rec = "High risk: oral anticoagulation recommended (DOAC preferred over warfarin if eligible)."
    return _result(
        "CHA2DS2-VASc",
        score,
        rec + " Female sex is a risk modifier, not an independent indication.",
        "Lip GY et al. Chest 2010;137:263-72; 2023 ACC/AHA/ACCP/HRS AF Guideline",
    )


def has_bled(
    hypertension_uncontrolled: bool = False,
    abnormal_renal_function: bool = False,
    abnormal_liver_function: bool = False,
    stroke_history: bool = False,
    bleeding_history_or_predisposition: bool = False,
    labile_inr: bool = False,
    elderly_over_65: bool = False,
    drugs_predisposing_bleeding: bool = False,
    alcohol_8_or_more_drinks_week: bool = False,
) -> dict[str, Any]:
    """Major bleeding risk on anticoagulation."""
    score = sum(
        [
            hypertension_uncontrolled,
            abnormal_renal_function,
            abnormal_liver_function,
            stroke_history,
            bleeding_history_or_predisposition,
            labile_inr,
            elderly_over_65,
            drugs_predisposing_bleeding,
            alcohol_8_or_more_drinks_week,
        ]
    )
    interp = (
        "High bleeding risk (>=3): address modifiable factors and monitor closely; "
        "a high score alone is not a reason to withhold anticoagulation."
        if score >= 3
        else "Low-moderate bleeding risk."
    )
    return _result("HAS-BLED", score, interp, "Pisters R et al. Chest 2010;138:1093-100")


def heart_score(
    history: int, ecg: int, age: int, risk_factors: int, troponin: int
) -> dict[str, Any]:
    """HEART score for ED chest pain. history/ecg/risk_factors/troponin are each 0-2; age in years."""
    for key, val in {"history": history, "ecg": ecg, "risk_factors": risk_factors, "troponin": troponin}.items():
        if val not in (0, 1, 2):
            raise ValueError(f"{key} must be 0, 1 or 2")
    age_pts = 0 if age < 45 else (1 if age < 65 else 2)
    score = history + ecg + age_pts + risk_factors + troponin
    if score <= 3:
        interp = "Low risk (0-3): 6-week MACE ~0.9-1.7%. Candidate for early discharge with follow-up."
    elif score <= 6:
        interp = "Moderate risk (4-6): 6-week MACE ~12-16.6%. Observation and further testing."
    else:
        interp = "High risk (7-10): 6-week MACE ~50-65%. Early invasive strategy / cardiology."
    return _result("HEART score", score, interp, "Six AJ et al. Neth Heart J 2008;16:191-6; Backus BE et al. Int J Cardiol 2013", age_points=age_pts)


def qtc(qt_ms: float, heart_rate_bpm: float) -> dict[str, Any]:
    """Corrected QT by Bazett and Fridericia (Fridericia preferred at HR extremes)."""
    _require_positive(qt_ms=qt_ms, heart_rate_bpm=heart_rate_bpm)
    rr = 60.0 / heart_rate_bpm
    bazett = round(qt_ms / math.sqrt(rr))
    fridericia = round(qt_ms / rr ** (1 / 3))
    q = fridericia
    if q >= 500:
        interp = "Markedly prolonged (>=500 ms): high torsades risk; stop QT-prolonging drugs, check K/Mg."
    elif q > 470:
        interp = "Prolonged (>470 ms women / >450 ms men is abnormal)."
    elif q > 450:
        interp = "Borderline-prolonged (abnormal in men >450 ms)."
    else:
        interp = "Normal."
    return _result(
        "QTc",
        fridericia,
        interp,
        "Bazett HC. Heart 1920; Fridericia LS. Acta Med Scand 1920; AHA/ACCF/HRS 2009 ECG standards",
        units="ms",
        bazett_ms=bazett,
        fridericia_ms=fridericia,
    )


def mean_arterial_pressure(systolic: float, diastolic: float) -> dict[str, Any]:
    _require_positive(systolic=systolic, diastolic=diastolic)
    m = round((systolic + 2 * diastolic) / 3, 1)
    interp = "Below 65 mmHg suggests inadequate organ perfusion (septic shock target >=65)." if m < 65 else "Adequate perfusion pressure range."
    return _result("Mean arterial pressure", m, interp, "Surviving Sepsis Campaign 2021", units="mmHg")


# --- Pulmonary / VTE / Infection ----------------------------------------


def wells_pe(
    clinical_signs_dvt: bool = False,
    pe_most_likely_diagnosis: bool = False,
    heart_rate_over_100: bool = False,
    immobilization_or_surgery_past_4_weeks: bool = False,
    previous_dvt_or_pe: bool = False,
    hemoptysis: bool = False,
    active_malignancy: bool = False,
) -> dict[str, Any]:
    score = (
        3.0 * clinical_signs_dvt
        + 3.0 * pe_most_likely_diagnosis
        + 1.5 * heart_rate_over_100
        + 1.5 * immobilization_or_surgery_past_4_weeks
        + 1.5 * previous_dvt_or_pe
        + 1.0 * hemoptysis
        + 1.0 * active_malignancy
    )
    if score > 4:
        interp = "PE likely (>4): proceed to CT pulmonary angiography (or V/Q if contraindicated)."
    else:
        interp = "PE unlikely (<=4): apply PERC if very low pretest probability, otherwise D-dimer (age-adjusted if >50)."
    return _result("Wells score for PE", score, interp, "Wells PS et al. Thromb Haemost 2000;83:416-20")


def perc_rule(
    age_50_or_over: bool = False,
    heart_rate_100_or_over: bool = False,
    sao2_below_95: bool = False,
    unilateral_leg_swelling: bool = False,
    hemoptysis: bool = False,
    recent_surgery_or_trauma: bool = False,
    prior_pe_or_dvt: bool = False,
    hormone_use: bool = False,
) -> dict[str, Any]:
    positives = sum(
        [
            age_50_or_over,
            heart_rate_100_or_over,
            sao2_below_95,
            unilateral_leg_swelling,
            hemoptysis,
            recent_surgery_or_trauma,
            prior_pe_or_dvt,
            hormone_use,
        ]
    )
    interp = (
        "PERC negative: if clinical gestalt pretest probability <15%, PE is effectively excluded without D-dimer."
        if positives == 0
        else "PERC positive: cannot rule out PE by PERC; proceed to D-dimer or imaging per Wells."
    )
    return _result("PERC rule", positives, interp, "Kline JA et al. J Thromb Haemost 2004;2:1247-55")


def curb_65(
    confusion: bool,
    bun_over_19_mg_dl: bool,
    respiratory_rate_30_or_over: bool,
    sbp_under_90_or_dbp_60_or_under: bool,
    age_65_or_over: bool,
) -> dict[str, Any]:
    score = sum([confusion, bun_over_19_mg_dl, respiratory_rate_30_or_over, sbp_under_90_or_dbp_60_or_under, age_65_or_over])
    interp = {
        0: "Low risk (30-day mortality ~0.6%): consider outpatient treatment.",
        1: "Low risk (~2.7%): consider outpatient treatment.",
        2: "Moderate risk (~6.8%): short inpatient stay or closely supervised outpatient.",
    }.get(score, "Severe pneumonia (score >=3, mortality 14-27%+): hospitalize; assess for ICU if 4-5.")
    return _result("CURB-65", score, interp, "Lim WS et al. Thorax 2003;58:377-82")


def qsofa(respiratory_rate_22_or_over: bool, altered_mentation: bool, systolic_bp_100_or_under: bool) -> dict[str, Any]:
    score = sum([respiratory_rate_22_or_over, altered_mentation, systolic_bp_100_or_under])
    interp = (
        "qSOFA >=2: high risk of poor outcome with suspected infection; evaluate for sepsis/organ dysfunction now. "
        "(Not recommended as a single screening tool — SSC 2021.)"
        if score >= 2
        else "qSOFA <2: does not exclude sepsis; use clinical judgment, lactate, and full SOFA/NEWS."
    )
    return _result("qSOFA", score, interp, "Seymour CW et al. JAMA 2016;315:762-74; Evans L et al. SSC 2021")


def centor_mcisaac(
    age: int,
    tonsillar_exudate_or_swelling: bool,
    tender_anterior_cervical_nodes: bool,
    fever_over_38c: bool,
    cough_absent: bool,
) -> dict[str, Any]:
    score = sum([tonsillar_exudate_or_swelling, tender_anterior_cervical_nodes, fever_over_38c, cough_absent])
    if 3 <= age <= 14:
        score += 1
    elif age >= 45:
        score -= 1
    if score <= 1:
        interp = "Low probability of GAS pharyngitis (<10%): no testing or antibiotics."
    elif score <= 3:
        interp = "Intermediate: rapid antigen test/culture; treat only if positive."
    else:
        interp = "High probability (~50%+): test (RADT); some guidelines allow empiric treatment."
    return _result("Centor (McIsaac) score", score, interp, "McIsaac WJ et al. JAMA 2004;291:1587-95; IDSA 2012")


# --- Hepatology ----------------------------------------------------------


def meld_na(
    creatinine_mg_dl: float,
    bilirubin_mg_dl: float,
    inr: float,
    sodium_meq_l: float,
    dialysis_twice_in_past_week: bool = False,
) -> dict[str, Any]:
    """MELD-Na (UNOS/OPTN 2016)."""
    _require_positive(creatinine_mg_dl=creatinine_mg_dl, bilirubin_mg_dl=bilirubin_mg_dl, inr=inr, sodium_meq_l=sodium_meq_l)
    cr = 4.0 if dialysis_twice_in_past_week else min(max(creatinine_mg_dl, 1.0), 4.0)
    bili = max(bilirubin_mg_dl, 1.0)
    inr_ = max(inr, 1.0)
    meld = 10 * (0.957 * math.log(cr) + 0.378 * math.log(bili) + 1.120 * math.log(inr_) + 0.643)
    meld = round(meld, 1)
    na = min(max(sodium_meq_l, 125), 137)
    if meld > 11:
        meld = meld + 1.32 * (137 - na) - 0.033 * meld * (137 - na)
    score = min(round(meld), 40)
    if score < 10:
        mort = "~1.9%"
    elif score < 20:
        mort = "~6.0%"
    elif score < 30:
        mort = "~19.6%"
    elif score < 40:
        mort = "~52.6%"
    else:
        mort = "~71.3%"
    return _result(
        "MELD-Na",
        score,
        f"Estimated 90-day mortality {mort}. MELD >=15 generally warrants transplant evaluation.",
        "Kim WR et al. N Engl J Med 2008;359:1018-26; OPTN policy 2016",
    )


# --- Neuro ---------------------------------------------------------------


def glasgow_coma_scale(eye: int, verbal: int, motor: int) -> dict[str, Any]:
    """Glasgow Coma Scale (eye 1-4, verbal 1-5, motor 1-6). For preverbal children use the pediatric verbal
    scale: 5 coos/babbles, 4 irritable cry, 3 cries to pain, 2 moans to pain, 1 none."""
    if eye not in range(1, 5) or verbal not in range(1, 6) or motor not in range(1, 7):
        raise ValueError("eye 1-4, verbal 1-5, motor 1-6")
    total = eye + verbal + motor
    if total <= 8:
        interp = "Severe (<=8): consider airway protection ('GCS 8, intubate')."
    elif total <= 12:
        interp = "Moderate (9-12)."
    else:
        interp = "Mild (13-15)."
    return _result("Glasgow Coma Scale", total, interp, "Teasdale G, Jennett B. Lancet 1974;2:81-4", components=f"E{eye}V{verbal}M{motor}")


# --- Chemistry -----------------------------------------------------------


def anion_gap(sodium: float, chloride: float, bicarbonate: float, albumin_g_dl: float | None = None) -> dict[str, Any]:
    ag = sodium - (chloride + bicarbonate)
    extra: dict[str, Any] = {"units": "mEq/L"}
    effective = ag
    if albumin_g_dl is not None:
        effective = round(ag + 2.5 * (4.0 - albumin_g_dl), 1)
        extra["albumin_corrected"] = effective
    if effective > 12:
        interp = "Elevated anion gap: consider lactate, ketones, renal failure, toxic alcohols, salicylates (GOLDMARK). Compute delta-delta."
    elif effective < 3:
        interp = "Low anion gap: consider hypoalbuminemia, lab error, paraproteinemia, lithium/bromide."
    else:
        interp = "Normal anion gap (~3-12, lab dependent). If acidemic, consider normal-gap (hyperchloremic) acidosis."
    return _result("Anion gap", round(ag, 1), interp, "Kraut JA, Madias NE. Clin J Am Soc Nephrol 2007;2:162-74", **extra)


def corrected_calcium(calcium_mg_dl: float, albumin_g_dl: float) -> dict[str, Any]:
    _require_positive(calcium_mg_dl=calcium_mg_dl, albumin_g_dl=albumin_g_dl)
    c = round(calcium_mg_dl + 0.8 * (4.0 - albumin_g_dl), 2)
    if c > 10.5:
        interp = "Hypercalcemia (corrected). Confirm with ionized calcium; check PTH."
    elif c < 8.5:
        interp = "Hypocalcemia (corrected). Confirm with ionized calcium; check Mg, PTH, vitamin D."
    else:
        interp = "Within normal range (8.5-10.5). Ionized calcium is more accurate in critical illness."
    return _result("Albumin-corrected calcium", c, interp, "Payne RB et al. BMJ 1973;4:643-6", units="mg/dL")


def corrected_sodium_hyperglycemia(sodium: float, glucose_mg_dl: float) -> dict[str, Any]:
    _require_positive(sodium=sodium, glucose_mg_dl=glucose_mg_dl)
    excess = max(glucose_mg_dl - 100, 0) / 100
    katz = round(sodium + 1.6 * excess, 1)
    hillier = round(sodium + 2.4 * excess, 1)
    return _result(
        "Sodium corrected for hyperglycemia",
        hillier,
        "Hillier (2.4) is more accurate above glucose ~400 mg/dL; Katz (1.6) shown for comparison. "
        "Use to track true tonicity during DKA/HHS treatment.",
        "Hillier TA et al. Am J Med 1999;106:399-403; Katz MA. NEJM 1973",
        units="mEq/L",
        katz=katz,
        hillier=hillier,
    )


# --- General -------------------------------------------------------------


def bmi(weight_kg: float, height_cm: float) -> dict[str, Any]:
    _require_positive(weight_kg=weight_kg, height_cm=height_cm)
    b = round(weight_kg / (height_cm / 100) ** 2, 1)
    if b < 18.5:
        cat = "Underweight"
    elif b < 25:
        cat = "Normal weight"
    elif b < 30:
        cat = "Overweight"
    elif b < 35:
        cat = "Obesity class I"
    elif b < 40:
        cat = "Obesity class II"
    else:
        cat = "Obesity class III"
    return _result(
        "Body mass index",
        b,
        f"{cat} (WHO adult categories; Asian populations use lower cut-offs; not valid in pregnancy; for ages 2-19 use CDC BMI-for-age percentiles, not adult categories).",
        "WHO Technical Report Series 894, 2000",
        units="kg/m²",
    )


CALCULATORS = {
    "egfr_ckd_epi_2021": egfr_ckd_epi_2021,
    "creatinine_clearance_cockcroft_gault": creatinine_clearance_cockcroft_gault,
    "cha2ds2_vasc": cha2ds2_vasc,
    "has_bled": has_bled,
    "heart_score": heart_score,
    "qtc": qtc,
    "mean_arterial_pressure": mean_arterial_pressure,
    "wells_pe": wells_pe,
    "perc_rule": perc_rule,
    "curb_65": curb_65,
    "qsofa": qsofa,
    "centor_mcisaac": centor_mcisaac,
    "meld_na": meld_na,
    "glasgow_coma_scale": glasgow_coma_scale,
    "anion_gap": anion_gap,
    "corrected_calcium": corrected_calcium,
    "corrected_sodium_hyperglycemia": corrected_sodium_hyperglycemia,
    "bmi": bmi,
}
