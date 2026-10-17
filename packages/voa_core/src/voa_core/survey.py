"""The post-op questionnaire, by text -- plan, parsers, and triage.

Pure logic, no I/O, no clock, no model. Everything here is deterministic so
that a clinical decision can be pointed at a line of code, which is the whole
reason this path has no LLM in it.

Three things live here:

* **The plan** (:func:`build_plan`) -- which questions this patient gets, fixed
  before the first one is sent so ``(n/N)`` can never change mid-flow.
* **The parsers** -- what a handset actually sends, not what a form designer
  wishes it sent.
* **The triage** (:func:`triage`) -- ``<escalation_criteria>`` from
  the voice agent's instructions, ported from prose to code.

The filing endpoint validates ``survey_responses`` independently and rejects
several shapes that look reasonable here (notably ``pain_present="yes"``
without severity, location AND trend), so change the plan with that in mind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --- The plan -----------------------------------------------------------------

# Field keys match the voice schema exactly, because the report they produce
# has to be indistinguishable from a call's on the dashboard and in triage.
Q_OVERALL = "overall_feeling"
Q_PAIN = "pain_present"
Q_FEVER = "fever_reported"
Q_INCISION = "incision_concerns"
Q_SUGAR = "blood_sugar_status"

# Conditional follow-ups. NOT numbered: the patient was already told there are
# N questions, and these appear only for the people who trigger them. All four
# are REQUIRED by the proxy when their parent answer is "yes", so they are not
# optional extras -- without them the submission is rejected outright.
F_PAIN_SEVERITY = "pain_severity"
F_PAIN_LOCATION = "pain_location"
F_PAIN_TREND = "pain_trend"
F_INCISION_DESC = "incision_description"

_PAIN_FOLLOWUPS = (F_PAIN_SEVERITY, F_PAIN_LOCATION, F_PAIN_TREND)


def coerce_bool(value) -> bool:
    """Firestore round-trips booleans as strings often enough to matter.

    ``"false"`` is a non-empty string and therefore truthy, which would put a
    blood-sugar question and a blood-sugar field on a non-diabetic's report.
    The proxy has the same helper for the same reason.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def build_plan(patient_context: dict | None) -> list[str]:
    """The numbered questions this patient will be asked, in order."""
    plan = [Q_OVERALL, Q_PAIN, Q_FEVER, Q_INCISION]
    if coerce_bool((patient_context or {}).get("is_diabetic")):
        plan.append(Q_SUGAR)
    return plan


def followups_for(field: str, answer: str) -> tuple[str, ...]:
    """Unnumbered follow-ups triggered by an answer to a numbered question."""
    if field == Q_PAIN and answer == "yes":
        return _PAIN_FOLLOWUPS
    if field == Q_INCISION and answer == "yes":
        return (F_INCISION_DESC,)
    return ()


# --- Parsers ------------------------------------------------------------------

_YES = {
    "y",
    "yes",
    "yeah",
    "yep",
    "yup",
    "ya",
    "sure",
    "correct",
    "true",
    "affirmative",
    "i do",
    "i am",
    "some",
    "a little",
    "a bit",
    "a little bit",
    "kind of",
    "kinda",
    "slightly",
    "mild",
    "mildly",
    "somewhat",
}
_NO = {
    "n",
    "no",
    "nope",
    "nah",
    "none",
    "not really",
    "no pain",
    "negative",
    "false",
    "nothing",
    "no thanks",
    "all good",
    "im fine",
    "i'm fine",
}

# "a little" is a YES. The voice script is explicit: any hint of pain
# ("just a little bit", "not bad", "mild") is "yes". Treating a hedged yes as
# a no is the single easiest way to under-report pain in this whole feature.

_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

_TREND = {
    "better": "better",
    "b": "better",
    "improving": "better",
    "improved": "better",
    "same": "same",
    "s": "same",
    "unchanged": "same",
    "no change": "same",
    "worse": "worse",
    "w": "worse",
    "worsening": "worse",
    "getting worse": "worse",
}

