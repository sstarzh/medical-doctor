---
name: emergency-triage
description: Emergency recognition and disposition — ABCDE, vital-sign danger thresholds, time-critical syndromes (stroke, STEMI, sepsis, anaphylaxis, PE, DKA), crisis resources, and safe-disposition rules. Use whenever a case might need emergency or same-day care.
metadata:
  last_reviewed: "2026-10-09"
  review_interval_days: "365"
  clinical_review: "pending"
---

# Emergency Triage

## Always first
1. Run `red_flag_screen` on the narrative. Never downgrade an "emergency" result without explicit contradicting data.
2. ABCDE: Airway (stridor, voice change, angioedema), Breathing (RR, SpO2, work of breathing, full sentences?), Circulation (HR, BP, perfusion, bleeding), Disability (GCS — use `calc_glasgow_coma_scale`, glucose, focal deficits, pupils), Exposure (rash, temperature, trauma).

## Adult danger vital signs (any one → urgent/emergent assessment)
- RR ≥25 or ≤8; SpO2 <92% (or <88% in known CO2 retainers); HR ≥131 or ≤40; SBP ≤90 or MAP <65; SBP ≥180 with symptoms; new confusion; temperature ≥39.1 °C or ≤35.0 °C. (NEWS2 ≥7 = emergency response; ≥5 = urgent.)

## Time-critical syndromes
| Syndrome | Recognition | Critical clock |
|---|---|---|
| Stroke | BE-FAST: balance, eyes, face, arm, speech, time | IV thrombolysis ≤4.5 h from last known well; thrombectomy up to 24 h with imaging selection |
| STEMI / ACS | Pressure-like pain, diaphoresis, dyspnea; atypical in women, elderly, diabetics | ECG ≤10 min; PCI door-to-balloon ≤90 min |
| Sepsis | Suspected infection + organ dysfunction (hypotension, confusion, lactate ≥2, oliguria) | Antibiotics ≤1 h for probable septic shock; 30 mL/kg crystalloid if hypotensive/lactate ≥4 |
| Anaphylaxis | Skin/mucosal + respiratory or circulatory compromise, or hypotension after known allergen | IM epinephrine 0.01 mg/kg (max 0.5 mg adult, 0.3 mg child) anterolateral thigh, repeat q5–15 min |
| PE | Dyspnea, pleuritic pain, tachycardia, hypoxia, syncope | Use `calc_wells_pe`/`calc_perc_rule`; massive PE → thrombolysis |
| Aortic dissection | Tearing pain to back, pulse/BP differential, neuro deficit | CT angiography; HR <60, SBP <120 |
| DKA/HHS | Polyuria, vomiting, Kussmaul breathing, glucose high, ketones | Fluids first, then insulin once K ≥3.5 |
| Meningitis | Fever, headache, neck stiffness, altered mental status, petechiae | Antibiotics immediately, do not wait for LP/CT |
| Ectopic pregnancy | Positive/possible pregnancy + pain or bleeding, syncope | Quantitative hCG + transvaginal US; unstable → OR |
| Testicular/ovarian torsion | Sudden severe unilateral pain, vomiting | Surgical detorsion ideally <6 h |
| Cauda equina | Back pain + saddle anesthesia, retention/incontinence, bilateral leg symptoms | Emergency MRI |

## Mental health crisis
- Ask directly about suicidal thoughts, plan, intent, means, and timeline — asking does not increase risk.
- Imminent risk → emergency services; do not leave the person alone; remove means.
- US 988 (call/text), UK & IE Samaritans 116 123, elsewhere findahelpline.com.

## Pediatric specifics
- Fever ≥38.0 °C in infants <90 days → same-hour emergency evaluation.
- Danger signs: lethargy/unrousable, poor feeding, grunting, retractions, non-blanching rash, bulging fontanelle, <1 wet diaper in 8 h (dehydration), bilious vomiting.

## Disposition language
- **EMERGENCY**: "Call 911/local emergency number now" — never suggest driving themselves.
- **URGENT**: "Be seen today (ED or urgent care)."
- **SOON**: "Within 1–3 days by your clinician."
- **ROUTINE**: "Schedule a regular visit."
- Always give **return precautions**: the specific symptoms that should prompt immediate care.
