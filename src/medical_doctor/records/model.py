"""Patient chart data model (FHIR-aligned field names, strict validation, bounded sizes)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Short = Field(default=None, max_length=200)
Long = Field(default=None, max_length=5000)


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Profile(_Item):
    """Demographics and the facts every clinical decision depends on."""

    name: str | None = Short
    relationship: Literal["self", "child", "partner", "parent", "other"] = "self"
    date_of_birth: date | None = None
    sex_at_birth: Literal["female", "male", "intersex", "unknown"] | None = None
    gender_identity: str | None = Short
    pronouns: str | None = Short
    pregnancy_status: Literal["not_pregnant", "pregnant", "postpartum", "possible", "unknown"] | None = None
    gestational_weeks: float | None = Field(default=None, ge=0, le=45)
    breastfeeding: bool | None = None
    weight_kg: float | None = Field(default=None, gt=0, le=500)
    height_cm: float | None = Field(default=None, gt=0, le=275)
    weight_date: date | None = None
    blood_type: str | None = Field(default=None, max_length=10)
    preferred_language: str | None = Short
    primary_clinician: str | None = Short
    preferred_pharmacy: str | None = Short
    emergency_contact: str | None = Short


class SocialHistory(_Item):
    tobacco: Literal["never", "former", "current"] | None = None
    pack_years: float | None = Field(default=None, ge=0, le=300)
    quit_year: int | None = Field(default=None, ge=1900, le=2100)
    vaping: bool | None = None
    alcohol_drinks_per_week: float | None = Field(default=None, ge=0, le=500)
    substances: str | None = Short
    occupation: str | None = Short
    physical_activity: str | None = Short
    diet: str | None = Short
    sexual_history: str | None = Long
    living_situation: str | None = Short
    recent_travel: str | None = Short
    firearms_at_home: bool | None = None


class Condition(_Item):
    name: str = Field(max_length=200)
    code: str | None = Field(default=None, max_length=40)  # ICD-10-CM / SNOMED CT
    status: Literal["active", "resolved", "remission", "suspected"] = "active"
    onset: str | None = Field(default=None, max_length=40)
    notes: str | None = Long


class Medication(_Item):
    name: str = Field(max_length=200)
    dose: str | None = Field(default=None, max_length=100)
    route: str | None = Field(default=None, max_length=40)
    frequency: str | None = Field(default=None, max_length=100)
    indication: str | None = Short
    status: Literal["active", "stopped", "as_needed"] = "active"
    started: str | None = Field(default=None, max_length=40)
    prescriber: str | None = Short
    notes: str | None = Long


class Allergy(_Item):
    substance: str = Field(max_length=200)
    reaction: str | None = Short
    severity: Literal["mild", "moderate", "severe", "anaphylaxis", "unknown"] = "unknown"
    category: Literal["drug", "food", "environment", "other"] = "drug"
    type: Literal["allergy", "intolerance"] = "allergy"
    notes: str | None = Long


class Immunization(_Item):
    vaccine: str = Field(max_length=200)
    date: str | None = Field(default=None, max_length=40)
    dose_number: int | None = Field(default=None, ge=1, le=20)
    notes: str | None = Long


class Procedure(_Item):
    name: str = Field(max_length=200)
    date: str | None = Field(default=None, max_length=40)
    notes: str | None = Long


class FamilyHistory(_Item):
    relation: str = Field(max_length=60)
    condition: str = Field(max_length=200)
    age_at_onset: int | None = Field(default=None, ge=0, le=120)
    notes: str | None = Long


class Observation(_Item):
    """A vital sign or lab result."""

    name: str = Field(max_length=120)  # e.g. "blood pressure", "HbA1c", "creatinine", "eGFR"
    value: str = Field(max_length=60)
    unit: str | None = Field(default=None, max_length=30)
    date: str | None = Field(default=None, max_length=40)
    category: Literal["vital", "lab", "imaging", "other"] = "lab"
    reference_range: str | None = Field(default=None, max_length=60)
    notes: str | None = Long


class Note(_Item):
    """Visit note, symptom diary entry, or document summary."""

    title: str = Field(max_length=200)
    text: str = Field(max_length=20_000)
    date: str | None = Field(default=None, max_length=40)


class CarePlanItem(_Item):
    """A proposed action: prescription draft, referral draft, test, or follow-up (never an order)."""

    kind: Literal["prescription_draft", "referral_draft", "test", "follow_up", "lifestyle"]
    status: Literal["proposed", "accepted", "completed", "declined", "cancelled"] = "proposed"
    summary: str = Field(max_length=500)
    due: str | None = Field(default=None, max_length=40)
    details: dict[str, Any] = Field(default_factory=dict)


# section -> (model, singleton?)
SECTIONS: dict[str, tuple[type[_Item], bool]] = {
    "profile": (Profile, True),
    "social_history": (SocialHistory, True),
    "conditions": (Condition, False),
    "medications": (Medication, False),
    "allergies": (Allergy, False),
    "immunizations": (Immunization, False),
    "procedures": (Procedure, False),
    "family_history": (FamilyHistory, False),
    "observations": (Observation, False),
    "notes": (Note, False),
    "care_plan": (CarePlanItem, False),
}

LIST_SECTIONS = [s for s, (_, single) in SECTIONS.items() if not single]
MAX_ITEMS_PER_SECTION = 2000
MAX_PATIENTS_PER_ACCOUNT = 20
MAX_ITEM_BYTES = 64 * 1024


def validate_item(section: str, data: dict[str, Any]) -> dict[str, Any]:
    if section not in SECTIONS:
        raise ValueError(f"Unknown section {section!r}. Sections: {', '.join(SECTIONS)}")
    model, _ = SECTIONS[section]
    clean = model.model_validate(data).model_dump(mode="json", exclude_none=True)
    if len(json.dumps(clean)) > MAX_ITEM_BYTES:
        raise ValueError(f"Entry too large (max {MAX_ITEM_BYTES // 1024} KB)")
    return clean


def age_years(profile: dict[str, Any], today: date | None = None) -> float | None:
    dob = profile.get("date_of_birth")
    if not dob:
        return None
    born = date.fromisoformat(dob) if isinstance(dob, str) else dob
    today = today or date.today()
    return (today - born).days / 365.25