# "range" / "ok" / "good" alone are NOT enough: "out of range" and "ok a bit
# high" both contain one, and reading either as normal under-triages a
# diabetic who just told us the opposite. High/low are checked first, and
# "out of range" is matched explicitly.
_SUGAR_IN_RANGE = {
    "in range",
    "in my usual range",
    "normal",
    "usual",
    "fine",
    "ok",
    "okay",
    "good",
    "steady",
    "stable",
}
_SUGAR_OUT = {"out of range", "out of my range", "outside my range", "not in range"}
_SUGAR_HIGH = {"high", "up", "elevated", "higher", "too high"}
_SUGAR_LOW = {"low", "down", "lower", "too low"}


def _norm(body: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (body or "").lower()).strip()


def parse_yes_no(body: str) -> str | None:
    """ "yes"/"no", or None if it cannot be read as either."""
    t = _norm(body)
    if not t:
        return None
    if t in _YES:
        return "yes"
    if t in _NO:
        return "no"
    # Fallback for a sentence rather than a bare word. Both sides are scanned
    # and an answer containing BOTH signals is treated as unclear rather than
    # resolved by scan order -- "no fever but some pain" is a real thing to
    # send, and a first-match-wins loop reads it as whichever list ran first.
    # On a clinical form, asking again beats guessing.
    negated = _negated_words(body)

    def _hits(vocab: set[str]) -> list[str]:
        return [
            p
            for p in vocab
            if p not in _YES_EXACT_ONLY
            and re.search(rf"(?<![a-z]){re.escape(p)}(?![a-z])", t)
            and p.split()[0] not in negated
        ]

    no_hits, yes_hits = _hits(_NO), _hits(_YES)
    if no_hits and yes_hits:
        return None
    if no_hits:
        return "no"
    if yes_hits:
        return "yes"
    return None


# "3/10" and "3 out of 10" are how people write a pain score, and the scale
# half must be removed BEFORE looking for numbers. Without this, normalising
# "0/10" to "0 10" and taking the maximum reports every routine score as a 10
# and escalates the entire cohort.
_OUT_OF_TEN = re.compile(r"\s*(?:/|\bout of\b|\bof\b)\s*10\b")

# Answers to "how bad is it" that mean zero. Exact matches only.
_NO_PAIN = frozenset({"no", "none", "no pain", "zero", "nothing", "not any", "n"})


def parse_scale(body: str) -> int | None:
    """A 0-10 pain score.

    Handles what people actually send: ``8``, ``eight``, ``3/10``,
    ``about a 4``, ``4-5``, ``4 or 5``, ``none``. A range takes the HIGHER
    end, matching the voice script -- under-reporting pain is the more
    dangerous error, and over-reporting only costs a phone call.
    """
    raw = (body or "").lower()
    t = _norm(_OUT_OF_TEN.sub(" ", raw))
    if not t:
        return None
    # "none" / "no pain" is a real answer to "how bad is it", and it means 0.
    # Deliberately an exact whitelist rather than parse_yes_no: "no idea"
    # also reads as a no, and it means the opposite of zero.
    if t in _NO_PAIN:
        return 0
    # Only short digit runs can be a 0-10 score. Checking the length BEFORE
    # int() matters: int() on a multi-thousand-digit string raises ValueError
    # (Python's int-string limit), and in a webhook that is a 500 the carrier
    # retries in a loop (review finding S-10).
    nums = [int(n) for n in re.findall(r"\d+", t) if len(n.lstrip("0")) <= 2 and int(n) <= 10]
    if nums:
        return max(nums)
    for word, val in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", t):
            return val
    return None


# "not worse" and "no better" invert what follows them. A plain substring
# scan reads both as the word itself, which is the wrong direction on the one
# field that escalates on its own.
#
# The negation reaches the next few words of the SAME clause: "not getting
# worse" negates "worse" (the first version negated only "getting", and
# "worse" escalated as urgent), while "no pain but getting worse" is two
# clauses and the second one stands. Clauses split on punctuation and "but",
# on the RAW text, because _norm strips the punctuation that marks them.
_NEGATORS = frozenset(
    {
        "not",
        "no",
        "never",
        "without",
        "isn",
        "aren",
        "wasn",
        "hasn",
        "don",
        "doesn",
        "didn",
        "cant",
        "cannot",
    }
)
_NEGATION_REACH = 3
_CLAUSE_SPLIT = re.compile(r"[,;.!?]|\bbut\b|\bhowever\b|\bthough\b")


