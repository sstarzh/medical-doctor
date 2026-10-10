"""Pediatric calculators and decision rules.

Children need age- and weight-specific math: normal vital signs change with
age, every dose is per kilogram with an adult ceiling, and the validated
emergency decision rules (PECARN, febrile infant, croup, dehydration) differ
from adult ones. All of it lives here as deterministic code.
"""

from __future__ import annotations

from typing import Any, Literal

from .calculators import _require_positive, _result

# --- Vital signs ---------------------------------------------------------

# PALS 2020 (AHA) normal ranges, awake. (label, max_age_years_exclusive, HR, RR)
_VITAL_BANDS: tuple[tuple[str, float, tuple[int, int], tuple[int, int]], ...] = (
    ("neonate (<28 days)", 28 / 365, (100, 205), (30, 60)),
    ("infant (1-12 months)", 1, (100, 180), (30, 53)),
    ("toddler (1-2 years)", 3, (98, 140), (22, 37)),
    ("preschool (3-5 years)", 6, (80, 120), (20, 28)),
    ("school-age (6-11 years)", 12, (75, 118), (18, 25)),
    ("adolescent (12-17 years)", 18, (60, 100), (12, 20)),
)


def _sbp_hypotension_threshold(age_years: float) -> int:
    """PALS definition of hypotension (systolic, mmHg)."""
    if age_years < 28 / 365:
        return 60
    if age_years < 1:
        return 70
    if age_years <= 10:
        return int(70 + 2 * age_years)
    return 90


def pediatric_vital_signs(
    age_years: float,
    heart_rate: float | None = None,
    respiratory_rate: float | None = None,
    systolic_bp: float | None = None,
    temperature_c: float | None = None,
    spo2: float | None = None,
) -> dict[str, Any]:
    """Compare a child's vital signs to PALS age-specific norms. age_years may be fractional (e.g. 0.25 = 3 months)."""
    if age_years < 0 or age_years >= 18:
        raise ValueError("age_years must be between 0 and 18")
    band = next(b for b in _VITAL_BANDS if age_years < b[1])
    label, _, (hr_lo, hr_hi), (rr_lo, rr_hi) = band
    hypo = _sbp_hypotension_threshold(age_years)
    flags: list[str] = []
    if heart_rate is not None:
        if heart_rate > hr_hi:
            flags.append(f"Tachycardia: HR {heart_rate:g} > {hr_hi} (shock, fever, pain, dehydration, SVT if >220 infant / >180 child)")
        elif heart_rate < hr_lo:
            flags.append(f"Bradycardia: HR {heart_rate:g} < {hr_lo} — in a sick child this is a pre-arrest sign (hypoxia)")
    if respiratory_rate is not None:
        if respiratory_rate > rr_hi:
            flags.append(f"Tachypnea: RR {respiratory_rate:g} > {rr_hi}")
        elif respiratory_rate < rr_lo:
            flags.append(f"Bradypnea: RR {respiratory_rate:g} < {rr_lo} — tiring or CNS depression")
    if systolic_bp is not None and systolic_bp < hypo:
        flags.append(f"HYPOTENSION: SBP {systolic_bp:g} < {hypo} — decompensated shock, emergency")
    if temperature_c is not None:
        if temperature_c >= 38.0:
            fever = f"Fever {temperature_c:g}°C"
            if age_years < 0.25:
                fever += " in an infant <3 months — EMERGENCY evaluation (febrile infant pathway)"
            flags.append(fever)
        elif temperature_c < 36.0:
            flags.append(f"Hypothermia {temperature_c:g}°C — in infants can signal sepsis")
    if spo2 is not None and spo2 < 92:
        flags.append(f"Hypoxemia: SpO2 {spo2:g}% (<92%)")
    svt = 220 if age_years < 1 else 180
    hypotensive = systolic_bp is not None and systolic_bp < hypo
    hypoxemic = spo2 is not None and spo2 < 90
    severe_brady = heart_rate is not None and heart_rate < 60 and age_years < 12
    if heart_rate is not None and heart_rate > svt:
        flags.append(f"HR {heart_rate:g} > {svt}: possible SVT or shock — EMERGENCY")
    # Bradycardia is an emergency when profound (<60 in a child, the PALS CPR threshold with poor
    # perfusion) or with hypotension/hypoxemia; mild bradycardia in a fit adolescent is common.
    emergency = hypotensive or hypoxemic or severe_brady or any("EMERGENCY" in f for f in flags)
    interp = "EMERGENCY: " if emergency else ("Abnormal: " if flags else "Within PALS normal ranges for age. ")
    interp += "; ".join(flags) if flags else "Interpret with the child's appearance, work of breathing and circulation."
    return _result(
        "Pediatric vital signs (PALS)",
        len(flags),
        interp,
        "AHA PALS Provider Manual 2020; Topjian AA et al. Circulation 2020;142:S469",
        age_band=label,
        normal_heart_rate=f"{hr_lo}-{hr_hi}",
        normal_respiratory_rate=f"{rr_lo}-{rr_hi}",
        hypotension_sbp_below=hypo,
        flags=flags,
    )


