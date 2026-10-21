"""The questionnaire by text -- plan, follow-ups, and the triage entry point.

Pure logic, no clock, no model; the only I/O is reading a use-case spec once per
id (:func:`campaign_for`, cached). Everything here is deterministic so that a
clinical decision can be pointed at a line of code (or a rule in a spec), which
is the whole reason this path has no LLM in it.

* **The plan** (:func:`build_plan`) -- which questions this patient gets, fixed
  before the first one is sent so ``(n/N)`` can never change mid-flow. The order
  and the follow-ups come from the spec (questions' order, ``when`` and
  ``when_patient``).
* **The parsers** -- by question type, in ``voa_core.parsers`` (re-exported here).
* **The triage** (:func:`triage`) -- the spec's ``rules``, evaluated by
  ``voa_core.rules``; post-op's are the pre-engine ``<escalation_criteria>`` code
  rewritten as rules and named checks, with identical output.

The filing endpoint validates ``survey_responses`` against the same spec
(``reports.validate_report``), so a follow-up a report requires
(``required_with``) is strict here: an unreadable answer stays on the question.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import cache
from typing import Any

from . import parsers
from .campaign import DEFAULT_CAMPAIGN_ID, Campaign, Question, load_campaign, norm_answer
from .checks import RAW_KEY, raw_answers, swelling_is_urgent  # noqa: F401 (re-exported)
from .parsers import (  # noqa: F401 (re-exported: the pre-engine names)
    _NO,
    _YES,
    MAX_FREE_TEXT,
    SUGAR_HIGH_MGDL,
    SUGAR_LOW_MGDL,
    _negated_words,
    _norm,
    parse_free_text,
    parse_scale,
    parse_sugar,
    parse_trend,
    parse_yes_no,
)
from .rules import (  # noqa: F401 (re-exported)
    FOLLOW_UP_EMERGENCY,
    FOLLOW_UP_INFO,
    FOLLOW_UP_NONE,
    FOLLOW_UP_ROUTINE,
    FOLLOW_UP_URGENT,
    TIER_SHORT,
    Triage,
)

# --- Post-op field ids ----------------------------------------------------------
#
# The engine does not depend on these: they name post-op's fields for callers and
# tests that talk about post-op specifically.
Q_OVERALL = "overall_feeling"
Q_PAIN = "pain_present"
Q_FEVER = "fever_reported"
Q_INCISION = "incision_concerns"
Q_SUGAR = "blood_sugar_status"
Q_SWELLING = "legs_swollen"
Q_SLEEP = "sleeping_well"
Q_HAS_QUESTIONS = "has_questions"
F_PAIN_SEVERITY = "pain_severity"
F_PAIN_LOCATION = "pain_location"
F_PAIN_TREND = "pain_trend"
F_INCISION_DESC = "incision_description"
F_SWELLING_DESC = "swelling_description"
#: The patient's own words for their blood sugars ("120 to 250"): the blood_sugar
#: parser's ``raw_field``.
F_SUGAR_READING = "blood_sugar_reading"
#: Questions the patient asked the care team: [{text, category, priority}]
#: (``voa_core.patient_questions``). Engine-wide: an ``open_questions`` question's
#: "yes" collects one or more.
F_PATIENT_QUESTIONS = "patient_questions"

# How many times to re-ask a field the report requires as a typed value before
# giving up and filing a partial. Looping forever strands the patient.
MAX_STRICT_RETRIES = 3


# --- The spec -------------------------------------------------------------------


@cache
def campaign_for(campaign_id: str | None = None) -> Campaign:
    """The spec a questionnaire runs on (read once per id per process).

    Raises ValueError if the spec has problems (``campaign_lint``), so a bad edit
    fails on the first text rather than mid-questionnaire.
    """
    from .campaign_lint import campaign_problems

    c = load_campaign(campaign_id or DEFAULT_CAMPAIGN_ID)
    problems = campaign_problems(c)
    if problems:
        raise ValueError(f"campaign {c.id!r} cannot drive the questionnaire: {problems}")
    return c


def sms_campaign() -> Campaign:
    """The default (post-op) spec. Kept for callers that predate campaign ids."""
    return campaign_for(DEFAULT_CAMPAIGN_ID)


def _c(campaign: Campaign | None) -> Campaign:
    return campaign or campaign_for(DEFAULT_CAMPAIGN_ID)


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


def _patient_matches(q: Question, patient_context: dict) -> bool:
    for key, want in q.when_patient.items():
        have = patient_context.get(key)
        if isinstance(want, bool):
            # coerce_bool, not norm_answer: "1"/"on" from Firestore are true here,
            # as they always were for the SMS plan.
            if coerce_bool(have) != want:
                return False
        elif norm_answer(have) != norm_answer(want):
            return False
    return True


def build_plan(patient_context: dict | None, campaign: Campaign | None = None) -> list[str]:
    """The numbered questions this patient will be asked, in order.

    The campaign's questions without a ``when`` (follow-ups are unnumbered),
    in campaign order, filtered by ``when_patient``.
    """
    c = _c(campaign)
    ctx = patient_context or {}
    return [q.id for q in c.questions if not q.when and _patient_matches(q, ctx)]


def followups_for(field: str, answer: str, campaign: Campaign | None = None) -> tuple[str, ...]:
    """Unnumbered follow-ups triggered by an answer to a numbered question.

    The campaign's questions whose ``when`` is this answer, in campaign order.
    """
    c = _c(campaign)
    got = norm_answer(answer)
    return tuple(q.id for q in c.questions if field in q.when and norm_answer(q.when[field]) == got)


# --- Parsing by type --------------------------------------------------------------


def strict_fields(campaign: Campaign | None = None) -> frozenset[str]:
    """Follow-ups a report needs as a typed value once their parent is answered, so
    "record unknown and move on" is not available for them -- an unparseable
    answer has to stay on the question or the report cannot be filed at all."""
    return frozenset(q.id for q in _c(campaign).questions if q.when and not q.optional)


def unknownable(campaign: Campaign | None = None) -> frozenset[str]:
    """Fields whose unreadable answer (after one re-ask) is recorded as "unknown"."""
    return frozenset(q.id for q in _c(campaign).questions if parsers.sms_unknownable(q))


def parse_answer(field: str, body: str, campaign: Campaign | None = None):
    q = _c(campaign).question(field)
    if q is None:
        raise KeyError(field)
    return parsers.parse(q, body)


def __getattr__(name: str):
    # The pre-engine module constants, now derived from the post-op spec.
    lazy: dict[str, Callable[[], Any]] = {
        "STRICT_FIELDS": strict_fields,
        "UNKNOWNABLE": unknownable,
    }
    if name in lazy:
        return lazy[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# --- Triage -------------------------------------------------------------------


def triage(answers: dict, campaign: Campaign | None = None, patient: dict | None = None) -> Triage:
    """Decide follow_up and its reasons, from the answers alone (the spec's rules).

    Every finding is kept with its own tier (``Triage.findings``), so a call with a
    same-day medication question AND poor sleep records both. ``follow_up`` is the
    most urgent tier found; ``reason`` names that tier's findings first, then the
    lower ones ("Also noted: ...").

    ``follow_up_reason`` may reference ONLY what the patient reported in this
    conversation -- never the procedure, the date, the DOB, or diabetic
    status. That is an explicit rule in <escalation_criteria>.
    """
    from .rules import evaluate

    return evaluate(_c(campaign), answers, patient)


def has_urgent_finding(answers: dict, campaign: Campaign | None = None) -> bool:
    """Whether an abandoned survey is already worth filing.

    Losing a collected "fever: yes" because the patient stopped answering the
    next question is a clinical loss, not a demo compromise.
    """
    return triage(answers, campaign).follow_up in (FOLLOW_UP_EMERGENCY, FOLLOW_UP_URGENT)


# --- Follow-ups that use what the patient already said -------------------------
#
# The text questionnaire is a fixed script, so it would ask "where is the pain?"
# right after "it hurts around my incision". A follow-up with ``reuse: <helper>``
# reads the earlier replies and either rephrases itself ("anywhere else?") or is
# skipped when already answered. The helpers are reviewed code, named here.

_PAIN_SITE = re.compile(
    r"\b(incision|wound|scar|stitches|knee|hip|back|lower back|shoulder|abdomen|stomach|belly|"
    r"side|groin|leg|calf|thigh|foot|feet|ankle|arm|elbow|wrist|hand|neck|head|throat|chest)\b",
    re.I,
)
_SWELL_SIDE = re.compile(
    r"\b(both|one|single|left|right|either)\b",
    re.I,
)
_SWELL_TREND = re.compile(
    r"\b(worse|worsening|bigger|increas\w*|spreading|better|improv\w*|smaller|going down|"
    r"same|not changing|unchanged|stable)\b",
    re.I,
)
_PAIN_WORD = re.compile(
    r"\b(pain\w*|hurt\w*|sore\w*|ache\w*|aching|tender\w*|throb\w*|sting\w*)", re.I
)
#: Replies read for an already-named pain site, in order. The overall-feeling
#: reply counts only when it talks about pain ("my incision looks fine" does not).
_PAIN_SOURCES = (Q_PAIN, F_PAIN_SEVERITY, Q_OVERALL)


def _raw(answers: dict, field: str) -> str:
    return str(raw_answers(answers).get(field) or "")


def known_pain_site(answers: dict) -> str | None:
    """Where the patient already said it hurts ("incision"), from their own words.
    Reads post-op's pain_present, pain_severity and overall_feeling replies."""
    for field in _PAIN_SOURCES:
        text = _raw(answers, field) or (str(answers.get(field) or "") if field == Q_OVERALL else "")
        if field == Q_OVERALL and not _PAIN_WORD.search(text):
            continue
        negated = _negated_words(text)
        for m in _PAIN_SITE.finditer(text):
            if m.group(1).lower() not in negated:
                return m.group(1).lower()
    return None


def swelling_known(answers: dict, parent: str = Q_SWELLING) -> tuple[bool, bool]:
    """(side said, trend said) in the patient's reply to the swelling question."""
    text = _raw(answers, parent)
    return bool(_SWELL_SIDE.search(text)), bool(_SWELL_TREND.search(text))


#: reuse helper -> the SMS variant fields it sends, and their placeholders.
FOLLOWUP_REUSE: dict[str, dict[str, frozenset[str]]] = {
    # pain_location: they already named a site -> "anywhere else?" ({site}).
    "pain_site": {"sms_if_known": frozenset({"site"})},
    # swelling_description: the parent reply said the side, or the trend.
    "swelling_parts": {
        "sms_if_side_known": frozenset(),
        "sms_if_trend_known": frozenset(),
    },
}


def _reuse(field: str, campaign: Campaign | None) -> tuple[str, str]:
    """(helper name, parent question id) for a follow-up; ("", "") for none."""
    q = _c(campaign).question(field)
    if q is None or not q.reuse:
        return "", ""
    return q.reuse, next(iter(q.when), "")


def followup_already_answered(
    field: str, answers: dict, campaign: Campaign | None = None
) -> str | None:
    """The value to record without asking, when the earlier reply fully answers it."""
    helper, parent = _reuse(field, campaign)
    if helper == "swelling_parts":
        side, trend = swelling_known(answers, parent)
        if side and trend:
            return _raw(answers, parent).strip()[:MAX_FREE_TEXT]
    return None


def followup_value(field: str, answers: dict, reply_value, campaign: Campaign | None = None):
    """Join a rephrased follow-up's reply with what the patient said before it."""
    helper, parent = _reuse(field, campaign)
    if helper == "pain_site" and (site := known_pain_site(answers)):
        reply = str(reply_value or "").strip()
        if not reply or parse_yes_no(reply) == "no":
            return site
        return f"{site}; also: {reply}"[:MAX_FREE_TEXT]
    if helper == "swelling_parts":
        before = _raw(answers, parent).strip()
        side, trend = swelling_known(answers, parent)
        reply = str(reply_value or "").strip()
        if before and side and not trend:
            # Asked only "is it getting worse? YES/NO".
            yn = parse_yes_no(reply)
            worse = {"yes": "getting worse", "no": "not getting worse"}.get(yn or "", reply)
            return f"{before}; {worse}"[:MAX_FREE_TEXT]
        if before and (side or trend):
            return f"{before}; {reply}"[:MAX_FREE_TEXT]
    return reply_value


def followup_variant(field: str, answers: dict, campaign: Campaign | None = None):
    """Which reworded SMS variant to send for a follow-up: (variant field, values),
    or None for the default ``sms`` text."""
    helper, parent = _reuse(field, campaign)
    if helper == "pain_site" and (site := known_pain_site(answers)):
        return "sms_if_known", {"site": site}
    if helper == "swelling_parts":
        side, trend = swelling_known(answers, parent)
        if side and not trend:
            return "sms_if_side_known", {}
        if trend and not side:
            return "sms_if_trend_known", {}
    return None
