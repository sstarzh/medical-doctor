---
name: pediatric-infectious-disease
description: Common and dangerous pediatric infections — fever without a source by age, acute otitis media, strep pharyngitis, sinusitis, pneumonia, urinary tract infection, skin infections, Kawasaki disease and MIS-C, orbital cellulitis, meningitis, measles, pertussis, and antibiotic dosing/stewardship in children. Use whenever a child may have an infection.
---

# Pediatric Infectious Disease

Doses are typical references; **verify with the label/formulary and `calc_weight_based_dose`**, and never exceed the adult dose.

## Fever by age
- **0–28 days**: full workup including LP, admit, empiric antibiotics (ampicillin + gentamicin or ceftazidime/cefepime; add acyclovir if HSV risk).
- **29–60 days**: AAP 2021 febrile infant guideline; `calc_pecarn_febrile_infant` with UA, ANC, procalcitonin.
- **3–36 months**: well-appearing and fully immunized → bacteremia is rare; look for UTI (urine test, especially girls <24 months, uncircumcised boys) and a focus. Ill-appearing → sepsis evaluation.
- **Fever ≥5 days** → think Kawasaki disease, MIS-C, UTI, pneumonia, osteomyelitis, EBV, and (with travel) malaria/typhoid/dengue.

## Acute otitis media (AAP 2013)
- Diagnose only with a **moderately to severely bulging TM**, or mild bulging with new ear pain or intense erythema, or new otorrhea not from otitis externa.
- Antibiotics: all <6 months; 6–23 months if bilateral or severe (otalgia >48 h, temperature ≥39 °C); otherwise **observation with a safety-net prescription** is an option.
- First line: high-dose amoxicillin 80–90 mg/kg/day divided twice daily. Amoxicillin-clavulanate if amoxicillin in the past 30 days, purulent conjunctivitis, or recurrent AOM unresponsive to amoxicillin. Duration 10 days <2 years, 7 days for 2–5 years, 5–7 days ≥6 years.
- Pain control always (acetaminophen/ibuprofen).

## Pharyngitis
- Viral features (cough, rhinorrhea, conjunctivitis, hoarseness, ulcers) → don't test. Generally don't test children <3 years.
- Use `calc_centor_mcisaac`; confirm with rapid antigen test (back-up culture if negative in children).
- GAS: amoxicillin (once-daily dosing is acceptable) or penicillin V for 10 days; IM benzathine penicillin if adherence is doubtful. Treatment prevents rheumatic fever.
- Complications: peritonsillar abscess (trismus, uvular deviation, "hot potato" voice), retropharyngeal abscess (neck stiffness/torticollis, drooling in young children).

## Sinusitis (AAP 2013)
Diagnose only if symptoms persist ≥10 days without improvement, severe onset (fever ≥39 °C with purulent discharge ≥3 days), or "double worsening". Amoxicillin ± clavulanate. Orbital or intracranial signs → emergency.

## Pneumonia (PIDS/IDSA 2011; short-course trials)
- Preschool pneumonia is mostly viral; don't routinely give antibiotics for wheeze-predominant illness.
- Bacterial CAP, outpatient, immunized: high-dose amoxicillin; **5 days is non-inferior to 10** (SCOUT-CAP, SAFER). Atypical (school-age, gradual onset) → azithromycin.
- Admit for hypoxemia (SpO2 <90–92%), age <3–6 months, respiratory distress, dehydration, or failed outpatient therapy.

## Urinary tract infection (AAP 2011/2016, ages 2–24 months)
- Screen febrile infants/toddlers; diagnose with UA plus culture from a catheter or suprapubic sample (bag specimens only to rule out). Positive culture ≥50,000 CFU/mL with pyuria (lower thresholds are increasingly accepted).
- Oral therapy is as effective as IV if tolerated (cephalexin, cefixime, or by local antibiogram); 7–10 days for febrile UTI.
- Renal and bladder ultrasound after the first febrile UTI; VCUG only if ultrasound is abnormal or for recurrence.

## Skin and soft tissue
Impetigo (topical mupirocin if limited), cellulitis (cephalexin), abscess (drainage ± MRSA coverage). **Periorbital vs orbital cellulitis**: proptosis, painful or limited eye movements, or vision change → orbital → CT and IV antibiotics. Rapidly spreading pain out of proportion (e.g., after varicella) → necrotizing infection.

## Kawasaki disease (AHA 2017) and MIS-C
- Fever ≥5 days plus ≥4 of: bilateral non-exudative conjunctival injection; oral changes (red cracked lips, strawberry tongue); polymorphous rash; extremity changes (edema/erythema, later periungual peeling); cervical lymph node ≥1.5 cm.
- **Incomplete Kawasaki** (especially infants <6 months, who may have prolonged fever alone): CRP/ESR and supplemental labs + echocardiogram.
- Treatment: IVIG 2 g/kg within 10 days of fever onset plus aspirin (specialist-directed exception to the aspirin rule); echo at diagnosis, 1–2 weeks and 4–6 weeks.
- MIS-C: fever, multi-system inflammation, recent SARS-CoV-2 → shock and myocardial dysfunction risk; hospitalize.

## Meningitis (beyond the neonatal period)
Fever + headache, neck stiffness, irritability, bulging fontanelle, petechiae/purpura. Ceftriaxone (meningitic dosing) + vancomycin; dexamethasone for suspected Hib/pneumococcal meningitis (give before or with the first antibiotic dose). Do not delay antibiotics for CT/LP.

## Vaccine-preventable diseases (rising with lower coverage)
- **Measles**: fever with cough, coryza, conjunctivitis, then a rash spreading from the hairline downward; Koplik spots. Highly contagious → call ahead, isolate, report to public health; vitamin A for children with measles (WHO); post-exposure MMR within 72 h or immune globulin within 6 days.
- **Pertussis**: paroxysmal cough, whoop, post-tussive emesis; infants present with **apnea or cyanosis** without a whoop → admit young infants. Azithromycin (preferred in infants); prophylaxis for close contacts.
- **Varicella**: supportive; avoid ibuprofen/aspirin; antiviral if ≥13 years, chronic skin/lung disease, on steroids, or immunocompromised.

## Stewardship
Viral URI, bronchiolitis, and most diarrhea need no antibiotics. Watch-and-wait prescriptions reduce antibiotic use. Document allergy accurately — most "penicillin-allergic" children (benign rash) are not allergic; refer for direct oral amoxicillin challenge.
