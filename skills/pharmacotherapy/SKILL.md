---
name: pharmacotherapy
description: Safe prescribing and medication review — verify every dose against the FDA label, renal/hepatic adjustment, interactions (CYP, QT, serotonergic, additive CNS/bleeding), pregnancy/lactation, Beers criteria for older adults, and deprescribing. Use whenever a drug is mentioned, recommended, or a medication list is provided.
---

# Pharmacotherapy

## Mandatory verification
- **Never quote a dose from memory.** Call `drug_label` (sections `dosage_and_administration`, `boxed_warning`, `contraindications`, `use_in_specific_populations`).
- Normalize names with `normalize_drug` (brand ↔ generic, combinations).
- With ≥2 drugs, run `drug_interaction_check` on the full list. Absence of a hit is not proof of safety.
- Renal dosing: most labels use Cockcroft-Gault CrCl → `calc_creatinine_clearance_cockcroft_gault`; staging CKD → `calc_egfr_ckd_epi_2021`.

## High-risk interaction mechanisms to always consider
- **QT prolongation** (macrolides, fluoroquinolones, ondansetron, haloperidol, methadone, citalopram/escitalopram, class IA/III antiarrhythmics, azoles) — check `calc_qtc`, K, Mg.
- **Serotonin syndrome** (SSRIs/SNRIs, MAOIs, linezolid, tramadol, fentanyl, meperidine, triptans [low risk], dextromethorphan, St John's wort).
- **CYP3A4 inhibitors** (clarithromycin, ketoconazole/itraconazole, ritonavir/cobicistat, grapefruit, diltiazem/verapamil) with statins (simvastatin/lovastatin), DOACs, calcium-channel blockers, tacrolimus, colchicine.
- **CYP2C9/vitamin K** — warfarin with TMP-SMX, fluconazole, metronidazole, amiodarone (↑ INR).
- **Bleeding**: anticoagulant + antiplatelet + NSAID + SSRI.
- **Hyperkalemia**: ACEi/ARB/ARNI + MRA (spironolactone) + TMP-SMX + K supplements, especially with CKD.
- **CNS/respiratory depression**: opioids + benzodiazepines/gabapentinoids/alcohol (FDA boxed warning).
- **"Triple whammy" AKI**: ACEi/ARB + diuretic + NSAID.
- **Nephrotoxins**: aminoglycosides, vancomycin + piperacillin-tazobactam, contrast, NSAIDs.

## Special populations
- **Pregnancy/lactation**: use label sections 8.1/8.2 (PLLR narrative). Known teratogens: isotretinoin, valproate, warfarin, methotrexate, ACEi/ARB (2nd–3rd trimester), mycophenolate, tetracyclines (after 15 weeks), statins (generally stopped).
- **Older adults (≥65)**: AGS Beers Criteria 2023 — avoid first-generation antihistamines, long-acting sulfonylureas, benzodiazepines/Z-drugs, muscle relaxants, chronic NSAIDs; anticholinergic burden; fall risk.
- **Hepatic impairment**: Child-Pugh class guides adjustments per label.
- **Pediatrics**: weight-based dosing with adult-dose ceilings; verify with label pediatric section.

## Medication review output
1. Problems ranked by severity (interaction, contraindication, dose error, duplication, untreated indication, no indication, adherence/cost).
2. Specific recommended change + rationale + citation.
3. Monitoring (labs, ECG, timeframe).
4. Deprescribing candidates with taper plan when needed (benzodiazepines, PPIs, opioids, antipsychotics in dementia).
