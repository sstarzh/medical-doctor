---
name: infectious-disease
description: Infectious disease consults — sepsis recognition, empiric antibiotic selection by syndrome with stewardship, CAP (CURB-65), UTI, skin/soft tissue, pharyngitis (Centor), meningitis, STIs, travel medicine, and immunocompromised hosts. Use whenever infection is suspected or antimicrobials are considered.
metadata:
  last_reviewed: "2026-10-09"
  review_interval_days: "365"
  clinical_review: "pending"
---

# Infectious Disease

## Principles
- Source + host + severity + local resistance → empiric choice; obtain cultures before antibiotics when it does not delay care in sepsis.
- De-escalate at 48–72 h; shortest effective duration; document indication and stop date.
- Always check allergy history (true IgE-mediated vs intolerance). Cefazolin/3rd-gen cephalosporins are usually safe in non-severe penicillin allergy (dissimilar side chains).
- Doses must be verified with `drug_label` and adjusted to renal function.

## Sepsis
- Sepsis = infection + organ dysfunction. `calc_qsofa` is prognostic, not a screening rule-out; use lactate, `calc_mean_arterial_pressure`.
- Septic shock: antibiotics within 1 h, 30 mL/kg balanced crystalloid for hypotension or lactate ≥4, norepinephrine for MAP ≥65.

## Common syndromes (adult, immunocompetent; confirm with IDSA/local guidance)
| Syndrome | Typical empiric options | Duration |
|---|---|---|
| CAP, outpatient, no comorbidity | Amoxicillin, or doxycycline (macrolide only if local pneumococcal resistance <25%) | 5 days if stable |
| CAP, outpatient with comorbidity | Amoxicillin-clavulanate + macrolide or doxycycline; or respiratory fluoroquinolone | 5 days |
| CAP, inpatient non-severe | β-lactam (ceftriaxone) + macrolide; or respiratory FQ | 5 days |
| Uncomplicated cystitis (women) | Nitrofurantoin 5 d, TMP-SMX 3 d (if resistance <20%), fosfomycin single dose | as listed |
| Pyelonephritis | FQ 5–7 d or TMP-SMX 14 d if susceptible; ceftriaxone if inpatient | 5–14 d |
| Cellulitis, non-purulent | Cephalexin (strep coverage) | 5 d, extend if slow |
| Purulent SSTI / abscess | Incision & drainage ± TMP-SMX or doxycycline for MRSA | 5–10 d |
| GAS pharyngitis | Use `calc_centor_mcisaac`; test then penicillin V or amoxicillin | 10 d |
| Bacterial meningitis (adult) | Ceftriaxone + vancomycin (+ ampicillin if >50 y or immunocompromised) + dexamethasone | Do not delay |

CURB-65 via `calc_curb_65` informs site of care (with clinical judgment and SpO2).

## Red-flag infections
Necrotizing fasciitis (pain out of proportion, crepitus, rapid spread → surgery), neutropenic fever (ANC <500 → antipseudomonal β-lactam within 1 h), endocarditis (fever + murmur/IVDU/prosthetic valve → 3 blood cultures, echo), epidural abscess, malaria in returning traveler (same-day smear/RDT), toxic shock.

## STIs (CDC 2021)
Gonorrhea: ceftriaxone 500 mg IM single dose (1 g if ≥150 kg). Chlamydia: doxycycline 100 mg BID × 7 d (azithromycin in pregnancy). Test for HIV and syphilis; expedited partner therapy where legal. Doxy-PEP may be offered to selected high-risk patients.

## Vaccines and prevention
Refer to CDC adult/child schedules (updated yearly); HIV PrEP for people at risk; post-exposure prophylaxis within 72 h.
