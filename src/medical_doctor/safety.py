"""Deterministic red-flag screening.

This runs on every consult *before* any model reasoning. It is deliberately
over-sensitive: a false alarm costs a sentence, a missed stroke costs a brain.
It does not replace clinical triage; it guarantees that obvious emergencies
are always surfaced, even if the LLM layer is unavailable or wrong.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Literal

Urgency = Literal["emergency", "urgent", "routine"]

_URGENCY_RANK = {"routine": 0, "urgent": 1, "emergency": 2}

# NegEx-style negation: a cue negates the words after it up to the end of the
# clause (punctuation or a contrastive conjunction), capped at ~60 characters.
_NEGATION_SCOPE = re.compile(
    r"\b(?:no|not|denies|denied|denying|without|negative for|absence of|free of|never had|ruled out)\b"
    r"[^.;,:\n]{0,60}?(?=[.;,:\n]|\bbut\b|\bhowever\b|\bthough\b|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RedFlagRule:
    id: str
    condition: str
    urgency: Urgency
    patterns: tuple[str, ...]
    action: str
    # False for findings that are themselves phrased negatively ("no wet diaper").
    negatable: bool = True


@dataclass
class RedFlagHit:
    id: str
    condition: str
    urgency: Urgency
    matched: str
    action: str


@dataclass
class TriageResult:
    urgency: Urgency
    hits: list[RedFlagHit] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict:
        return {"urgency": self.urgency, "message": self.message, "red_flags": [asdict(h) for h in self.hits]}


CALL_EMS = "Call emergency services now (911 in the US / 112 in EU / 999 UK). Do not drive yourself."

RULES: tuple[RedFlagRule, ...] = (
    RedFlagRule(
        "acs",
        "Possible acute coronary syndrome",
        "emergency",
        (
            r"chest (pain|pressure|tightness|heaviness|discomfort)",
            r"crushing (chest|pain)",
            r"pain (radiating|radiates|spreading) (to|into) (the )?(left )?(arm|jaw|neck|back)",
        ),
        CALL_EMS + " Chew 325 mg aspirin if no allergy/bleeding risk and advised by dispatcher. ECG within 10 minutes of arrival.",
    ),
    RedFlagRule(
        "stroke",
        "Possible stroke (BE-FAST)",
        "emergency",
        (
            r"(face|facial) (droop|drooping|weakness|numbness)",
            r"(sudden|acute)[^.]{0,30}(weakness|numbness) (of|in|on) (one|the (left|right))",
            r"(one[- ]sided|unilateral|left[- ]sided|right[- ]sided) (weakness|numbness|paralysis)",
            r"(slurred|garbled) speech|trouble (speaking|talking)|can'?t (speak|talk)",
            r"(sudden|acute) (loss of )?(vision|blindness)|sudden (double vision|loss of balance)",
        ),
        CALL_EMS + " Note the last-known-well time: thrombolysis/thrombectomy are time-critical.",
    ),
    RedFlagRule(
        "thunderclap",
        "Thunderclap headache (subarachnoid hemorrhage until proven otherwise)",
        "emergency",
        (r"worst headache( of (my|their|his|her) life)?", r"thunderclap", r"sudden (severe|explosive) headache"),
        CALL_EMS + " Needs non-contrast CT head; LP if CT negative and >6 h.",
    ),
    RedFlagRule(
        "anaphylaxis",
        "Possible anaphylaxis",
        "emergency",
        (
            r"(throat|tongue|lip|face) (swelling|swollen|closing|tight)",
            r"anaphyla",
            r"(hives|rash)[^.]{0,40}(trouble breathing|wheez|short(ness)? of breath)",
        ),
        "Use epinephrine auto-injector (IM, anterolateral thigh) immediately if available. " + CALL_EMS,
    ),
    RedFlagRule(
        "airway_breathing",
        "Severe respiratory distress",
        "emergency",
        (
            r"can'?t breathe|unable to breathe|struggling to breathe|gasping",
            r"(blue|cyanotic|grey) (lips|face|skin)|cyanosis",
            r"(spo2|o2 sat(uration)?|oxygen saturation|sats?)[^0-9]{0,15}([0-8][0-9])\s*%",
            r"can'?t (speak|talk) (in )?full sentences",
        ),
        CALL_EMS,
    ),
    RedFlagRule(
        "sepsis",
        "Possible sepsis / septic shock",
        "emergency",
        (
            r"sepsis|septic",
            r"(fever|rigors|chills)[^.]{0,60}(confus|low blood pressure|hypotens|mottled|not passing urine|racing heart)",
            r"(confus|hypotens|mottled)[^.]{0,60}(fever|infection)",
        ),
        CALL_EMS + " Sepsis is time-critical: cultures, lactate, and antibiotics within 1 hour.",
    ),
    RedFlagRule(
        "meningitis",
        "Possible meningitis / meningococcal disease",
        "emergency",
        (
            r"(stiff neck|neck stiffness)[^.]{0,80}(fever|headache|photophobia|rash)",
            r"(fever|headache)[^.]{0,80}(stiff neck|neck stiffness)",
            r"non[- ]?blanching rash|petechial rash|purpuric rash|purpura",
        ),
        CALL_EMS + " Do not delay antibiotics for imaging or LP.",
    ),
    RedFlagRule(
        "suicide",
        "Suicide or self-harm risk",
        "emergency",
        (
            r"suicid",
            r"kill (myself|themselves|himself|herself)",
            r"end (my|their|his|her) life",
            r"(want|wants|wanted|going) to die",
            r"self[- ]harm|hurt(ing)? (myself|themselves)|cutting (myself|themselves)",
            r"overdos",
        ),
        "If in immediate danger call emergency services. US: call or text 988 (Suicide & Crisis Lifeline). "
        "UK/IE: Samaritans 116 123. Elsewhere: findahelpline.com. Stay with the person; remove means.",
    ),
    RedFlagRule(
        "bleeding",
        "Major hemorrhage",
        "emergency",
        (
            r"(vomiting|coughing up|throwing up) (blood|coffee[- ]grounds?)",
            r"hematemesis|hemoptysis (massive|large)",
            r"(black|tarry) stools?|melena",
            r"(won'?t|doesn'?t|will not|can'?t) stop bleeding|uncontrolled bleeding|bleeding heavily",
        ),
        CALL_EMS + " Apply direct pressure to external bleeding.",
    ),
    RedFlagRule(
        "pregnancy",
        "Obstetric emergency",
        "emergency",
        (
            r"(pregnan)[^.]{0,80}(bleeding|severe (abdominal|pelvic) pain|seizure|severe headache|vision changes|fluid leak|decreased (fetal )?movement)",
            r"(bleeding|severe (abdominal|pelvic) pain|seizure|severe headache)[^.]{0,80}(pregnan|weeks pregnant)",
            r"ectopic",
            r"eclampsia|preeclampsia",
        ),
        CALL_EMS + " Possible ectopic pregnancy, abruption, or (pre)eclampsia.",
    ),
    RedFlagRule(
        "neonatal_fever",
        "Fever in an infant under 3 months",
        "emergency",
        (
            r"(newborn|neonat|\b[0-9]{1,2}[- ](day|week)[- ]old|\b[0-2][- ]month[- ]old|infant)[^.]{0,80}(fever|temperature|38)",
            r"(fever|temperature of 38)[^.]{0,80}(newborn|neonat|\b[0-9]{1,2}[- ](day|week)[- ]old|\b[0-2][- ]month[- ]old)",
        ),
        "Any fever >=38.0°C (100.4°F) in an infant <3 months (90 days) needs same-hour emergency evaluation.",
    ),
    RedFlagRule(
        "peds_respiratory_distress",
        "Respiratory distress in a child",
        "emergency",
        (
            r"grunting|head bobbing|nasal flaring|tracheal tug",
            r"(chest|rib|ribs)[^.]{0,20}(sucking in|pulling in|caving in)|(severe|deep) retractions",
            r"(stridor|drooling)[^.]{0,60}(at rest|can'?t swallow|tripod)",
        ),
        CALL_EMS + " Keep the child calm and upright in a caregiver's arms; do not examine the throat if drooling/stridor.",
    ),
    RedFlagRule(
        "sick_infant",
        "Seriously ill infant or young child",
        "emergency",
        (
            r"(baby|infant|newborn|toddler|child|son|daughter)[^.]{0,80}(floppy|limp|unrousable|won'?t wake|hard to wake|lethargic|inconsolable|high[- ]pitched cry|bulging (soft spot|fontanelle))",
            r"bulging (soft spot|fontanelle)",
            r"(baby|infant|newborn)[^.]{0,60}(not feeding|refusing (all )?feeds|stopped feeding|turned blue|stopped breathing|pause in breathing)",
        ),
        CALL_EMS + " Lethargy, poor feeding, or color change in an infant can be sepsis, meningitis, or a cardiac problem.",
    ),
    RedFlagRule(
        "bilious_vomiting",
        "Bilious (green) vomiting or possible intussusception",
        "emergency",
        (r"(green|bilious|bile)[^.]{0,20}vomit|vomit(ing)?[^.]{0,20}(green|bile)", r"currant[- ]jelly", r"intussuscep"),
        "Emergency surgical evaluation: malrotation with volvulus or intussusception until proven otherwise.",
    ),
    RedFlagRule(
        "ingestion",
        "Toxic or dangerous ingestion",
        "emergency",
        (
            r"(swallowed|ate|ingested|chewed)[^.]{0,40}(button battery|coin battery|magnets?|detergent pod|laundry pod|bleach|drain cleaner|pills?|tablets?|medicine|medication|vape liquid|nicotine|gummies|edibles?|lamp oil|antifreeze)",
            r"button battery",
            r"poison(ed|ing)",
        ),
        "US Poison Control 1-800-222-1222 (or local poison center) now; call emergency services if symptomatic. "
        "Button batteries and multiple magnets need emergency removal even if the child looks well. Do not induce vomiting.",
    ),
    RedFlagRule(
        "peds_dehydration",
        "Possible significant dehydration in a child",
        "urgent",
        (
            r"(no|not had a|hasn'?t had a) wet (diaper|nappy)",
            r"(no|not) (peed|urinated)[^.]{0,30}([8-9]|1[0-9]|twelve|eight) hours",
            r"sunken (soft spot|fontanelle|eyes)",
            r"no tears when crying",
        ),
        "Same-day evaluation; emergency care if lethargic, breathing fast, cold hands/feet, or unable to keep any fluids down.",
        negatable=False,
    ),
    RedFlagRule(
        "kawasaki_prolonged_fever",
        "Prolonged fever in a child (Kawasaki disease / MIS-C to exclude)",
        "urgent",
        (
            r"fever[^.]{0,30}(for )?(5|6|7|8|9|10|five|six|seven|eight|nine|ten|over a) (days|week)",
            r"(red|cracked|strawberry) (lips|tongue)[^.]{0,80}(fever|rash)|kawasaki|mis-c",
        ),
        "Same-day pediatric evaluation: Kawasaki disease needs IVIG within 10 days of fever onset to prevent coronary aneurysms.",
    ),
    RedFlagRule(
        "neonatal_jaundice",
        "Jaundice in a newborn",
        "urgent",
        (r"(newborn|baby|infant|neonat)[^.]{0,60}(yellow|jaundice)", r"(yellow|jaundice)[^.]{0,60}(newborn|baby|infant)"),
        "Same-day bilirubin check; EMERGENCY if within the first 24 hours of life, poor feeding, lethargy, arching, or high-pitched cry.",
    ),
    RedFlagRule(
        "seizure_loc",
        "Seizure, unresponsiveness, or acute confusion",
        "emergency",
        (r"seizur|convuls|fitting", r"unresponsive|unconscious|passed out and (won'?t|not) wak", r"(sudden|acute) confusion"),
        CALL_EMS + " Seizure >5 minutes is status epilepticus.",
    ),
    RedFlagRule(
        "dka",
        "Possible diabetic ketoacidosis / hyperglycemic crisis",
        "emergency",
        (
            r"(diabet|type 1)[^.]{0,80}(vomiting|fruity breath|kussmaul|deep rapid breathing|ketones)",
            r"\bdka\b|ketoacidosis",
            r"(blood )?(sugar|glucose)[^0-9]{0,15}([5-9][0-9]{2})",
        ),
        CALL_EMS + " Needs blood gas, ketones, electrolytes; IV fluids and insulin protocol.",
    ),
    RedFlagRule(
        "acute_abdomen",
        "Possible surgical abdomen",
        "urgent",
        (
            r"(severe|worst|excruciating)[^.]{0,30}(abdominal|stomach|belly) pain",
            r"rigid (abdomen|belly)|rebound tenderness",
            r"(testicular|scrotal) pain",
            r"pain (in|at) (the )?(right lower quadrant|rlq)",
        ),
        "Same-day emergency evaluation (appendicitis, torsion, perforation, ischemia, AAA must be excluded).",
    ),
    RedFlagRule(
        "pe_dvt",
        "Possible pulmonary embolism / DVT",
        "urgent",
        (
            r"(calf|leg) (swelling|swollen|pain)[^.]{0,80}(short(ness)? of breath|chest pain|after (a )?(flight|surgery))",
            r"(short(ness)? of breath|pleuritic)[^.]{0,80}(calf|leg) (swelling|swollen)",
            r"pulmonary embol|\bdvt\b",
        ),
        "Same-day evaluation; emergency services if breathless at rest, chest pain, or collapse.",
    ),
    RedFlagRule(
        "cauda_equina",
        "Possible cauda equina syndrome",
        "emergency",
        (
            r"saddle (numbness|anesthesia|anaesthesia)",
            r"(back pain)[^.]{0,80}(loss of bladder|incontinen|urinary retention|can'?t (pee|urinate))",
        ),
        "Emergency MRI and neurosurgical review.",
    ),
    RedFlagRule(
        "hypertensive_emergency",
        "Severe hypertension with possible end-organ damage",
        "urgent",
        (r"(bp|blood pressure)[^0-9]{0,15}(1[89][0-9]|2[0-9]{2})\s*/\s*([0-9]{2,3})",),
        "BP >=180/120: emergency care if headache, chest pain, dyspnea, neuro deficit, or vision change; otherwise prompt same-day review.",
    ),
    RedFlagRule(
        "child_abuse_or_violence",
        "Safeguarding concern",
        "urgent",
        (r"(being|was|got) (hit|beaten|abused|choked|strangled)", r"domestic (violence|abuse)", r"not safe at home"),
        "Ensure immediate safety. US: 911 / National DV Hotline 1-800-799-7233. Mandatory reporting may apply for children.",
    ),
)


def _mask_negations(text: str) -> str:
    """Blank out negated spans (same length, so offsets stay valid)."""
    return _NEGATION_SCOPE.sub(lambda m: " " * len(m.group(0)), text)


def screen(text: str) -> TriageResult:
    """Screen free text for red flags. Returns the highest urgency found."""
    hits: list[RedFlagHit] = []
    if not text:
        return TriageResult("routine", [], "No text provided.")
    masked = _mask_negations(text)
    for rule in RULES:
        for pat in rule.patterns:
            m = re.search(pat, masked if rule.negatable else text, re.IGNORECASE)
            if m:
                hits.append(RedFlagHit(rule.id, rule.condition, rule.urgency, text[m.start() : m.end()], rule.action))
                break
    urgency: Urgency = "routine"
    for h in hits:
        if _URGENCY_RANK[h.urgency] > _URGENCY_RANK[urgency]:
            urgency = h.urgency
    if urgency == "emergency":
        msg = "EMERGENCY RED FLAGS DETECTED — seek emergency care immediately. " + " | ".join(
            f"{h.condition}: {h.action}" for h in hits if h.urgency == "emergency"
        )
    elif urgency == "urgent":
        msg = "URGENT: same-day medical evaluation recommended. " + " | ".join(f"{h.condition}: {h.action}" for h in hits)
    else:
        msg = "No automated red flags detected. This screen is keyword-based and does not exclude serious illness."
    return TriageResult(urgency, hits, msg)
