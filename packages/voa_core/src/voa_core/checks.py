"""Named triage checks: reviewed Python a spec's rules can call (``check: <name>``).

The rule language (``voa_core.rules``) covers comparisons, ``in``, ``answered``
and ``mentions``. Clinical logic that is too subtle for it -- free-text scans with
negation and field attribution, the swelling heuristic, the patient-question
summary -- lives here instead, as functions with a name. Adding or changing one
is a code change with clinical review; using one in a spec is not.

The post-op checks wrap the pre-engine ``survey.triage`` code unchanged.

A check is called as ``fn(ctx, *args)`` and returns:

* falsy: no finding;
* ``True``: one finding with the rule's reason and fields;
* a :class:`Hit`: one finding; its ``text``/``fields`` override the rule's when set;
* a list of :class:`Hit`: one finding per hit.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .parsers import _negated_words

if TYPE_CHECKING:
    from .campaign import Campaign

#: Every raw reply, kept verbatim alongside the parsed answers (``answers["_raw"]``).
RAW_KEY = "_raw"
#: Questions the patient asked the care team: [{text, category, priority}].
PATIENT_QUESTIONS = "patient_questions"


@dataclass(frozen=True)
class Hit:
    text: str | None = None
    fields: tuple[str, ...] | None = None


@dataclass(frozen=True)
class CheckContext:
    campaign: Campaign
    answers: dict
    patient: dict


# --- Free text --------------------------------------------------------------------

# Ported from the voice agent's <escalation_criteria>. The vocabulary must
# match the dashboard's triage colouring so the two agree about what counts
# as severe.
SEVERE_LANGUAGE = re.compile(
    r"\b(terrible|awful|unbearable|excruciating|worst|severe|really bad|very bad)\b",
    re.I,
)
WANTS_HUMAN = re.compile(
    r"\b(nurse|doctor|physician|call me|speak (to|with) (someone|a )|"
    r"talk to (someone|a )|real person|human)\b",
    re.I,
)
WORRIED = re.compile(
    r"\b(worried|worry|scared|frightened|anxious|nervous|concerned|"
    r"don'?t understand|confused)\b",
    re.I,
)


def prose_fields(c: Campaign) -> tuple[str, ...]:
    """The answers that are the patient's own words: free-text questions without a
    named parser, in spec order (post-op: overall_feeling, pain_location,
    incision_description, swelling_description)."""
    return tuple(q.id for q in c.questions if q.kind == "text" and not q.parser)


def raw_answers(answers: dict) -> dict:
    """field -> exactly what the patient typed. Empty if not recorded."""
    raw = answers.get(RAW_KEY)
    if isinstance(raw, dict):
        return raw
    # Legacy shape: a flat list, with no field attribution. Still usable for
    # scanning prose, just not for pointing at a specific message.
    return {}


def free_text_blob(c: Campaign, answers: dict) -> str:
    """Everything the patient actually wrote, parsed fields included."""
    # Patient questions are NOT scanned here: "when will I see my doctor?" is a
    # question for the care team, not a request to speak to someone today.
    # patient_questions.classify() triages them instead.
    skip = set(c.open_question_ids)
    parts = [str(answers.get(f) or "") for f in prose_fields(c)]
    raw = answers.get(RAW_KEY)
    if isinstance(raw, dict):
        parts.extend(str(v) for k, v in raw.items() if k not in skip)
    elif isinstance(raw, (list, tuple)):
        parts.extend(str(x) for x in raw)
    return " ".join(parts)


def prose_field(c: Campaign, answers: dict, pattern: re.Pattern) -> str | None:
    """Which answer the matching prose actually came from.

    A request for a nurse can be typed into any answer, including a yes/no
    one where the parser throws the sentence away. Scanning the raw text
    per field is the only way to point at the message it was in.
    """
    skip = set(c.open_question_ids)
    for field, text in raw_answers(answers).items():
        if field not in skip and pattern.search(str(text)):
            return field
    for field in prose_fields(c):
        if pattern.search(str(answers.get(field) or "")):
            return field
    return None


def _prose_check(pattern: re.Pattern) -> Callable[[CheckContext], Hit | None]:
    def check(ctx: CheckContext) -> Hit | None:
        if not pattern.search(free_text_blob(ctx.campaign, ctx.answers)):
            return None
        field = prose_field(ctx.campaign, ctx.answers, pattern)
        return Hit(fields=(field,) if field else None)

    return check


# --- Swelling ---------------------------------------------------------------------

# Swelling in one leg, or swelling that is getting worse or comes with pain,
# redness, warmth or breathlessness, can mean a clot: same day. (Calf swelling
# WITH pain is also a red flag the voice agent acts on as an emergency.)
SWELLING_URGENT = re.compile(
    r"\b(one (leg|foot|side|ankle|calf)|just (the |my )?(left|right)|only (the |my |on )?"
    r"(left|right|one)|(left|right) (leg|foot|side|ankle|calf|knee|shin)|single|"
    r"worse|worsening|getting bigger|bigger|increas|spreading|"
    r"pain|painful|hurts?|tender|red|warm|hot|short of breath|breath)",
    re.I,
)


def swelling_is_urgent(description: str) -> bool:
    """One leg, worsening, or painful/red/warm swelling: same day. A negated word
    ("not painful", "no redness") does not count. Shared with the nurse agent so
    its colouring cannot drift from this rule."""
    negated = _negated_words(description)
    for m in SWELLING_URGENT.finditer(description or ""):
        words = m.group(0).lower().split()
        # The pattern matches stems ("red", "pain"); the negated word is whole ("redness").
        if not any(n.startswith(w) for w in words for n in negated):
            return True
    return False


def _swelling_urgent(ctx: CheckContext, description_field: str) -> bool:
    return swelling_is_urgent(str(ctx.answers.get(description_field) or ""))


# --- Patient questions ------------------------------------------------------------


def valid_questions(answers: dict) -> list[dict]:
    from . import patient_questions

    qs = answers.get(PATIENT_QUESTIONS)
    return [q for q in qs if patient_questions.valid_entry(q)] if isinstance(qs, list) else []


def _questions_for_care_team(ctx: CheckContext, when: str) -> list[Hit]:
    """One finding per topic line ("questions asked (medication, ...)") at this
    priority: ``same_day`` or ``next_day`` (patient_questions.summary)."""
    from . import patient_questions as pq

    now, nxt = pq.summary(valid_questions(ctx.answers))
    return [Hit(text=t) for t in (now if when == "same_day" else nxt)]


def _questions_recorded(ctx: CheckContext) -> bool:
    return bool(valid_questions(ctx.answers))


#: name -> (function, the number of args it takes, what it does).
NAMED_CHECKS: dict[str, tuple[Callable[..., Any], int, str]] = {
    "severe_language": (
        _prose_check(SEVERE_LANGUAGE),
        0,
        "the patient's own words say the pain is severe (terrible, unbearable, worst...)",
    ),
    "asks_for_a_person": (
        _prose_check(WANTS_HUMAN),
        0,
        "the patient's own words ask for a nurse, a doctor or a call back",
    ),
    "expressed_worry": (
        _prose_check(WORRIED),
        0,
        "the patient's own words express worry or confusion",
    ),
    "swelling_is_urgent": (
        _swelling_urgent,
        1,
        "the swelling description (arg: its question id) says one side, worsening, or "
        "painful, red or warm, and not negated",
    ),
    "questions_for_care_team": (
        _questions_for_care_team,
        1,
        "the recorded patient questions at a priority (arg: same_day or next_day), "
        "one finding per topic line",
    ),
    "questions_recorded": (
        _questions_recorded,
        0,
        "at least one valid patient question was recorded",
    ),
}

#: Arguments a check accepts that are not question ids.
CHECK_ARG_VALUES: dict[str, frozenset[str]] = {
    "questions_for_care_team": frozenset({"same_day", "next_day"}),
}


def run_check(name: str, ctx: CheckContext, args: list[str] | tuple[str, ...] = ()) -> Any:
    fn, _n, _doc = NAMED_CHECKS[name]
    return fn(ctx, *args)
