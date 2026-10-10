---
name: pediatrics
description: Core pediatric practice for infants, children and adolescents — pediatric history and exam by age, age-specific vital signs, weight-based dosing safety, drugs to never give children, talking with parents, and when to use the other pediatric skills. Use whenever the patient is under 18.
metadata:
  last_reviewed: "2026-10-09"
  review_interval_days: "365"
  clinical_review: "pending"
---

# Pediatrics (Core)

Children are not small adults: physiology, normal values, diseases, doses, and communication all change with age. Pair this skill with **pediatric-emergency**, **neonatology** (≤28 days and young infants), **pediatric-infectious-disease**, and **well-child-development**.

## Pediatric history
- Age in **days** for neonates, **weeks/months** under 2 years. **Weight** in kg (measured, dated). Ask for both before any dose.
- Birth history: gestational age, birth weight, NICU stay, maternal GBS/HSV/infections, complications.
- Feeding (breast/formula, volumes, frequency), wet diapers (≥6/day after day 5), stools, sleep.
- Growth trajectory (crossing percentile lines down is a red flag), development (loss of skills is always abnormal), immunization status (verify; under-immunized children have different risks).
- Family and social: who cares for the child, daycare, smoke exposure, home safety, food security, firearms in the home.
- Adolescents: interview part of the visit alone (HEADSSS — see well-child-development).

## Exam by age
- Start with the **Pediatric Assessment Triangle** (appearance, work of breathing, circulation to skin) from across the room. "Ill-appearing" overrides normal numbers.
- Compare vitals to age norms with `calc_pediatric_vital_signs`. Tachycardia is the earliest sign of shock; **hypotension is late** and means decompensation; bradycardia in a sick child is pre-arrest.
- Capillary refill >2 s, mottling, cool extremities = poor perfusion. Count the respiratory rate for a full 30–60 seconds.
- Infants: fontanelle, tone, suck, cry, jaundice, hips, red reflex, femoral pulses.

## Dosing safety
- Every dose: **mg/kg from the label or a pediatric formulary** (`drug_label` pediatric use section) → arithmetic with `calc_weight_based_dose` (caps at adult maximum) → volume in mL for the exact concentration.
- 10-fold errors are the classic pediatric error; restate the dose in mg AND mL, use oral syringes, never teaspoons.
- Common OTC references (verify on the product): acetaminophen ~15 mg/kg every 4–6 h (max 5 doses/24 h, ≤75 mg/kg/day, never above adult max); ibuprofen ~10 mg/kg every 6–8 h, only ≥6 months and when drinking (avoid in dehydration).
- Kidney function: `calc_bedside_schwartz_egfr`. Maintenance fluids: `calc_maintenance_fluids_holliday_segar` (isotonic).
- Emergency without a weight: length-based tape, else `calc_estimated_weight_by_age`.

## Never in children (unless a specialist specifically directs)
- Aspirin for viral illness/fever under 18 (Reye syndrome) — exception: Kawasaki disease under specialist care.
- Honey under 12 months (infant botulism).
- Codeine or tramadol under 12 years, or after tonsillectomy/adenoidectomy under 18 (FDA contraindication).
- OTC cough/cold medicines under 4 years; promethazine under 2 years (boxed warning, respiratory depression).
- Loperamide under 2 years; bismuth subsalicylate (salicylate) in children with viral illness.
- Fluoroquinolones and tetracyclines are generally avoided — but **doxycycline is fine at any age for short courses (≤21 days)**, e.g. Rocky Mountain spotted fever.
- Benzocaine teething gels under 2 years (methemoglobinemia).

## Talking with parents and caregivers
- Parents know their child: "not acting like themselves" is a meaningful finding.
- Address the specific worry; explain fever is a symptom, not the disease, and that fever height alone does not equal severity.
- Give **concrete safety-netting**: breathing hard or fast, color change, not drinking or no wet diaper in 8 h, unusually sleepy/hard to wake, non-blanching rash, seizure, fever in an infant <3 months, or "you are worried" → seek care.
- Consent and confidentiality: parents consent for minors; adolescents have confidential care rights that vary by jurisdiction (sexual health, mental health, substance use).

## Safeguarding
Injury inconsistent with the history or developmental stage, delayed presentation, sentinel injuries in infants, and bruising patterns (TEN-4-FACESp) need a child-protection evaluation. Clinicians are mandated reporters.
