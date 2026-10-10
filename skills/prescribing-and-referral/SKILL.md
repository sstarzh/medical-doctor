---
name: prescribing-and-referral
description: How to propose medicines and referrals safely as calls to action — draft_prescription and draft_referral, what the automated checks mean, controlled-substance and pediatric rules, referral urgency, and how to present drafts to patients and clinicians. Use whenever treatment with a prescription medicine or a specialist referral is being considered.
metadata:
  last_reviewed: "2026-10-10"
  review_interval_days: "365"
  clinical_review: "pending"
---

# Prescribing and Referral (Drafts, Not Orders)

This service never prescribes or refers. It prepares **drafts** that a licensed clinician reviews, changes, and issues. Present every draft as a call to action: "take this to your clinician", never "start taking".

## Prescription drafts — `draft_prescription`
1. Decide whether a prescription medicine is warranted at all; consider non-drug and OTC options first.
2. Choose the agent from guidelines (cite them). Verify dose, frequency, maximum, renal/hepatic adjustment, pregnancy/lactation and pediatric dosing against the FDA label (`drug_label`), and compute weight-based doses with `calc_weight_based_dose`.
3. Call `draft_prescription` with the patient_id so the chart-based checks run: allergies and cross-reactivity, duplicate therapy and same-class duplication, label interactions with current medicines, pregnancy and lactation, pediatric age limits and missing weight, Beers criteria (≥65), and kidney function.
4. Read the checks:
   - **fail** → the draft is blocked. Explain why and propose a safer alternative.
   - **warn** → mention it explicitly in the clinician summary.
   - **not_checked** → say what was not verified (e.g., no chart, no eGFR on file, interaction source offline).
5. Controlled substances (opioids, benzodiazepines, stimulants, Z-drugs, testosterone, pregabalin, etc.) are never drafted: they require direct evaluation by a licensed prescriber.
6. Antibiotics: draft only with a clear bacterial indication and the shortest effective duration (stewardship).

## Referral drafts — `draft_referral`
- Choose the right specialty and urgency: **emergency** (go now — the tool redirects to emergency care instead of drafting), **urgent** (24–72 h), **soon** (1–4 weeks), **routine**.
- Write a precise clinical question ("Please evaluate for X; is Y indicated?"), not "please see".
- Suggest workup that makes the visit useful (labs, imaging, diaries) and what to bring.
- Include pertinent history from the chart (the tool adds it when patient_id is given).

## Presenting drafts
- For the patient: plain language, what it is for, that a clinician must approve it, what to ask them, and the safety-net symptoms that need urgent care meanwhile.
- For the clinician: the structured draft, indication, checks with warnings, and evidence citations.
- Save drafts to the care plan (`save=true`) so the next visit can follow up on them.
