---
name: cardiology
description: Cardiovascular consults — chest pain risk stratification (HEART), ACS, atrial fibrillation (CHA2DS2-VASc, HAS-BLED, rate vs rhythm), heart failure (GDMT four pillars), hypertension, lipids and prevention, syncope, and ECG/QTc. Use for any cardiac symptom, finding, or risk question.
---

# Cardiology

## Chest pain
- ECG within 10 minutes; STEMI criteria → emergency reperfusion.
- Undifferentiated ED chest pain: `calc_heart_score` + serial high-sensitivity troponin. Low (0–3) with negative troponins → discharge with follow-up.
- Consider dissection (pulse/BP differential, tearing pain), PE (`calc_wells_pe`), pericarditis (pleuritic, positional, diffuse ST elevation + PR depression), tamponade.
- Women, older adults, and people with diabetes more often present with dyspnea, fatigue, nausea, or epigastric pain.

## Atrial fibrillation
- Stroke risk: `calc_cha2ds2_vasc`; bleeding: `calc_has_bled` (modifiable risk factors, not a reason to withhold).
- DOAC preferred over warfarin except mechanical valve or moderate-severe mitral stenosis. Dose per label (renal function via Cockcroft-Gault, age, weight, interacting drugs).
- Rate control target resting HR <110 (lenient) unless symptoms; early rhythm control is beneficial in recent-onset AF (EAST-AFNET 4). Catheter ablation first-line in HFrEF with AF in selected patients.
- Address risk factors: weight, alcohol, sleep apnea, hypertension.

## Heart failure with reduced EF (≤40%) — four pillars (ACC/AHA/HFSA 2022)
1. ARNI (sacubitril/valsartan) preferred, or ACEi/ARB
2. Evidence-based β-blocker (carvedilol, metoprolol succinate, bisoprolol)
3. MRA (spironolactone/eplerenone) if eGFR >30 and K <5.0
4. SGLT2 inhibitor (dapagliflozin/empagliflozin)
Start low, titrate every 1–2 weeks; monitor K and creatinine. Loop diuretic for congestion. SGLT2i also recommended in HFmrEF/HFpEF.

## Hypertension (ACC/AHA 2017; 2025 update directionally similar)
- Confirm with out-of-office readings. Stage 1 130–139/80–89; Stage 2 ≥140/90.
- Target <130/80 for most adults. First-line: thiazide-type diuretic, ACEi or ARB, dihydropyridine CCB. Start two agents if ≥20/10 above goal. Do not combine ACEi + ARB.
- Severe ≥180/120: emergency if end-organ damage (chest pain, neuro deficit, dyspnea, AKI, vision change); otherwise oral therapy and close follow-up.

## Lipids / prevention
- Estimate 10-year risk (PREVENT or Pooled Cohort Equations — compute with a validated tool, not by memory).
- Clinical ASCVD → high-intensity statin, LDL goal <70 mg/dL (<55 very high risk per ESC); add ezetimibe, then PCSK9 inhibitor.
- LDL ≥190 → high-intensity statin, consider familial hypercholesterolemia.
- Aspirin for primary prevention is no longer routine (USPSTF 2022: individualized for 40–59 with ≥10% risk; not started ≥60).

## Syncope
High-risk features: exertional, supine, preceded by palpitations, structural heart disease, abnormal ECG (QT, Brugada, pre-excitation, block), family history of sudden death → admit/urgent cardiology. Check `calc_qtc`.
