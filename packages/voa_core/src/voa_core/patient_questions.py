"""Questions a patient asks the care team, and how soon a nurse must answer them.

Deterministic on purpose, like the rest of triage: a priority can be pointed at a
line of code. The agent never answers these questions itself (no medical advice);
it repeats a short summary back, and this module decides who calls and when. With
the knowledge base on (``knowledge.py``), it may read the clinic's own approved
answer for an allowed, non-clinical category; such a question then needs no nurse
unless the patient says it didn't answer it (:func:`needs_follow_up`).

Rules (agreed 19 Oct 2026; clinical sign-off pending, see docs/HANDOFF.md):

* **Immediate** (``urgent_same_day``): anything about medication -- which, when,
  how much, a missed dose, stopping or starting, side effects, refills -- because
  the answer may affect their health today; and anything about a symptom or a
  complication (wound, bleeding, pain, fever, breathing, swelling, not able to eat,
  drink or pee, a drain or catheter).
* **Next business day** (``routine_next_business_day``): appointments, activity
  (driving, work, lifting, bathing), diet, paperwork, billing, supplies, the
  recovery timeline.
* **Unclear**: immediate. When a question fits nothing, err on the side of a nurse
  seeing it today.

An immediate match always wins over a routine one ("can I take ibuprofen before I
drive?" is a medication question).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PRIORITY_IMMEDIATE = "urgent_same_day"
PRIORITY_NEXT_DAY = "routine_next_business_day"
PRIORITIES = (PRIORITY_IMMEDIATE, PRIORITY_NEXT_DAY)

CAT_MEDICATION = "medication"
CAT_SYMPTOM = "symptom"
CAT_APPOINTMENT = "appointment"
CAT_ACTIVITY = "activity"
CAT_DIET = "diet"
CAT_ADMIN = "paperwork_billing"
CAT_SUPPLIES = "supplies"
CAT_RECOVERY = "recovery"
CAT_OTHER = "other"

#: Shown to nurses.
CATEGORY_LABELS = {
    CAT_MEDICATION: "Medication",
    CAT_SYMPTOM: "Symptom or wound",
    CAT_APPOINTMENT: "Appointment",
    CAT_ACTIVITY: "Activity",
    CAT_DIET: "Diet",
    CAT_ADMIN: "Paperwork or billing",
    CAT_SUPPLIES: "Supplies",
    CAT_RECOVERY: "Recovery timeline",
    CAT_OTHER: "Other",
}

#: A call records at most this many questions; the care team covers the rest.
MAX_QUESTIONS = 5
MAX_QUESTION_CHARS = 300


def _rx(*words: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(words) + r")", re.I)


# Every immediate rule is checked first. Within a group the first match names the
# category (supplies before activity: "walker" is not "walk").
_IMMEDIATE: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        CAT_MEDICATION,
        _rx(
            r"medic(?:ation|ine)s?\b",
            r"meds?\b",
            r"pills?\b",
            r"tablets?\b",
            r"capsules?\b",
            r"dos(?:e|es|age|ing)\b",
            r"prescri",
            r"refill(?:s|ed|ing)?\b",
            r"beta.?blockers?\b",
            r"blood pressure (?:pills?|meds?|medicine)",
            r"water pills?\b",
            r"diuretic",
            r"statins?\b",
            r"steroids?\b",
            r"prednisone",
            r"inhaler",
            r"injections?\b",
            r"shots?\b",
            r"vitamins?\b",
            r"supplements?\b",
            r"pharmac",
            r"drugs?\b",
            r"antibiotic",
            r"blood thinner",
            r"thinners?\b",
            r"pain ?killers?\b",
            r"pain (?:pills?|meds?|medicine)",
            r"insulin",
            r"aspirin",
            r"tylenol",
            r"acetaminophen",
            r"ibuprofen",
            r"advil",
            r"motrin",
            r"aleve",
            r"naproxen",
            r"oxycodone",
            r"hydrocodone",
            r"norco",
            r"percocet",
            r"tramadol",
            r"gabapentin",
            r"eliquis",
            r"xarelto",
            r"warfarin",
            r"coumadin",
            r"lovenox",
            r"metformin",
            r"laxative",
            r"stool softener",
            r"side effects?\b",
            r"missed (?:a |my )?(?:dose|pill)",
            r"(?:stop|start|keep) taking",
            r"how (?:much|many|often) (?:should|can|do) i take",
            r"(?:should|can|do) i (?:still )?take",
            r"when (?:should|do|can) i take",
            r"what (?:should|do|can) i take",
            r"which (?:one|ones) (?:should|do|can) i take",
        ),
    ),
    (
        CAT_SYMPTOM,
        _rx(
            r"pain",
            r"hurts?\b",
            r"hurting",
            r"ache",
            r"sore\b",
            r"bleed",
            r"blood\b",
            r"bloody",
            r"wound",
            r"incision",
            r"stitch",
            r"staple",
            r"sutures?\b",
            r"steri.?strips?",
            r"dressing",
            r"bandage",
            r"drain",
            r"catheter",
            r"ooz",
            r"pus\b",
            r"discharge",
            r"fever",
            r"temperature",
            r"chills",
            r"infect",
            r"red(?:ness)?\b",
            r"swell",
            r"swollen",
            r"breath",
            r"cough",
            r"chest",
            r"dizz",
            r"faint",
            r"nause",
            r"vomit",
            r"throw(?:ing)? up",
            r"diarrh",
            r"constipat",
            r"bowel",
            r"poop",
            r"urin",
            r"pee\b",
            r"peeing",
            r"rash",
            r"itch",
            r"numb",
            r"tingl",
            r"(?:can'?t|cannot|unable to|not able to) (?:eat|drink|keep|sleep|walk|pee|urinate)",
            r"not eating",
            r"not drinking",
        ),
    ),
)

_NEXT_DAY: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        CAT_APPOINTMENT,
        _rx(
            r"appointment",
            r"appt\b",
            r"follow.?up (?:visit|appointment)",
            r"visit",
            r"schedul",
            r"reschedul",
            r"see (?:the |my )?(?:doctor|surgeon|nurse)",
            r"clinic",
            r"check.?up",
        ),
    ),
    (
        CAT_SUPPLIES,
        _rx(
            r"supplies",
            r"equipment",
            r"walker",
            r"crutch",
            r"cane\b",
            r"wheelchair",
            r"brace",
            r"sling",
            r"compression",
            r"ice (?:pack|machine)",
        ),
    ),
    (
        CAT_ACTIVITY,
        _rx(
            r"driv",
            r"car\b",
            r"work\b",
            r"job\b",
            r"lift",
            r"exercis",
            r"walk",
            r"stairs",
            r"shower",
            r"bath",
            r"swim",
            r"sex",
            r"travel",
            r"fly\b",
            r"flying",
            r"gym",
            r"sport",
            r"golf",
            r"bend",
        ),
    ),
    (CAT_DIET, _rx(r"eat\b", r"eating", r"food", r"diet", r"alcohol", r"drink alcohol", r"coffee")),
    (
        CAT_ADMIN,
        _rx(
            r"paperwork",
            r"forms?\b",
            r"note for",
            r"work note",
            r"doctor'?s note",
            r"fmla",
            r"disability",
            r"insurance",
            r"bill",
            r"cost",
            r"pay\b",
            r"payment",
            r"records?\b",
        ),
    ),
    (
        CAT_RECOVERY,
        _rx(
            r"how long",
            r"recover",
            r"heal",
            r"back to normal",
            r"when (?:can|will) i",
            r"normal\b",
        ),
    ),
)


@dataclass(frozen=True)
class Classified:
    category: str
    priority: str


def classify(text: str) -> Classified:
    """Category and follow-up priority for one patient question."""
    t = (text or "").strip()
    for category, pattern in _IMMEDIATE:
        if pattern.search(t):
            return Classified(category, PRIORITY_IMMEDIATE)
    for category, pattern in _NEXT_DAY:
        if pattern.search(t):
            return Classified(category, PRIORITY_NEXT_DAY)
    return Classified(CAT_OTHER, PRIORITY_IMMEDIATE)


def record(text: str) -> dict[str, str]:
    """The stored form of one question: what the patient asked, and its triage."""
    clean = " ".join((text or "").split())[:MAX_QUESTION_CHARS]
    c = classify(clean)
    return {"text": clean, "category": c.category, "priority": c.priority}


#: Knowledge base (docs/KNOWLEDGE-PLAN.md): a question the agent answered from the
#: clinic's approved Q&A, or one left for a nurse. Optional on an entry: entries
#: recorded before the knowledge base (or with it off) have no outcome.
OUTCOME_ANSWERED = "answered"
OUTCOME_DEFERRED = "deferred"
OUTCOMES = (OUTCOME_ANSWERED, OUTCOME_DEFERRED)
MAX_ANSWER_CHARS = 600


def _valid_optional(entry: dict) -> bool:
    if "outcome" in entry and entry["outcome"] not in OUTCOMES:
        return False
    if entry.get("item_id") is not None and not (
        isinstance(entry["item_id"], str) and 0 < len(entry["item_id"]) <= 64
    ):
        return False
    v = entry.get("item_version")
    if v is not None and not (isinstance(v, int) and not isinstance(v, bool) and v >= 1):
        return False
    a = entry.get("answer")
    if a is not None and not (isinstance(a, str) and len(a) <= MAX_ANSWER_CHARS):
        return False
    return entry.get("satisfied") in (True, False, None)


def valid_entry(entry: object) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("text"), str)
        and 0 < len(entry["text"]) <= MAX_QUESTION_CHARS
        and entry.get("priority") in PRIORITIES
        and entry.get("category") in CATEGORY_LABELS
        and _valid_optional(entry)
    )


def needs_follow_up(entry: dict) -> bool:
    """False only for a question the agent answered from the clinic's Q&A that the
    patient did not say missed the point (``satisfied`` is not False)."""
    return not (entry.get("outcome") == OUTCOME_ANSWERED and entry.get("satisfied") is not False)


def summary(questions: list[dict]) -> tuple[list[str], list[str]]:
    """(immediate reasons, next-business-day reasons) for the triage reason line,
    e.g. "questions asked (medication, symptom or wound)".

    Names the topics, never the question text: the reason line is shown in places
    (banners, cards) where a short label reads better than a sentence. Questions
    answered from the clinic's Q&A (see :func:`needs_follow_up`) add no reason.
    """

    def line(priority: str) -> list[str]:
        topics = [
            CATEGORY_LABELS.get(q.get("category"), "Other").lower()
            for q in questions
            if q.get("priority") == priority and needs_follow_up(q)
        ]
        if not topics:
            return []
        noun = "question" if len(topics) == 1 else "questions"
        return [f"{noun} asked ({', '.join(dict.fromkeys(topics))})"]

    return line(PRIORITY_IMMEDIATE), line(PRIORITY_NEXT_DAY)