def estimated_weight_by_age(age_years: float) -> dict[str, Any]:
    """APLS weight estimate for emergencies when the child cannot be weighed (prefer a length-based tape)."""
    if age_years < 0 or age_years > 12:
        raise ValueError("APLS formula covers 0-12 years")
    if age_years < 1:
        months = age_years * 12
        w, formula = 0.5 * months + 4, "(0.5 × age in months) + 4"
    elif age_years < 6:
        w, formula = 2 * age_years + 8, "(2 × age) + 8"
    else:
        w, formula = 3 * age_years + 7, "(3 × age) + 7"
    return _result(
        "Estimated weight (APLS)",
        round(w, 1),
        "Emergency estimate only; a measured weight or length-based tape (Broselow) is more accurate. "
        "Underestimates weight in children with obesity.",
        "Advanced Paediatric Life Support, 6th ed. (2016)",
        units="kg",
        formula=formula,
    )


# --- Dosing & fluids -----------------------------------------------------


def weight_based_dose(
    weight_kg: float,
    dose_mg_per_kg: float,
    max_single_dose_mg: float | None = None,
    doses_per_day: int | None = None,
    max_daily_dose_mg: float | None = None,
    concentration_mg_per_ml: float | None = None,
) -> dict[str, Any]:
    """Weight-based dose arithmetic with adult ceilings and liquid volume.
    The mg/kg, maxima and concentration MUST come from the drug label or a pediatric formulary, never memory."""
    _require_positive(weight_kg=weight_kg, dose_mg_per_kg=dose_mg_per_kg)
    warnings: list[str] = []
    raw = weight_kg * dose_mg_per_kg
    dose = raw
    if max_single_dose_mg is not None and raw > max_single_dose_mg:
        dose = max_single_dose_mg
        warnings.append(f"Calculated {raw:g} mg exceeds max single dose; capped at {max_single_dose_mg:g} mg")
    if max_daily_dose_mg is not None and not doses_per_day:
        raise ValueError("doses_per_day is required when max_daily_dose_mg is given (to check the daily total)")
    if concentration_mg_per_ml is not None and concentration_mg_per_ml <= 0:
        raise ValueError("concentration_mg_per_ml must be positive")
    if doses_per_day is not None and doses_per_day <= 0:
        raise ValueError("doses_per_day must be positive")
    daily = dose * doses_per_day if doses_per_day else None
    if daily is not None and max_daily_dose_mg is not None and daily > max_daily_dose_mg:
        dose = max_daily_dose_mg / doses_per_day
        daily = max_daily_dose_mg
        warnings.append(f"Daily total exceeds max daily dose; reduced to {dose:g} mg per dose")
    if weight_kg >= 40:
        warnings.append("Weight >=40 kg: use adult dosing if lower than the weight-based dose")
    if weight_kg > 150:
        warnings.append("Weight >150 kg is implausible for a child — check units (kg vs lb)")
    out: dict[str, Any] = {"units": "mg", "single_dose_mg": round(dose, 2), "warnings": warnings}
    if daily is not None:
        out["daily_dose_mg"] = round(daily, 2)
    if concentration_mg_per_ml:
        out["volume_ml"] = round(dose / concentration_mg_per_ml, 2)
    return _result(
        "Weight-based dose",
        round(dose, 2),
        "Verify the mg/kg and maximum against the label/formulary and have a second person double-check "
        "(10-fold errors are the most common pediatric dosing error). Use an oral syringe in mL, never teaspoons.",
        "ISMP pediatric medication safety guidance; AAP Policy Statement on Pediatric Medication Safety",
        **out,
    )


