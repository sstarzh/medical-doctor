---
name: lab-interpretation
description: Systematic interpretation of laboratory results — CBC, metabolic panel, acid-base (stepwise with anion gap and compensation), liver tests pattern, thyroid, iron studies, urinalysis, cardiac biomarkers, and critical values. Use whenever lab values are supplied.
metadata:
  last_reviewed: "2026-10-09"
  review_interval_days: "365"
  clinical_review: "pending"
---

# Laboratory Interpretation

Reference ranges vary by lab — prefer the reporting lab's range when provided. Always interpret **trend** and **clinical context**, and compute derived values with the calculator tools.

## Critical values requiring same-day action (typical)
K <2.8 or >6.0 mEq/L; Na <120 or >160; glucose <54 or >400 mg/dL (with symptoms/ketones); Ca (corrected) >13 or <6.5 mg/dL; Hb <7 g/dL (or acute drop ≥2); platelets <20k; ANC <500 with fever; INR >5 or any INR with bleeding; troponin above 99th percentile with rise/fall; lactate ≥4 mmol/L; pH <7.2.

## Acid-base, stepwise
1. pH: acidemia (<7.35) vs alkalemia (>7.45).
2. Primary process: PaCO2 and HCO3 direction.
3. Compensation: metabolic acidosis → expected PaCO2 = 1.5×HCO3 + 8 ± 2 (Winter). Metabolic alkalosis → PaCO2 ≈ 0.7×HCO3 + 21 ± 2. Acute respiratory acidosis HCO3 ↑1 per 10 PaCO2; chronic ↑3.5–4.
4. Anion gap with `calc_anion_gap` (supply albumin). High AG: lactate, ketones, uremia, toxic alcohols (check osmolar gap), salicylates.
5. Delta-delta: ΔAG/ΔHCO3 <1 → concurrent normal-AG acidosis; >2 → concurrent metabolic alkalosis.

## Electrolytes
- **Hyponatremia**: confirm hypotonic (serum osm); correct for glucose with `calc_corrected_sodium_hyperglycemia`; then volume status + urine osm/Na. Correction limit 8 mEq/L per 24 h (osmotic demyelination risk), 6 if high-risk.
- **Hyperkalemia**: exclude hemolysis; ECG changes → IV calcium immediately; then shift (insulin+glucose, β-agonist) and remove.
- **Calcium**: correct for albumin with `calc_corrected_calcium` or measure ionized; PTH separates PTH-mediated from non-PTH (malignancy, vitamin D, granulomatous).

## Kidney
- eGFR via `calc_egfr_ckd_epi_2021` (stable creatinine only). AKI per KDIGO: creatinine ↑≥0.3 mg/dL in 48 h or ≥1.5× baseline in 7 days, or urine <0.5 mL/kg/h for 6 h.
- Albuminuria (UACR) A1 <30, A2 30–300, A3 >300 mg/g.

## Liver pattern (R ratio = (ALT/ULN)/(ALP/ULN))
R >5 hepatocellular; R <2 cholestatic; 2–5 mixed. ALT >1000: ischemia, acetaminophen, acute viral, autoimmune, choledocholithiasis (early). Isolated bilirubin: Gilbert (unconjugated), hemolysis. Synthetic function: INR, albumin; prognosis → `calc_meld_na`.

## Hematology
- Anemia by MCV: microcytic (iron deficiency — ferritin <30 ng/mL diagnostic, <100 with inflammation; thalassemia; anemia of inflammation), normocytic (check reticulocytes: hemolysis/bleeding vs production), macrocytic (B12/folate, alcohol, liver, hypothyroid, MDS, drugs — methotrexate, hydroxyurea, zidovudine).
- Pancytopenia or blasts → urgent hematology. Isolated thrombocytopenia: check smear for clumping; consider HIT (4T score), ITP, TTP (schistocytes → emergency).

## Thyroid
TSH first. High TSH + low FT4 = primary hypothyroidism; low TSH + high FT4/T3 = hyperthyroidism. Nonthyroidal illness and biotin interfere; central disease → TSH inappropriately normal.

## Cardiac
High-sensitivity troponin: use the assay's 0/1-h or 0/2-h algorithm; myocardial injury needs a rise/fall for acute MI plus ischemic features. BNP <100 pg/mL / NT-proBNP <300 pg/mL makes acute HF unlikely (higher in CKD, lower in obesity).

## Urinalysis
Nitrite + leukocyte esterase suggests UTI; asymptomatic bacteriuria should not be treated except in pregnancy or before urologic procedures. RBC casts → glomerulonephritis. Dipstick blood without RBCs → myoglobin/hemoglobin.