def _negated_words(body: str) -> set[str]:
    out: set[str] = set()
    for clause in _CLAUSE_SPLIT.split((body or "").lower()):
        toks = _norm(clause).split()
        for i, tok in enumerate(toks):
            if tok in _NEGATORS:
                out.update(toks[i + 1 : i + 1 + _NEGATION_REACH])
    return out


# Entries that only mean "yes" as a WHOLE answer. Inside a sentence they are
# ordinary words: "not sure" and "I am not sure" both scanned as yes, which on
# the pain question fabricated a pain report and three follow-ups.
_YES_EXACT_ONLY = frozenset({"sure", "i am", "i do"})


def parse_trend(body: str) -> str | None:
    t = _norm(body)
    if not t:
        return None
    if t in _TREND:
        return _TREND[t]
    negated = _negated_words(body)
    for phrase in sorted(_TREND, key=len, reverse=True):
        if len(phrase) > 1 and phrase in t:
            if phrase in negated or any(w in negated for w in phrase.split()):
                # "not worse" says what it is NOT. Skip it: "not better,
                # worse" still has an answer in it, and a bare "not worse"
                # falls through to None, i.e. ask again rather than record
                # the opposite of what they said.
                continue
            return _TREND[phrase]
    return None


# A typed meter reading, in mg/dL (review finding S-10: "250" used to be
# unparseable and the question was re-asked). The thresholds are the
# conventional ones -- below 70 is hypoglycaemia, above 180 is above the usual
# target -- and the plausibility bounds are roughly what a home meter can
# display; anything outside them (or a decimal, which is likely mmol/L) is not
# read as a reading at all. Over-triaging is the chosen direction: an abnormal
# number wins over reassuring words ("fine, 250" is high), and abnormal words
# win over an in-range number ("130, a bit high for me" is high).
SUGAR_LOW_MGDL = 70
SUGAR_HIGH_MGDL = 180
_SUGAR_MIN_READING = 20
_SUGAR_MAX_READING = 600
_SUGAR_READING_RE = re.compile(r"(?<![\d.])(\d{2,3})(?![\d.]|\s*%)")


def _sugar_from_reading(body: str) -> str | None:
    """ "high"/"low"/"in_range" from typed numbers, None if there are none.

    Several readings resolve to the most abnormal one ("120 then 250" is
    high). A high AND a low ("65 this morning, 250 at night") returns
    "unclear": that patient needs a person, but recording one direction
    would misstate what they said, so the question is asked again.
    """
    values = {
        "low" if n < SUGAR_LOW_MGDL else "high" if n > SUGAR_HIGH_MGDL else "in_range"
        for n in (int(x) for x in _SUGAR_READING_RE.findall(body or ""))
        if _SUGAR_MIN_READING <= n <= _SUGAR_MAX_READING
    }
    if "high" in values and "low" in values:
        return "unclear"
    for value in ("high", "low", "in_range"):
        if value in values:
            return value
    return None


def _sugar_from_words(body: str) -> tuple[bool, str | None]:
    """(matched, value). matched=False means no sugar vocabulary at all."""
    t = _norm(body)
    if not t:
        return False, None
    negated = _negated_words(body)
    # "Out of range" says abnormal without saying which way. Unclear is the
    # honest answer -- re-asking beats recording "normal".
    for phrase in _SUGAR_OUT:
        if phrase in t:
            return True, None
    # High/low first: "ok a bit high" must not resolve on "ok".
    for vocab, value in (
        (_SUGAR_HIGH, "high"),
        (_SUGAR_LOW, "low"),
        (_SUGAR_IN_RANGE, "in_range"),
    ):
        for phrase in sorted(vocab, key=len, reverse=True):
            if phrase in t:
                if phrase in negated or any(w in negated for w in phrase.split()):
                    return True, None
                return True, value
    return False, None


def parse_sugar(body: str) -> str | None:
    """ "high"/"low"/"in_range", or None to ask again."""
    reading = _sugar_from_reading(body)
    if reading == "unclear":
        return None
    if reading in ("high", "low"):
        return reading
    matched, value = _sugar_from_words(body)
    if matched:
        return value
    return reading  # "in_range" from a number alone, or None


