"""Deterministic red-flag screening.

This runs on every consult *before* any model reasoning. It is deliberately
over-sensitive: a false alarm costs a sentence, a missed stroke costs a brain.
It does not replace clinical triage; it guarantees that obvious emergencies
are always surfaced, even if the LLM layer is unavailable or wrong.

Every rule change must keep tests/test_safety.py (a regression corpus of real
phrasings, including misses found in review) green.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Literal

Urgency = Literal["emergency", "urgent", "routine"]

_URGENCY_RANK = {"routine": 0, "urgent": 1, "emergency": 2}
MAX_SCREEN_CHARS = 50_000

# NegEx-style pre-negation. A cue ("no", "denies", "history of" ...) negates at most four
# following words and stops at clause boundaries or words that start a new finding
# ("and", "with", "now", "has", "presenting" ...). So "no history of heart disease and now
# has chest pain" masks only "history of heart disease", not the chest pain.
_CUES = (
    r"no|not|denies|denied|denying|without|negative for|absence of|free of|never had|ruled out|"
    r"history of|hx of|h/o|prior|previous|past history of|resolved"
)
_STOP = (
    r"and|but|with|w/|now|has|have|had|is|are|was|were|who|which|however|though|then|since|after|"
    r"presenting|presents|reports|complains|today|currently|yesterday|this"
)
_PRE_NEGATION = re.compile(
    rf"\b(?:{_CUES})\b((?:\s+(?!(?:{_STOP})\b)[\w'/%°.-]+){{1,4}})",
    re.IGNORECASE,
)
# Post-negation: "ectopic is not a concern", "stroke was ruled out".
_POST_NEGATION = re.compile(
    r"\b([\w'-]+(?:\s+[\w'-]+){0,2})\s+(?:is|was|were|are|has been|have been)\s+"
    r"(?:not a concern|ruled out|excluded|negative|unlikely|resolved)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RedFlagRule:
    id: str
    condition: str
    urgency: Urgency
    patterns: tuple[str, ...]
    action: str
    # False for findings that are themselves phrased negatively ("not breathing", "no wet diaper").
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
POISON = "US Poison Control 1-800-222-1222 (or your local poison center)."
GLUCOSE = r"(?:blood\s+)?(?:sugar|glucose|bg|bgl|cbg|fingerstick)"
INFANT_AGE = (
    r"(?:\b(?:[0-9]|[1-8][0-9])[- ]?days?[- ]old|\b(?:[0-9]|1[0-2])[- ]?(?:weeks?|wks?)[- ]old|"
    r"\b[0-2][- ]?(?:months?|mos?)[- ]old|newborn|neonat\w*)"
)
FEVER = r"(?:fever|febrile|temp\w*\s*(?:of\s*)?(?:3[89]|4[0-2]|10[0-6])|\b3[89](?:\.\d)?\s*°?\s*c\b|\b(?:100\.[4-9]|10[1-6](?:\.\d)?)\s*°?\s*f\b)"

RULES: tuple[RedFlagRule, ...] = (
    RedFlagRule(
        "arrest",
        "Possible cardiac or respiratory arrest",
        "emergency",
        (
            r"\b(?:is\s+not|isn'?t|not|wasn'?t|was\s+not|stopped|stops|no\s+longer)\s+breathing\b",
            r"\bno\s+(?:pulse|heartbeat)\b|\bpulseless\b|cardiac\s+arrest|\bin\s+arrest\b|\bdoing\s+cpr\b|\bstarted\s+cpr\b",
            r"\bcollapsed\b[^.]{0,40}\b(?:unresponsive|not\s+(?:responding|waking)|won'?t\s+wake)",
        ),
        CALL_EMS + " Start CPR (push hard and fast in the center of the chest) and use an AED if available; the dispatcher will coach you.",
        negatable=False,
    ),
    RedFlagRule(
        "acs",
        "Possible acute coronary syndrome",
        "emergency",
        (
            r"chest\s+(?:pain|pressure|tightness|heaviness|discomfort|hurts?)",
            r"crushing\s+(?:chest|pain)",
            r"pain\s+(?:radiating|radiates|spreading)\s+(?:to|into)\s+(?:the\s+)?(?:left\s+)?(?:arm|jaw|neck|back)",
            r"heart\s+attack|\bstemi\b|\bnstemi\b|myocardial\s+infarction|\bacute\s+coronary\b",
        ),
        CALL_EMS + " Chew 325 mg aspirin if no allergy/bleeding risk and advised by the dispatcher. ECG within 10 minutes of arrival.",
    ),
    RedFlagRule(
        "stroke",
        "Possible stroke (BE-FAST)",
        "emergency",
        (
            r"(?:face|facial|smile)\s+(?:is\s+|was\s+|looks\s+)?(?:droop\w*|weak\w*|numb\w*|uneven|lopsided)",
            r"(?:sudden(?:ly)?|acute(?:ly)?)\b[^.]{0,40}\b(?:weak|numb|paraly[sz]\w*|limp|can'?t\s+(?:move|lift|feel))",
            r"\b(?:arm|leg|hand|side)\b[^.]{0,30}\bsuddenly\b[^.]{0,20}\b(?:weak|numb|limp|paraly)",
            r"(?:one[- ]sided|unilateral|left[- ]sided|right[- ]sided|\b[lr][- ]sided)\s+(?:weakness|numbness|paralysis)",
            r"\b(?:aphasia|hemiparesis|hemiplegia|facial\s+palsy)\b",
            r"can'?t\s+(?:move|lift|feel)\s+(?:my|his|her|their)\s+(?:right|left)\s+(?:arm|leg|hand|side|foot)",
            r"(?:slurred|garbled)\s+speech|trouble\s+(?:speaking|talking|finding\s+words)|can'?t\s+(?:speak|talk)\b(?!\s+in\s+full)",
            r"(?:sudden|acute)\s+(?:loss\s+of\s+)?(?:vision|blindness)|sudden\s+(?:double\s+vision|loss\s+of\s+balance)",
            r"\b(?:having|had)\s+(?:a\s+)?stroke\b|\bstroke\s+symptoms\b|\btia\b|mini[- ]stroke",
        ),
        CALL_EMS + " Note the last-known-well time: thrombolysis/thrombectomy are time-critical.",
    ),
    RedFlagRule(
        "thunderclap",
        "Thunderclap headache (subarachnoid hemorrhage until proven otherwise)",
        "emergency",
        (
            r"worst(?:[- ]ever)?\s+headache(?:\s+of\s+(?:my|their|his|her)\s+life)?", r"thunderclap",
            r"sudden(?:,)?\s+(?:severe|explosive|worst[- ]ever)\s+headache", r"headache\s+(?:that\s+)?peaked\s+(?:with)?in\s+(?:a|one|\d+)\s+(?:minute|second)",
        ),
        CALL_EMS + " Needs non-contrast CT head; LP if CT negative and >6 h.",
    ),
    RedFlagRule(
        "anaphylaxis",
        "Possible anaphylaxis",
        "emergency",
        (
            r"(?:throat|tongue|lips?|face|mouth)\s+(?:is\s+|are\s+|feels?\s+)?(?:swelling|swollen|closing|tight)",
            r"anaphyla",
            r"(?:hives|rash)[^.]{0,40}(?:trouble\s+breathing|wheez|short(?:ness)?\s+of\s+breath|can'?t\s+breathe)",
        ),
        "Use an epinephrine auto-injector (IM, outer thigh) immediately if available. " + CALL_EMS,
    ),
    RedFlagRule(
        "airway_breathing",
        "Severe respiratory distress",
        "emergency",
        (
            r"can'?t\s+breathe|cannot\s+breathe|can\s+not\s+breathe|unable\s+to\s+breathe|struggling\s+to\s+breathe|gasping|choking",
            r"(?:lips|face|skin|fingers)\s+(?:are\s+|is\s+|turned\s+|turning\s+|look\s+)?(?:blue|grey|gray)|cyanosis|cyanotic",
            r"(?:spo2|o2\s+sat\w*|oxygen\s+sat\w*|sats?)[^0-9]{0,15}(?:[0-8][0-9]|[0-9])\s*%",
            r"can'?t\s+(?:speak|talk)\s+(?:in\s+)?full\s+sentences|short(?:ness)?\s+of\s+breath\s+at\s+rest",
        ),
        CALL_EMS,
    ),
    RedFlagRule(
        "sepsis",
        "Possible sepsis / septic shock",
        "emergency",
        (
            r"\bsepsis\b|\bseptic\b",
            r"(?:fever|rigors|chills)[^.]{0,60}(?:confus|low\s+blood\s+pressure|hypotens|mottled|not\s+passing\s+urine|racing\s+heart|not\s+making\s+sense)",
            r"(?:confus|hypotens|mottled|not\s+making\s+sense)[^.]{0,60}(?:fever|infection|\b3[89](?:\.\d)?\b|\b10[1-5]\b)",
        ),
        CALL_EMS + " Sepsis is time-critical: cultures, lactate, and antibiotics within 1 hour.",
    ),
    RedFlagRule(
        "meningitis",
        "Possible meningitis / meningococcal disease",
        "emergency",
        (
            r"(?:stiff\s+neck|neck\s+stiffness)[^.]{0,80}(?:fever|headache|photophobia|rash)",
            r"(?:fever|headache)[^.]{0,80}(?:stiff\s+neck|neck\s+stiffness)",
            r"non[- ]?blanching\s+rash|petechial\s+rash|purpuric\s+rash|\bpurpura\b",
        ),
        CALL_EMS + " Do not delay antibiotics for imaging or LP.",
    ),
    RedFlagRule(
        "suicide",
        "Suicide or self-harm risk",
        "emergency",
        (
            r"suicid",
            r"kill\s+(?:myself|themselves|himself|herself)",
            r"end(?:ing)?\s+(?:my|their|his|her)\s+life|end\s+it\s+all|ending\s+it\s+all",
            r"(?:want|wants|wanted|going)\s+to\s+die",
            r"(?:don'?t|do\s+not|doesn'?t)\s+want\s+to\s+(?:live|be\s+alive|wake\s+up)|better\s+off\s+dead|no\s+reason\s+to\s+live",
            r"self[- ]harm|hurt(?:ing)?\s+(?:myself|themselves)|cutting\s+(?:myself|themselves)",
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
            r"(?:vomiting|coughing\s+up|throwing\s+up)\s+(?:blood|coffee[- ]grounds?)",
            r"hematemesis|massive\s+hemoptysis",
            r"(?:black|tarry)\s+stools?|\bmelena\b",
            r"(?:won'?t|doesn'?t|will\s+not|can'?t)\s+stop\s+bleeding|uncontrolled\s+bleeding|bleeding\s+heavily|heavy\s+bleeding",
        ),
        CALL_EMS + " Apply direct pressure to external bleeding.",
    ),
    RedFlagRule(
        "pregnancy",
        "Obstetric emergency",
        "emergency",
        (
            r"pregnan\w*[^.]{0,80}(?:bleeding|severe\s+(?:abdominal|pelvic|belly)\s+pain|seizure|severe\s+headache|vision\s+changes|fluid\s+leak|decreased\s+(?:fetal\s+)?movement)",
            r"(?:bleeding|severe\s+(?:abdominal|pelvic)\s+pain|seizure|severe\s+headache)[^.]{0,80}(?:pregnan|weeks\s+pregnant)",
            r"\bectopic\b",
            # Possible early pregnancy + pain/bleeding/lightheadedness = ectopic until proven otherwise.
            r"(?:missed\s+(?:a\s+|her\s+|my\s+)?period|period\s+(?:is\s+)?(?:late|overdue)|(?:last\s+menstrual\s+period|\blmp\b)[^.]{0,20}\d+\s+weeks|positive\s+pregnancy\s+test)"
            r"[^.]{0,120}(?:pelvic\s+pain|abdominal\s+pain|quadrant\s+pain|belly\s+pain|spotting|bleeding|light-?headed|dizz\w*|faint\w*)",
            r"\b(?:pre-?)?eclampsia\b",
        ),
        CALL_EMS + " Possible ectopic pregnancy, abruption, or (pre)eclampsia.",
    ),
    RedFlagRule(
        "neonatal_fever",
        "Fever in an infant under 3 months",
        "emergency",
        (rf"{INFANT_AGE}[^.]{{0,80}}{FEVER}", rf"{FEVER}[^.]{{0,80}}{INFANT_AGE}"),
        "Any fever >=38.0°C (100.4°F) in an infant <3 months (90 days) needs same-hour emergency evaluation.",
    ),
    RedFlagRule(
        "peds_respiratory_distress",
        "Respiratory distress in a child",
        "emergency",
        (
            r"grunting|head\s+bobbing|nasal\s+flaring|tracheal\s+tug",
            r"(?:chest|rib|ribs)[^.]{0,20}(?:sucking\s+in|pulling\s+in|caving\s+in)|(?:severe|deep)\s+retractions",
            r"(?:stridor|drooling)[^.]{0,60}(?:at\s+rest|can'?t\s+swallow|tripod)",
        ),
        CALL_EMS + " Keep the child calm and upright in a caregiver's arms; do not examine the throat if drooling/stridor.",
    ),
    RedFlagRule(
        "sick_infant",
        "Seriously ill infant or young child",
        "emergency",
        (
            r"(?:baby|infant|newborn|toddler|child|son|daughter)[^.]{0,80}(?:floppy|limp|unrousable|won'?t\s+wake|hard\s+to\s+wake|lethargic|inconsolable|high[- ]pitched\s+cry|bulging\s+(?:soft\s+spot|fontanelle))",
            r"bulging\s+(?:soft\s+spot|fontanelle)",
            r"(?:baby|infant|newborn)[^.]{0,60}(?:not\s+feeding|refusing\s+(?:all\s+)?feeds|stopped\s+feeding|turned\s+blue|stopped\s+breathing|pause\s+in\s+breathing)",
        ),
        CALL_EMS + " Lethargy, poor feeding, or color change in an infant can be sepsis, meningitis, or a cardiac problem.",
        negatable=False,
    ),
    RedFlagRule(
        "bilious_vomiting",
        "Bilious (green) vomiting or possible intussusception",
        "emergency",
        (r"(?:green|bilious|bile)[^.]{0,20}vomit|vomit(?:ing)?[^.]{0,20}(?:green|bile)", r"currant[- ]jelly", r"intussuscep"),
        "Emergency surgical evaluation: malrotation with volvulus or intussusception until proven otherwise.",
    ),
    RedFlagRule(
        "ingestion",
        "Toxic or dangerous ingestion / overdose",
        "emergency",
        (
            r"(?:swallowed|ate|ingested|chewed|drank|drunk)[^.]{0,30}(?:button\s+battery|coin\s+battery|\bbattery\b|batteries|magnets?|detergent\s+pod|laundry\s+pod|bleach|drain\s+cleaner|antifreeze|lamp\s+oil|vape\s+liquid|e-?liquid|nicotine|weed\s+gummies|edibles?|rat\s+poison|pesticide)",
            r"(?:took|swallowed|ate|ingested|taken)\s+(?:a\s+|an\s+)?(?:\d{2,}|bunch\s+of|handful\s+of|whole\s+bottle|bottle\s+of|all\s+(?:of\s+)?(?:my|her|his|the|their))\b[^.]{0,30}(?:pills?|tablets?|tylenol|acetaminophen|paracetamol|ibuprofen|advil|meds|medications?|capsules?)",
            r"(?:got\s+into|into\s+the)[^.]{0,20}(?:pills|medicine\s+cabinet|grandma'?s\s+(?:pills|medicine))",
            r"button\s+battery|\bpoison(?:ed|ing)\b",
        ),
        POISON + " Call emergency services if there are any symptoms. Button batteries and multiple magnets need emergency "
        "removal even if the child looks well. Do not induce vomiting.",
    ),
    RedFlagRule(
        "head_injury",
        "Head injury with warning signs",
        "emergency",
        (
            r"(?:hit|bumped|struck|banged)\s+(?:his|her|my|their|the)\s+head[^.]{0,80}(?:confus\w*|vomit\w*|unconscious|passed\s+out|drowsy|sleepy|seizure|blood\s+thinner|not\s+acting\s+normal)",
            r"head\s+(?:injury|trauma)[^.]{0,60}(?:confus\w*|vomit\w*|unconscious|loss\s+of\s+consciousness|blood\s+thinner|anticoagula)",
            r"(?:fell|fall(?:en)?|hit|struck|collision|crash|accident|tackled)\b[^.]{0,80}(?:loss\s+of\s+consciousness|knocked\s+(?:out|unconscious)|\bloc\b)",
        ),
        CALL_EMS + " Head injury with confusion, vomiting, loss of consciousness, or on blood thinners needs urgent CT.",
    ),
    RedFlagRule(
        "seizure_loc",
        "Seizure",
        "emergency",
        (r"seizur\w*|convuls\w*|\bfitting\b",),
        CALL_EMS + " Seizure >5 minutes is status epilepticus.",
    ),
    RedFlagRule(
        "altered_mental_status",
        "Unresponsiveness or acute confusion",
        "emergency",
        (
            r"unresponsive|unconscious|(?:isn'?t|not|stopped)\s+responding\b|non-?responsive|won'?t\s+wake\s+up|can'?t\s+(?:be\s+)?wake?n?\b|unarousable",
            r"(?:sudden|acute)\s+confusion|\b(?:now|suddenly|newly|acutely)\s+confused\b|not\s+making\s+sense",
            r"\bnew\s+(?:onset\s+)?(?:confusion|disorientation)|(?:increasing|worsening)\s+confusion|\bdelirium\b|\bdelirious\b",
        ),
        CALL_EMS + " New confusion or unresponsiveness can be stroke, sepsis, low blood sugar, overdose or head injury.",
        negatable=False,
    ),
    RedFlagRule(
        "hard_to_wake",
        "Reduced level of consciousness",
        "emergency",
        (r"(?:hard|difficult)\s+to\s+(?:a)?wake|(?:very|extremely|unusually)\s+(?:sleepy|drowsy)\s+and\s+(?:hard|difficult)",),
        CALL_EMS + " A child or adult who is hard to wake needs emergency assessment (dehydration, sepsis, hypoglycemia, head injury, toxins).",
    ),
    RedFlagRule(
        "hypoglycemia",
        "Severe hypoglycemia",
        "emergency",
        (
            rf"{GLUCOSE}[^0-9.]{{0,15}}\b(?:[1-4][0-9]|5[0-3])\b(?!\s*(?:mmol|%))",
            r"low\s+blood\s+sugar[^.]{0,60}(?:confus\w*|unconscious|seizure|not\s+responding|passed\s+out|can'?t\s+swallow)",
        ),
        "Give 15-20 g fast sugar if able to swallow safely; glucagon if not. " + CALL_EMS + " if unresponsive or not improving.",
    ),
    RedFlagRule(
        "dka",
        "Possible diabetic ketoacidosis / hyperglycemic crisis",
        "emergency",
        (
            r"(?:diabet\w*|type\s+1)[^.]{0,80}(?:vomiting|fruity\s+breath|kussmaul|deep\s+rapid\s+breathing|(?:high|large|moderate|positive)\s+ketones|ketones\s+(?:are\s+)?(?:high|large|positive))",
            r"\bdka\b|ketoacidosis|\bhhs\b|hyperosmolar",
            rf"{GLUCOSE}[^0-9.]{{0,15}}\b(?:[6-9][0-9]{{2}}|[1-9][0-9]{{3}})\b(?!\s*(?:mmol|%))",
        ),
        CALL_EMS + " Needs blood gas, ketones, electrolytes; IV fluids and insulin protocol.",
    ),
    RedFlagRule(
        "hyperglycemia",
        "Severe hyperglycemia",
        "urgent",
        (rf"{GLUCOSE}[^0-9.]{{0,15}}\b(?:4[0-9]{{2}}|5[0-9]{{2}})\b(?!\s*(?:mmol|%))",),
        "Same-day evaluation: check ketones; emergency care if vomiting, abdominal pain, deep breathing, or confusion.",
    ),
    RedFlagRule(
        "hypoxemia",
        "Low oxygen saturation",
        "urgent",
        (r"(?:spo2|o2\s+sat\w*|oxygen\s+sat\w*|sats?)[^0-9]{0,15}(?:9[01])\s*%",),
        "Same-day evaluation; emergency care if short of breath, confused, or saturation keeps falling.",
    ),
    RedFlagRule(
        "low_glucose",
        "Hypoglycemia",
        "urgent",
        (rf"{GLUCOSE}[^0-9.]{{0,15}}\b(?:5[4-9]|6[0-9])\b(?!\s*(?:mmol|%))",),
        "Take 15-20 g of fast sugar now and recheck in 15 minutes; review diabetes medicines with the clinician today.",
    ),
    RedFlagRule(
        "torsion",
        "Possible testicular or ovarian torsion",
        "emergency",
        (
            r"(?:sudden|severe|acute)[^.]{0,30}(?:testic\w*|scrot\w*)\s*(?:pain)?",
            r"(?:testic\w*|testes|scrot\w*|balls?)\s+(?:hurts?|pain|is\s+swollen|swelling)[^.]{0,40}(?:sudden|severe|swollen|vomit)",
            r"\btorsion\b",
        ),
        CALL_EMS + " Torsion needs surgery within hours to save the testis/ovary.",
    ),
    RedFlagRule(
        "acute_abdomen",
        "Possible surgical abdomen",
        "urgent",
        (
            r"(?:severe|worst|excruciating)[^.]{0,30}(?:abdominal|stomach|belly)\s+pain",
            r"rigid\s+(?:abdomen|belly)|rebound\s+tenderness",
            r"pain\s+(?:in|at)\s+(?:the\s+)?(?:right\s+lower\s+quadrant|rlq)",
            r"pain[^.]{0,40}(?:moving|moved|migrat\w*|shift\w*)\s+(?:to|into)\s+(?:the\s+)?(?:right\s+lower|rlq|lower\s+right)",
            r"(?:right\s+lower\s+quadrant|\brlq\b)\s+(?:pain|tenderness)|\bappendicitis\b",
        ),
        "Same-day emergency evaluation (appendicitis, perforation, ischemia, AAA must be excluded).",
    ),
    RedFlagRule(
        "syncope",
        "Syncope (fainting)",
        "urgent",
        (r"\bfaint(?:ed|ing)\b|passed\s+out|\bsyncop\w*|blacked\s+out",),
        "Same-day evaluation; emergency care if with chest pain, palpitations, exertion, breathlessness, or injury.",
    ),
    RedFlagRule(
        "pe_dvt",
        "Possible pulmonary embolism / DVT",
        "urgent",
        (
            r"(?:calf|leg)\s+(?:swelling|swollen|pain)[^.]{0,80}(?:short(?:ness)?\s+of\s+breath|chest\s+pain|after\s+(?:a\s+)?(?:flight|surgery))",
            r"(?:short(?:ness)?\s+of\s+breath|pleuritic)[^.]{0,80}(?:calf|leg)\s+(?:swelling|swollen)",
            r"pulmonary\s+embol|\bdvt\b",
        ),
        "Same-day evaluation; emergency services if breathless at rest, chest pain, or collapse.",
    ),
    RedFlagRule(
        "cauda_equina",
        "Possible cauda equina syndrome",
        "emergency",
        (
            r"saddle\s+(?:numbness|anesthesia|anaesthesia)",
            r"back\s+pain[^.]{0,80}(?:loss\s+of\s+bladder|incontinen|urinary\s+retention|can'?t\s+(?:pee|urinate))",
        ),
        "Emergency MRI and neurosurgical review.",
    ),
    RedFlagRule(
        "hypertensive_emergency",
        "Severe hypertension with possible end-organ damage",
        "urgent",
        (r"(?:bp|blood\s+pressure)[^0-9]{0,15}(?:1[89][0-9]|2[0-9]{2})\s*/\s*[0-9]{2,3}",),
        "BP >=180/120: emergency care if headache, chest pain, dyspnea, neuro deficit, or vision change; otherwise prompt same-day review.",
    ),
    RedFlagRule(
        "peds_dehydration",
        "Possible significant dehydration",
        "urgent",
        (
            r"(?:no|not\s+had\s+a|hasn'?t\s+had\s+a)\s+wet\s+(?:diaper|nappy)",
            r"(?:no|not|hasn'?t|has\s+not|haven'?t|didn'?t)\s+(?:peed|urinated|wet)[^.]{0,30}(?:[8-9]|1[0-9]|2[0-4]|eight|twelve)\s+hours",
            r"sunken\s+(?:soft\s+spot|fontanelle|eyes)",
            r"no\s+tears\s+when\s+crying",
        ),
        "Same-day evaluation; emergency care if lethargic, breathing fast, cold hands/feet, or unable to keep any fluids down.",
        negatable=False,
    ),
    RedFlagRule(
        "kawasaki_prolonged_fever",
        "Prolonged fever in a child (Kawasaki disease / MIS-C to exclude)",
        "urgent",
        (
            r"fever[^.]{0,30}(?:for\s+)?(?:5|6|7|8|9|10|five|six|seven|eight|nine|ten|over\s+a)\s+(?:days|week)",
            r"(?:red|cracked|strawberry)\s+(?:lips|tongue)[^.]{0,80}(?:fever|rash)|kawasaki|mis-c",
        ),
        "Same-day pediatric evaluation: Kawasaki disease needs IVIG within 10 days of fever onset to prevent coronary aneurysms.",
    ),
    RedFlagRule(
        "neonatal_jaundice",
        "Jaundice in a newborn",
        "urgent",
        (r"(?:newborn|baby|infant|neonat)[^.]{0,60}(?:yellow|jaundice)", r"(?:yellow|jaundice)[^.]{0,60}(?:newborn|baby|infant)"),
        "Same-day bilirubin check; EMERGENCY if within the first 24 hours of life, poor feeding, lethargy, arching, or high-pitched cry.",
    ),
    RedFlagRule(
        "child_abuse_or_violence",
        "Safeguarding concern",
        "urgent",
        (r"(?:being|was|got)\s+(?:hit|beaten|abused|choked|strangled)", r"domestic\s+(?:violence|abuse)", r"not\s+safe\s+at\s+home"),
        "Ensure immediate safety. US: 911 / National DV Hotline 1-800-799-7233. Mandatory reporting may apply for children.",
        negatable=False,
    ),
)

_COMPILED = [(rule, [re.compile(p, re.IGNORECASE) for p in rule.patterns]) for rule in RULES]


def _mask(text: str) -> str:
    """Blank out negated or historical spans (same length, so offsets stay valid)."""

    def blank(m: re.Match[str]) -> str:
        return " " * len(m.group(0))

    # Post-negation first: in "ectopic is not a concern" the pre-negation cue "not" would
    # otherwise blank "a concern" and hide the pattern.
    text = _POST_NEGATION.sub(blank, text)
    return _PRE_NEGATION.sub(blank, text)


def screen(text: str) -> TriageResult:
    """Screen free text for red flags. Returns the highest urgency found."""
    hits: list[RedFlagHit] = []
    if not text:
        return TriageResult("routine", [], "No text provided.")
    text = text[:MAX_SCREEN_CHARS]
    masked = _mask(text)
    for rule, patterns in _COMPILED:
        for pat in patterns:
            m = pat.search(masked if rule.negatable else text)
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
