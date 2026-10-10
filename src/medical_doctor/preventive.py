"""Preventive care checklist by age, sex and risk (USPSTF A/B, CDC/ACIP, AAP Bright Futures).

Deterministic and conservative: it lists what is generally recommended so the clinician-model
can personalize it. Recommendations change; each item carries its source and the output tells
users to confirm against the current guideline.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .records.model import age_years

_SOURCE_NOTE = "Recommendations change; confirm against the current USPSTF/CDC/AAP guidance and the patient's own history."


def _item(name: str, detail: str, source: str, kind: str = "screening") -> dict[str, str]:
    return {"item": name, "detail": detail, "source": source, "kind": kind}


def preventive_care(chart: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    p = chart.get("profile", {})
    social = chart.get("social_history", {})
    conditions = " ".join(c.get("name", "").lower() for c in chart.get("conditions", []))
    age = age_years(p, today)
    if age is None:
        return {"error": "Record the date of birth to compute preventive care.", "items": []}
    sex = p.get("sex_at_birth")
    female, male = sex == "female", sex == "male"
    pregnant = p.get("pregnancy_status") == "pregnant"
    bmi = None
    if p.get("weight_kg") and p.get("height_cm"):
        bmi = p["weight_kg"] / (p["height_cm"] / 100) ** 2
    smoker = social.get("tobacco") in ("current", "former")
    pack_years = social.get("pack_years") or 0
    items: list[dict[str, str]] = []

    if age < 18:
        months = age * 12
        items.append(_item("Well-child visits", "Per AAP Bright Futures periodicity (newborn, 3-5 d, 1, 2, 4, 6, 9, 12, 15, 18, 24, 30 mo, then yearly).", "AAP Bright Futures 2024"))
        items.append(_item("Immunizations", "Follow the current AAP / CDC childhood schedule; use catch-up schedule if behind.", "AAP / CDC ACIP", "vaccine"))
        if months < 36:
            items.append(_item("Developmental screening", "Standardized tool at 9, 18 and 30 months; autism screening (M-CHAT-R/F) at 18 and 24 months.", "AAP"))
        if 9 <= months <= 30:
            items.append(_item("Lead screening", "Risk assessment / blood lead at 12 and 24 months (required for Medicaid).", "AAP / CDC"))
        if 9 <= months <= 15:
            items.append(_item("Anemia screening", "Hemoglobin around 12 months.", "AAP"))
        if 9 <= age <= 11 or 17 <= age < 18:
            items.append(_item("Lipid screening", "Universal once at 9-11 and once at 17-21 years.", "AAP / NHLBI"))
        if age >= 12:
            items.append(_item("Depression screening", "Annually from 12 (PHQ-A), plus suicide risk screening.", "USPSTF B / AAP"))
            items.append(_item("HPV vaccine", "Routinely at 11-12 (can start at 9).", "CDC ACIP", "vaccine"))
            items.append(_item("Confidential adolescent visit", "Time alone with the clinician; HEADSSS; substance use (CRAFFT).", "AAP"))
        if age >= 15:
            items.append(_item("HIV screening", "At least once from 15-65.", "USPSTF A"))
        if age >= 3:
            items.append(_item("Vision and hearing", "Per Bright Futures schedule.", "AAP"))
        items.append(_item("Influenza vaccine", "Every year from 6 months.", "CDC ACIP", "vaccine"))
        return {"age_years": round(age, 1), "items": items, "note": _SOURCE_NOTE}

    # Adults
    items.append(_item("Blood pressure", "Every year if 40+ or at increased risk; every 3-5 years 18-39 with normal BP.", "USPSTF A"))
    items.append(_item("Depression screening", "Adults, including pregnant and postpartum people.", "USPSTF B"))
    if age <= 64:
        items.append(_item("Anxiety screening", "Adults 64 and younger.", "USPSTF B (2023)"))
    items.append(_item("Unhealthy alcohol use screening", "With brief counseling if positive.", "USPSTF B"))
    if social.get("tobacco") == "current" or social.get("vaping"):
        items.append(_item("Tobacco cessation", "Counseling plus pharmacotherapy (varenicline, NRT, bupropion).", "USPSTF A", "counseling"))
    if age <= 65:
        items.append(_item("HIV screening", "At least once 15-65; PrEP if at increased risk.", "USPSTF A"))
    if age <= 79:
        items.append(_item("Hepatitis C screening", "Once for adults 18-79.", "USPSTF B"))
    items.append(_item("Hepatitis B screening", "Once in adulthood (triple panel).", "CDC 2023"))
    if 35 <= age <= 70 and (bmi is None or bmi >= 25):
        items.append(_item("Prediabetes / type 2 diabetes", "Every 3 years if overweight/obese (BMI >= 25; >= 23 in Asian Americans).", "USPSTF B"))
    if 40 <= age <= 75 and "diabetes" not in conditions:
        items.append(_item("Cardiovascular risk / statin", "Lipids and 10-year risk; statin if a risk factor and risk >= 10%.", "USPSTF B", "prevention"))
    if 45 <= age <= 75:
        items.append(_item("Colorectal cancer screening", "Colonoscopy every 10 y, or FIT yearly, or stool DNA-FIT every 1-3 y, or CT colonography every 5 y.", "USPSTF A (50-75) / B (45-49)"))
    if female and 40 <= age <= 74:
        items.append(_item("Breast cancer screening", "Mammography every 2 years.", "USPSTF B (2024)"))
    if female and 21 <= age <= 65:
        items.append(_item("Cervical cancer screening", "21-29: cytology every 3 y; 30-65: HPV test every 5 y (or co-test every 5 y, or cytology every 3 y).", "USPSTF A"))
    if female and age <= 24:
        items.append(_item("Chlamydia and gonorrhea", "If sexually active (and older women at increased risk).", "USPSTF B"))
    if female and age <= 50 and not pregnant:
        items.append(_item("Folic acid", "0.4-0.8 mg daily if planning or capable of pregnancy.", "USPSTF A", "prevention"))
    if 50 <= age <= 80 and smoker and pack_years >= 20:
        quit = social.get("quit_year")
        eligible = social.get("tobacco") == "current" or (quit and (today or date.today()).year - quit <= 15)
        if eligible:
            items.append(_item("Lung cancer screening", "Annual low-dose CT (20+ pack-years, current or quit within 15 years).", "USPSTF B"))
    if male and 65 <= age <= 75 and smoker:
        items.append(_item("Abdominal aortic aneurysm", "One-time ultrasound (ever smoked).", "USPSTF B"))
    if female and age >= 65:
        items.append(_item("Osteoporosis screening", "DXA; earlier in postmenopausal women at increased risk.", "USPSTF B"))
    if age >= 65:
        items.append(_item("Fall prevention", "Exercise interventions if at increased fall risk.", "USPSTF B", "prevention"))
    if bmi is not None and bmi >= 30:
        items.append(_item("Weight management", f"BMI {bmi:.1f}: offer intensive behavioral intervention; discuss pharmacotherapy.", "USPSTF B", "counseling"))
    if pregnant:
        items.append(_item("Prenatal care", "Preeclampsia screening (BP each visit), low-dose aspirin if high risk from 12 weeks, GDM screening at 24+ weeks, syphilis/HIV/HBV screening, Tdap 27-36 weeks.", "USPSTF / ACOG"))

    # Adult vaccines (CDC ACIP adult schedule)
    items.append(_item("Influenza", "Every year.", "CDC ACIP", "vaccine"))
    items.append(_item("COVID-19", "Per the current CDC/ACIP recommendation for the season and the person's risk.", "CDC ACIP", "vaccine"))
    items.append(_item("Td/Tdap", "Tdap once, then Td or Tdap every 10 years (Tdap each pregnancy).", "CDC ACIP", "vaccine"))
    if age <= 26:
        items.append(_item("HPV", "Catch-up through 26 if not vaccinated.", "CDC ACIP", "vaccine"))
    if age >= 50:
        items.append(_item("Recombinant zoster (Shingrix)", "2 doses from 50.", "CDC ACIP", "vaccine"))
        items.append(_item("Pneumococcal", "From 50 (PCV20 or PCV21 alone, or PCV15 then PPSV23).", "CDC ACIP 2024", "vaccine"))
    if age >= 75 or (age >= 50 and conditions):
        items.append(_item("RSV", "Single dose at 75+, or 50-74 with conditions that raise risk.", "CDC ACIP", "vaccine"))

    recorded = [
        {"name": x.get("vaccine") or x.get("name"), "date": x.get("date")}
        for x in chart.get("immunizations", []) + chart.get("procedures", [])
    ]
    return {"age_years": round(age, 1), "items": items, "already_recorded": recorded, "note": _SOURCE_NOTE}