MAX_FREE_TEXT = 500

# How many times to re-ask a field the proxy requires as a typed value before
# giving up and filing a partial. Looping forever strands the patient.
MAX_STRICT_RETRIES = 3


def parse_free_text(body: str) -> str | None:
    t = (body or "").strip()
    return t[:MAX_FREE_TEXT] if t else None


_PARSERS = {
    Q_OVERALL: parse_free_text,
    Q_PAIN: parse_yes_no,
    Q_FEVER: parse_yes_no,
    Q_INCISION: parse_yes_no,
    Q_SUGAR: parse_sugar,
    F_PAIN_SEVERITY: parse_scale,
    F_PAIN_LOCATION: parse_free_text,
    F_PAIN_TREND: parse_trend,
    F_INCISION_DESC: parse_free_text,
}

# Fields the proxy requires as a typed value once their parent is "yes", so
# "record unknown and move on" is not available for them -- an unparseable
# answer has to stay on the question or the report cannot be filed at all.
STRICT_FIELDS = frozenset({F_PAIN_SEVERITY, F_PAIN_LOCATION, F_PAIN_TREND, F_INCISION_DESC})

# Fields whose schema has a real "unknown" value.
UNKNOWNABLE = frozenset({Q_FEVER, Q_INCISION, Q_SUGAR, Q_PAIN})


def parse_answer(field: str, body: str):
    return _PARSERS[field](body)


# --- Triage -------------------------------------------------------------------

# Ported from the voice agent's <escalation_criteria>. The vocabulary must
# match the dashboard's triage colouring so the two agree about what counts
# as severe.
_SEVERE_LANGUAGE = re.compile(
    r"\b(terrible|awful|unbearable|excruciating|worst|severe|really bad|very bad)\b",
    re.I,
)
_WANTS_HUMAN = re.compile(
    r"\b(nurse|doctor|physician|call me|speak (to|with) (someone|a )|"
    r"talk to (someone|a )|real person|human)\b",
    re.I,
)
_WORRIED = re.compile(
    r"\b(worried|worry|scared|frightened|anxious|nervous|concerned|"
    r"don'?t understand|confused)\b",
    re.I,
)

FOLLOW_UP_EMERGENCY = "emergency"
FOLLOW_UP_URGENT = "urgent_same_day"
FOLLOW_UP_ROUTINE = "routine_next_business_day"
FOLLOW_UP_INFO = "informational"
FOLLOW_UP_NONE = "none"


@dataclass(frozen=True)
class Triage:
    follow_up: str
    reason: str
    # The answer fields that caused the escalation. Computed here rather than
    # re-derived in the dashboard: which answer made a nurse necessary is a
    # clinical judgement, and having two implementations of it -- one that
    # decides and one that colours the screen -- is how they drift apart.
    triggers: tuple[str, ...] = ()


# Every raw reply, kept verbatim alongside the parsed answers. A patient who
# writes "No, but I'm really scared, please have a doctor call me" to a yes/no
# question has their whole sentence collapsed to "no" by the parser -- so
# scanning only the parsed answers loses the request for a human entirely and
# files them as needing nothing.
RAW_KEY = "_raw"


def raw_answers(answers: dict) -> dict:
    """field -> exactly what the patient typed. Empty if not recorded."""
    raw = answers.get(RAW_KEY)
    if isinstance(raw, dict):
        return raw
    # Legacy shape: a flat list, with no field attribution. Still usable for
    # scanning prose, just not for pointing at a specific message.
    return {}


def _free_text_blob(answers: dict) -> str:
    """Everything the patient actually wrote, parsed fields included."""
    parts = [str(answers.get(f) or "") for f in (Q_OVERALL, F_PAIN_LOCATION, F_INCISION_DESC)]
    raw = answers.get(RAW_KEY)
    if isinstance(raw, dict):
        parts.extend(str(v) for v in raw.values())
    elif isinstance(raw, (list, tuple)):
        parts.extend(str(x) for x in raw)
    return " ".join(parts)