def maintenance_fluids_holliday_segar(weight_kg: float) -> dict[str, Any]:
    """Holliday-Segar ('4-2-1') maintenance IV fluid rate."""
    _require_positive(weight_kg=weight_kg)
    w = weight_kg
    per_day = 100 * min(w, 10) + 50 * min(max(w - 10, 0), 10) + 20 * max(w - 20, 0)
    per_hour = 4 * min(w, 10) + 2 * min(max(w - 10, 0), 10) + 1 * max(w - 20, 0)
    formula_per_day = per_day
    per_hour = min(per_hour, 120)  # common adult cap
    per_day = min(per_day, 120 * 24)  # keep the daily figure consistent with the hourly cap
    return _result(
        "Maintenance fluids (Holliday-Segar)",
        round(per_hour, 1),
        "Use ISOTONIC fluid (e.g. 0.9% saline or balanced crystalloid with dextrose) to avoid hospital-acquired "
        "hyponatremia (AAP 2018). Reduce in SIADH-risk states (meningitis, pneumonia, post-op), heart or kidney failure. "
        "Deficit and ongoing losses are added separately.",
        "Holliday MA, Segar WE. Pediatrics 1957;19:823; Feld LG et al. AAP Clinical Practice Guideline, Pediatrics 2018;142:e20183083",
        units="mL/h",
        ml_per_day=round(per_day),
        formula_ml_per_day=round(formula_per_day),
    )


def bedside_schwartz_egfr(creatinine_mg_dl: float, height_cm: float, age_years: float) -> dict[str, Any]:
    """Bedside Schwartz eGFR for children 1-17 years (enzymatic creatinine)."""
    _require_positive(creatinine_mg_dl=creatinine_mg_dl, height_cm=height_cm)
    if not 1 <= age_years < 18:
        raise ValueError("Bedside Schwartz is validated for ages 1-17; use CKD-EPI 2021 for adults")
    egfr = round(0.413 * height_cm / creatinine_mg_dl, 1)
    if egfr >= 90:
        interp = "Normal or high (>=90)."
    elif egfr >= 60:
        interp = "Mildly decreased (60-89)."
    elif egfr >= 30:
        interp = "Moderately decreased (30-59): adjust renally cleared drugs; nephrology input."
    elif egfr >= 15:
        interp = "Severely decreased (15-29): pediatric nephrology."
    else:
        interp = "Kidney failure (<15)."
    return _result(
        "eGFR (bedside Schwartz)",
        egfr,
        interp + " Requires stable creatinine; inaccurate in AKI and low muscle mass.",
        "Schwartz GJ et al. J Am Soc Nephrol 2009;20:629-37 (CKiD)",
        units="mL/min/1.73m²",
    )


# --- Decision rules ------------------------------------------------------


