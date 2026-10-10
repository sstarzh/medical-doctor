---
name: pediatric-emergency
description: Pediatric emergency medicine — PALS approach, shock and sepsis, respiratory emergencies (asthma, croup, bronchiolitis, foreign body, epiglottitis), seizures and status epilepticus, head trauma (PECARN), dehydration, surgical abdomen in children, ingestions and button batteries, anaphylaxis, DKA, and child abuse recognition. Use for any acutely ill or injured child.
metadata:
  last_reviewed: "2026-10-09"
  review_interval_days: "365"
  clinical_review: "pending"
---

# Pediatric Emergency Medicine

Run `red_flag_screen`, then `calc_pediatric_vital_signs`. Doses below are typical references — **always confirm with the label/formulary and `calc_weight_based_dose`.**

## Recognition (PALS)
- Pediatric Assessment Triangle → ABCDE → glucose ("don't ever forget glucose": hypoglycemia <60 mg/dL → dextrose 10% 2–5 mL/kg).
- Respiratory distress (tachypnea, retractions, grunting, nasal flaring, head bobbing) vs failure (fatigue, poor effort, falling SpO2, bradycardia, altered consciousness).
- Compensated shock: tachycardia, cap refill >2 s, cool mottled skin, low urine output, with normal BP. Hypotension = decompensated.

## Shock and sepsis (Surviving Sepsis Campaign Pediatric 2020)
- Septic shock: antibiotics within 1 h of recognition; 10–20 mL/kg isotonic crystalloid boluses, reassessing for fluid overload after each (crackles, hepatomegaly), up to 40–60 mL/kg in the first hour where intensive care is available. Smaller boluses (10 mL/kg) in suspected cardiac disease.
- Fluid-refractory → epinephrine or norepinephrine (peripheral access acceptable initially).

## Respiratory
- **Asthma exacerbation**: albuterol (MDI with spacer is as good as nebulizer), ipratropium for moderate–severe, early systemic steroid (dexamethasone or prednisolone), IV magnesium for severe; avoid sedation. Pulse ox <92% after treatment → admit.
- **Croup**: score with `calc_westley_croup_score`; dexamethasone single dose for all severities; nebulized epinephrine for stridor at rest.
- **Bronchiolitis** (<2 y): supportive only — suction, hydration, O2 if SpO2 <90%; no routine albuterol, steroids, antibiotics, or chest X-ray (AAP 2014). High risk: <12 weeks, prematurity, apnea, congenital heart or lung disease.
- **Epiglottitis/bacterial tracheitis/retropharyngeal abscess**: toxic, drooling, tripod, muffled voice → keep calm with parent, no throat exam, airway team.
- **Foreign body aspiration**: sudden choking/coughing, unilateral wheeze → bronchoscopy even if X-ray normal.

## Seizures
- Status epilepticus (≥5 min): benzodiazepine first — IM/IN/buccal midazolam or IV lorazepam (0.1 mg/kg, max 4 mg); repeat once. Second line at 20–40 min: levetiracetam, fosphenytoin, or valproate (equivalent efficacy, ESETT/ConSEPT/EcLiPSE); avoid valproate <2 years or with suspected metabolic/liver disease.
- **Simple febrile seizure** (6 months–5 years, generalized, <15 min, once in 24 h, returns to baseline): no routine labs, EEG, or imaging; LP only if meningeal signs or concerning history (AAP 2011). Reassure: ~30% recur; no increase in epilepsy for simple febrile seizures; antipyretics do not prevent recurrence.
- Complex or focal seizure, persistent altered mental status, or infant → broader workup.

## Head trauma
Use `calc_pecarn_head_trauma`. Infants with scalp hematoma, unexplained injuries, or inconsistent history → consider abuse (skeletal survey under 2 years).

## Abdomen
- **Pyloric stenosis**: 2–8 weeks, projectile non-bilious vomiting, hungry after vomiting; hypochloremic hypokalemic metabolic alkalosis; ultrasound.
- **Malrotation with volvulus**: bilious vomiting at any age (most in first month) → emergency surgical consult.
- **Intussusception**: 3 months–3 years, episodic colicky pain with drawing up legs, lethargy between episodes, vomiting, late "currant jelly" stool → ultrasound; air/contrast enema.
- **Appendicitis**: `calc_pediatric_appendicitis_score`; ultrasound first; younger children perforate more often.
- **Testicular/ovarian torsion**: emergency at any age; don't let embarrassment delay care in adolescents.
- **Incarcerated inguinal hernia**, **Hirschsprung enterocolitis** (delayed meconium, distension, fever).

## Dehydration and gastroenteritis
Score with `calc_clinical_dehydration_scale`. ORS first-line, small frequent volumes; single oral ondansetron dose for vomiting ≥6 months. IV if severe or ORS fails. No antidiarrheals. Check glucose in lethargic children. Blood in stool + pallor/oliguria → think HUS (E. coli O157).

## Ingestions ("one pill can kill" a toddler)
Opioids (incl. buprenorphine), sulfonylureas, calcium-channel and β-blockers, clonidine/imidazolines, tricyclics, methyl salicylate (oil of wintergreen), camphor, cannabis edibles (toddler coma), nicotine liquid, iron. Call Poison Control (US 1-800-222-1222).
- **Button battery**: emergency X-ray and endoscopic removal within hours (esophageal burn in 2 h). While en route, children ≥1 year with ingestion within 12 h: honey 10 mL every 10 min up to 6 doses (National Capital Poison Center guideline).
- Two or more **magnets** (or magnet + metal) → emergency (bowel perforation).

## Anaphylaxis
IM epinephrine 0.01 mg/kg (max 0.3 mg prepubertal / 0.5 mg adolescent) into the anterolateral thigh; auto-injector 0.1 mg (7.5–15 kg), 0.15 mg (15–30 kg), 0.3 mg (≥30 kg). Repeat every 5–15 min. Antihistamines and steroids are adjuncts only. Observe for biphasic reaction; prescribe 2 auto-injectors and an action plan.

## Pediatric DKA
Higher risk of **cerebral edema** (headache, bradycardia, hypertension, altered consciousness → mannitol or 3% saline). Fluids: 10–20 mL/kg bolus then deficit replacement over 24–48 h (PECARN FLUID: rate/sodium content did not change neuro outcomes). Insulin infusion 0.05–0.1 U/kg/h, no bolus; no bicarbonate.

## Child abuse — recognize
- **TEN-4-FACESp**: bruising on Torso, Ears, Neck, Frenulum, Angle of jaw, Cheeks, Eyelids, Subconjunctivae in children ≤4 years; any bruising in an infant <4.99 months; Patterned bruising.
- Sentinel injuries in pre-mobile infants (bruise, oral injury), fractures without adequate mechanism, burns with immersion pattern, inconsistent or changing history.
- Workup by age (skeletal survey <2 y, head imaging, labs) and report to child protective services.