def _prose_field(answers: dict, pattern: re.Pattern) -> str | None:
    """Which answer the matching prose actually came from.

    A request for a nurse can be typed into any answer, including a yes/no
    one where the parser throws the sentence away. Scanning the raw text
    per field is the only way to point at the message it was in.
    """
    for field, text in raw_answers(answers).items():
        if pattern.search(str(text)):
            return field
    for field in (Q_OVERALL, F_PAIN_LOCATION, F_INCISION_DESC):
        if pattern.search(str(answers.get(field) or "")):
            return field
    return None


def triage(answers: dict) -> Triage:
    """Decide follow_up and a reason, from the answers alone.

    ``follow_up_reason`` may reference ONLY what the patient reported in this
    conversation -- never the procedure, the date, the DOB, or diabetic
    status. That is an explicit rule in <escalation_criteria> and this is the
    easiest place in the codebase to break it.
    """
    prose = _free_text_blob(answers)
    sev = answers.get(F_PAIN_SEVERITY)
    sev = sev if isinstance(sev, int) else None
    urgent: list[str] = []
    triggers: list[str] = []

    if sev is not None and sev >= 8:
        urgent.append(f"pain {sev}/10")
        triggers.append(F_PAIN_SEVERITY)
    if answers.get(F_PAIN_TREND) == "worse":
        urgent.append("pain worsening")
        triggers.append(F_PAIN_TREND)
    if answers.get(Q_FEVER) == "yes":
        urgent.append("fever reported")
        triggers.append(Q_FEVER)
    if answers.get(Q_INCISION) == "yes":
        # Voice escalates on a CONCERNING description, not on the bare yes.
        desc = str(answers.get(F_INCISION_DESC) or "")
        if desc.strip():
            urgent.append("incision concern described")
            triggers.append(F_INCISION_DESC)
        else:
            urgent.append("incision concern reported")
        triggers.append(Q_INCISION)
    if answers.get(Q_SUGAR) in ("high", "low"):
        urgent.append(f"blood sugar {answers[Q_SUGAR]}")
        triggers.append(Q_SUGAR)
    if answers.get(Q_PAIN) == "yes" and sev is None and _SEVERE_LANGUAGE.search(prose):
        urgent.append("severe pain described without a number")
        triggers.append(_prose_field(answers, _SEVERE_LANGUAGE) or Q_PAIN)
    if _WANTS_HUMAN.search(prose):
        urgent.append("asked to speak to someone")
        triggers.append(_prose_field(answers, _WANTS_HUMAN) or Q_OVERALL)
    if _WORRIED.search(prose):
        urgent.append("expressed worry")
        triggers.append(_prose_field(answers, _WORRIED) or Q_OVERALL)

    if urgent:
        return Triage(
            FOLLOW_UP_URGENT,
            "Patient reports " + ", ".join(urgent) + ".",
            tuple(dict.fromkeys(triggers)),
        )

    routine: list[str] = []
    if sev is not None and 4 <= sev <= 7:
        routine.append(f"pain {sev}/10")
        triggers.append(F_PAIN_SEVERITY)
    # Pain confirmed but no number obtained: voice's "uncertain but not
    # distressed". v1 of this design let it fall through to "none".
    if answers.get(Q_PAIN) == "yes" and sev is None:
        routine.append("pain reported, no severity given")
        triggers.append(Q_PAIN)
    unknowns = [f for f in (Q_PAIN, Q_FEVER, Q_INCISION, Q_SUGAR) if answers.get(f) == "unknown"]
    if unknowns:
        routine.append("some answers unclear")
        triggers.extend(unknowns)

    if routine:
        return Triage(
            FOLLOW_UP_ROUTINE,
            "Patient reports " + ", ".join(routine) + ".",
            tuple(dict.fromkeys(triggers)),
        )

    if sev is not None and 1 <= sev <= 3:
        return Triage(
            FOLLOW_UP_INFO,
            f"Patient reports mild pain {sev}/10.",
            (F_PAIN_SEVERITY,),
        )

    return Triage(FOLLOW_UP_NONE, "No concerning symptoms reported.")


def has_urgent_finding(answers: dict) -> bool:
    """Whether an abandoned survey is already worth filing.

    Losing a collected "fever: yes" because the patient stopped answering the
    next question is a clinical loss, not a demo compromise.
    """
    return triage(answers).follow_up in (FOLLOW_UP_EMERGENCY, FOLLOW_UP_URGENT)