def pecarn_head_trauma(
    age_years: float,
    gcs_below_15_or_altered_mental_status: bool,
    skull_fracture_signs: bool,
    loss_of_consciousness: bool = False,
    severe_mechanism: bool = False,
    vomiting: bool = False,
    severe_headache: bool = False,
    non_frontal_scalp_hematoma: bool = False,
    not_acting_normally_per_parent: bool = False,
) -> dict[str, Any]:
    """PECARN rule for CT after minor head trauma (GCS 14-15) in children <18.
    skull_fracture_signs = palpable skull fracture (<2 y) or signs of basilar skull fracture (>=2 y).
    loss_of_consciousness: >=5 s for <2 y, any for >=2 y. Severe mechanism: MVC with ejection/rollover/death,
    pedestrian/cyclist without helmet struck by vehicle, fall >0.9 m (<2 y) or >1.5 m (>=2 y), high-impact head strike."""
    if not 0 <= age_years < 18:
        raise ValueError("age_years must be 0-17")
    under2 = age_years < 2
    if gcs_below_15_or_altered_mental_status or skull_fracture_signs:
        tier, risk, rec = "high", "~4.4%" if under2 else "~4.3%", "CT head recommended."
    else:
        intermediate = (
            [non_frontal_scalp_hematoma, loss_of_consciousness, severe_mechanism, not_acting_normally_per_parent]
            if under2
            else [loss_of_consciousness, vomiting, severe_mechanism, severe_headache]
        )
        if any(intermediate):
            tier, risk = "intermediate", "~0.9%"
            rec = (
                "Observation vs CT by shared decision: favor CT if multiple findings, worsening symptoms, "
                "age <3 months, or physician experience; otherwise observe 4-6 h."
            )
        else:
            tier, risk = "very low", "<0.02%" if under2 else "<0.05%"
            rec = "CT not recommended."
    return _result(
        "PECARN pediatric head trauma rule",
        tier,
        f"Risk of clinically important TBI {risk}. {rec} Always consider non-accidental trauma in infants.",
        "Kuppermann N et al. Lancet 2009;374:1160-70",
        age_group="<2 years" if under2 else ">=2 years",
    )


def pecarn_febrile_infant(
    age_days: int,
    well_appearing: bool,
    urinalysis_positive: bool,
    anc_per_ul: float,
    procalcitonin_ng_ml: float,
) -> dict[str, Any]:
    """PECARN rule for serious bacterial infection in febrile (>=38.0 °C) infants <=60 days.
    urinalysis_positive = any leukocyte esterase, nitrite, or >5 WBC/hpf."""
    if not 0 <= age_days <= 60:
        raise ValueError("Rule applies to infants 0-60 days old")
    caveats = []
    if not well_appearing:
        return _result(
            "PECARN febrile infant rule",
            "not applicable",
            "Ill-appearing febrile infant: full sepsis evaluation (blood, urine, CSF cultures) and immediate empiric antibiotics.",
            "Kuppermann N et al. JAMA Pediatr 2019;173:342-51; Pantell RH et al. AAP CPG, Pediatrics 2021;148:e2021052228",
        )
    if age_days <= 7:
        caveats.append("<8 days old is outside the AAP 2021 guideline: treat as high risk (full workup, admit, antibiotics).")
    elif age_days <= 21:
        caveats.append("AAP 2021: 8-21 days old still needs full workup including LP, admission and empiric antibiotics regardless of this rule.")
    else:
        caveats.append("AAP 2021 (22-60 days): use inflammatory markers to decide on LP and disposition.")
    high = urinalysis_positive or anc_per_ul > 4090 or procalcitonin_ng_ml > 1.71
    if high:
        interp = "NOT low risk for serious bacterial infection (bacteremia, meningitis, UTI): further evaluation and antibiotics per pathway."
    else:
        interp = "Low risk for serious bacterial infection (rule sensitivity ~97.7%, NPV ~99.6%); low risk of bacterial meningitis."
    return _result(
        "PECARN febrile infant rule",
        "high risk" if high else "low risk",
        interp + " " + " ".join(caveats),
        "Kuppermann N et al. JAMA Pediatr 2019;173:342-51; Pantell RH et al. AAP CPG, Pediatrics 2021;148:e2021052228",
    )


