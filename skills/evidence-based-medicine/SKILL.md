---
name: evidence-based-medicine
description: Finding, grading and citing evidence — hierarchy of evidence, GRADE, NNT/NNH, absolute vs relative risk, guideline sources by specialty, and rules against fabricated citations. Use whenever making a management recommendation or citing a study.
---

# Evidence-Based Medicine

## Retrieval workflow
1. Prefer current guidelines (last 5 years) from the issuing society; then systematic reviews/meta-analyses; then RCTs; then cohort studies.
2. Use `pubmed_search` with `high_quality_only=true`; read abstracts with `pubmed_abstract` before relying on a result.
3. For drug facts, the FDA label (`drug_label`) is the primary source for dosing, contraindications and boxed warnings.
4. **Never fabricate** a PMID, DOI, trial name, statistic or guideline year. If unverified, label it "(unverified — confirm)".

## Key guideline publishers
- Cardiology: ACC/AHA, ESC. Stroke: AHA/ASA. Diabetes: ADA Standards of Care (annual). Kidney: KDIGO.
- Infectious disease: IDSA, CDC (STI treatment, immunization schedules), WHO. Antimicrobial stewardship: local antibiogram.
- Pulmonary: GOLD (COPD, annual), GINA (asthma, annual), ATS/IDSA (CAP).
- Obstetrics/Gynecology: ACOG. Pediatrics: AAP. Screening: USPSTF. UK: NICE.
- Oncology: NCCN, ASCO. GI: AGA, ACG, AASLD. Rheum: ACR/EULAR. Psychiatry: APA. Critical care: Surviving Sepsis Campaign.

## Interpreting effect sizes
- Report **absolute risk reduction (ARR)** and **NNT = 1/ARR**, not just relative risk.
- NNH for harms; weigh NNT vs NNH explicitly.
- Statistically significant ≠ clinically meaningful; check confidence intervals against a minimal important difference.
- Surrogate endpoints (LDL, HbA1c, BMD) do not guarantee patient-important outcomes.

## GRADE certainty
High / Moderate / Low / Very low; recommendation **strong** ("recommend") vs **conditional** ("suggest"). State both when citing a guideline.

## Applicability
Ask: does this patient resemble the trial population (age, comorbidity, kidney function, pregnancy)? Note exclusions explicitly.