def westley_croup_score(
    level_of_consciousness: Literal["normal", "disoriented"],
    cyanosis: Literal["none", "with_agitation", "at_rest"],
    stridor: Literal["none", "with_agitation", "at_rest"],
    air_entry: Literal["normal", "decreased", "markedly_decreased"],
    retractions: Literal["none", "mild", "moderate", "severe"],
) -> dict[str, Any]:
    score = (
        {"normal": 0, "disoriented": 5}[level_of_consciousness]
        + {"none": 0, "with_agitation": 4, "at_rest": 5}[cyanosis]
        + {"none": 0, "with_agitation": 1, "at_rest": 2}[stridor]
        + {"normal": 0, "decreased": 1, "markedly_decreased": 2}[air_entry]
        + {"none": 0, "mild": 1, "moderate": 2, "severe": 3}[retractions]
    )
    if score <= 2:
        interp = "Mild croup: single dose oral dexamethasone, home with return precautions."
    elif score <= 5:
        interp = "Moderate croup: dexamethasone; nebulized epinephrine if stridor at rest; observe 2-4 h after epinephrine."
    elif score <= 11:
        interp = "Severe croup: nebulized epinephrine + dexamethasone, admit/close monitoring."
    else:
        interp = "Impending respiratory failure: emergency airway team (anesthesia/ENT/PICU)."
    return _result("Westley croup score", score, interp, "Westley CR et al. Am J Dis Child 1978;132:484-7; Bjornson CL, Johnson DW. CMAJ 2013")


def clinical_dehydration_scale(
    general_appearance: Literal["normal", "thirsty_restless_or_lethargic_but_irritable", "drowsy_limp_cold_or_sweaty"],
    eyes: Literal["normal", "slightly_sunken", "very_sunken"],
    mucous_membranes: Literal["moist", "sticky", "dry"],
    tears: Literal["present", "decreased", "absent"],
) -> dict[str, Any]:
    """Clinical Dehydration Scale (validated ages 1-36 months with gastroenteritis)."""
    score = (
        ["normal", "thirsty_restless_or_lethargic_but_irritable", "drowsy_limp_cold_or_sweaty"].index(general_appearance)
        + ["normal", "slightly_sunken", "very_sunken"].index(eyes)
        + ["moist", "sticky", "dry"].index(mucous_membranes)
        + ["present", "decreased", "absent"].index(tears)
    )
    if score == 0:
        interp = "No dehydration (<3%): continue feeding, replace ongoing losses."
    elif score <= 4:
        interp = "Some dehydration (3-6%): oral rehydration solution ~50 mL/kg over 4 h; consider single-dose ondansetron (>=6 months) if vomiting."
    else:
        interp = "Moderate-severe dehydration (>=6%): IV/IO isotonic bolus 20 mL/kg and emergency assessment; check glucose."
    return _result("Clinical Dehydration Scale", score, interp, "Friedman JN et al. J Pediatr 2004;145:201-7; Goldman RD et al. Pediatrics 2008;122:545-9")


def pediatric_appendicitis_score(
    cough_percussion_or_hop_tenderness: bool,
    anorexia: bool,
    fever_38_or_higher: bool,
    nausea_or_vomiting: bool,
    rlq_tenderness: bool,
    wbc_over_10k: bool,
    neutrophils_over_75_percent: bool,
    pain_migration_to_rlq: bool,
) -> dict[str, Any]:
    score = (
        2 * cough_percussion_or_hop_tenderness
        + anorexia
        + fever_38_or_higher
        + nausea_or_vomiting
        + 2 * rlq_tenderness
        + wbc_over_10k
        + neutrophils_over_75_percent
        + pain_migration_to_rlq
    )
    if score <= 3:
        interp = "Low risk: consider alternative diagnoses, discharge with clear return precautions or observe."
    elif score <= 6:
        interp = "Equivocal: ultrasound first (avoid CT radiation), surgical consult or serial exams."
    else:
        interp = "High risk: pediatric surgical consultation."
    return _result("Pediatric Appendicitis Score (Samuel)", int(score), interp, "Samuel M. J Pediatr Surg 2002;37:877-81")


PEDIATRIC_CALCULATORS = {
    "pediatric_vital_signs": pediatric_vital_signs,
    "estimated_weight_by_age": estimated_weight_by_age,
    "weight_based_dose": weight_based_dose,
    "maintenance_fluids_holliday_segar": maintenance_fluids_holliday_segar,
    "bedside_schwartz_egfr": bedside_schwartz_egfr,
    "pecarn_head_trauma": pecarn_head_trauma,
    "pecarn_febrile_infant": pecarn_febrile_infant,
    "westley_croup_score": westley_croup_score,
    "clinical_dehydration_scale": clinical_dehydration_scale,
    "pediatric_appendicitis_score": pediatric_appendicitis_score,
}
