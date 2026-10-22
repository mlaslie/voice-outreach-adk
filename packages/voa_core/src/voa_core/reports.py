"""Post-call report validation, its effect on the call doc, and the nurse verdict.

Pure functions only. The report arrives from the voice agent's submit tool or from
the SMS questionnaire; the HTTP layer authenticates it, parses the JSON and calls
``validate_report``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from voa_core.campaign import DEFAULT_CAMPAIGN_ID, Campaign, load_campaign
from voa_core.lifecycle import LEFT_VMAIL, NON_REACHING_OUTCOMES, SUCCESS, strip_phi_for_call_doc

# ---------------------------------------------------------------------------
# Enums (tool schema v2.1)
# ---------------------------------------------------------------------------

#: Follow-up tiers, most to least urgent.
FOLLOW_UP_TIERS: tuple[str, ...] = (
    "emergency",
    "urgent_same_day",
    "routine_next_business_day",
    "informational",
    "none",
)
#: Tiers that need a human to do something.
ACTION_TIERS: tuple[str, ...] = ("emergency", "urgent_same_day", "routine_next_business_day")

OUTCOMES: frozenset[str] = frozenset(
    {
        "questionnaire_completed",
        "reschedule_requested",
        "voicemail_left",
        "unable_to_reach_wrong_person",
        "consent_declined",
        "identity_verification_failed",
        "emergency_ended",
    }
)
#: Never the DOB itself: only whether it matched.
DOB_VERIFICATION_RESULTS: frozenset[str] = frozenset({"verified", "mismatch", "refused", "skipped"})
CHANNELS: frozenset[str] = frozenset({"voice", "sms"})

REQUIRED_FIELDS: tuple[str, ...] = (
    "patient_id",
    "follow_up",
    "follow_up_reason",
    "survey_responses",
)
#: Everything else a client sends is dropped, so a secret holder cannot inject
#: server-managed linkage fields (recording_uri, conversation_id, ...).
CLIENT_FIELDS: frozenset[str] = frozenset(
    {
        "patient_id",
        "session_uuid",
        "call_sid",
        "channel",
        "dob_verification_result",
        "follow_up",
        "follow_up_reason",
        "follow_up_reasons",
        "outcome",
        "survey_responses",
        # Which use case (and version of its spec) the answers belong to. Stamped
        # by the filing layer (gateway, SMS service), never by the model.
        "campaign_id",
        "spec_version",
    }
)

PATIENT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SESSION_UUID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")
#: Provider-neutral call id: a safe document id (no slashes, bounded). Providers
#: with a stricter shape pass their own ``call_id_pattern``.
DEFAULT_CALL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: Report fields copied onto the call doc. survey_responses belongs here: the
#: dashboard reads the call doc, and without it every answer renders "not asked".
CALL_DOC_REPORT_FIELDS: tuple[str, ...] = (
    "outcome",
    "follow_up",
    "follow_up_reason",
    "follow_up_reasons",
    "dob_verification_result",
    "recording_uri",
    "recording_url",
    "channel",
    "survey_responses",
    "campaign_id",
    "spec_version",
)


# ---------------------------------------------------------------------------
# Validation (replaces the checks in the submit-survey endpoint)
# ---------------------------------------------------------------------------


def _completeness_errors(survey: Any, campaign: Campaign) -> list[str]:
    """Backstop for outcome=questionnaire_completed: agents have shipped it after
    asking one or two questions. Rejecting forces them to keep asking.

    What "complete" means comes from the spec: every top-level question that is
    not optional (``Campaign.report_required``), then each ``required_with``
    group (sub-answers once the parent has that answer), with the spec's own
    message; a required scale answer must be a whole number in range."""
    noun = f"{campaign.question_noun} " if campaign.question_noun else ""
    if not isinstance(survey, Mapping) or not survey:
        return [
            "outcome=questionnaire_completed requires a populated survey_responses object. "
            f"Ask every applicable {noun}question and record each answer before submitting."
        ]
    missing = [f for f in campaign.report_required() if f not in survey]
    if missing:
        return [
            "outcome=questionnaire_completed missing required survey_responses fields: "
            f"{', '.join(missing)}. Ask each unanswered {noun}question and re-submit."
        ]
    errors: list[str] = []
    for q in campaign.questions:
        rw = q.required_with
        if rw is None or survey.get(q.id) != rw.answer:
            continue
        if rw.presence == "key":
            absent = [f for f in rw.fields if f not in survey]
        else:
            absent = [f for f in rw.fields if not survey.get(f)]
        if absent:
            errors.append(rw.message.format(missing=", ".join(absent)))
            continue
        for f in rw.fields:
            fq = campaign.question(f)
            if fq is not None and fq.kind == "scale":
                lo, hi = fq.scale_range()
                v = survey.get(f)
                if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                    errors.append(
                        f"{f} must be an integer {lo}-{hi} that the patient actually stated."
                    )
    errors.extend(_questions_errors(survey))
    return errors


def _questions_errors(survey: Mapping[str, Any]) -> list[str]:
    from voa_core import patient_questions as pq

    qs = survey.get("patient_questions")
    if qs is None:
        return []
    if not isinstance(qs, list) or len(qs) > pq.MAX_QUESTIONS:
        return [f"patient_questions must be a list of at most {pq.MAX_QUESTIONS} questions."]
    if not all(pq.valid_entry(q) for q in qs):
        return ["patient_questions entries must be {text, category, priority}."]
    return []


MAX_REASONS = 20
MAX_REASON_CHARS = 200
#: "translation_check": raised after the call by the re-check on the English
#: translation (docs/LANGUAGES-PLAN.md §3.3); never lowers a tier.
REASON_SOURCES: frozenset[str] = frozenset({"triage", "agent", "nurse", "translation_check"})


def reasons_errors(reasons: Any) -> list[str]:
    """``follow_up_reasons``: every reason a call needs (or does not need) a nurse,
    each with its own tier: [{tier, text, source, fields?}]."""
    if not isinstance(reasons, list) or len(reasons) > MAX_REASONS:
        return [f"follow_up_reasons must be a list of at most {MAX_REASONS} reasons."]
    for r in reasons:
        if not (
            isinstance(r, Mapping)
            and r.get("tier") in FOLLOW_UP_TIERS
            and isinstance(r.get("text"), str)
            and 0 < len(r["text"]) <= MAX_REASON_CHARS
            and r.get("source", "triage") in REASON_SOURCES
        ):
            return ["follow_up_reasons entries must be {tier, text, source}."]
    return []


def single_reason(tier: str, text: str, source: str = "agent") -> list[dict[str, Any]]:
    return [{"tier": tier, "text": text[:MAX_REASON_CHARS], "source": source, "fields": []}]


def _enum_error(payload: Mapping[str, Any], field: str, allowed: frozenset[str]) -> str:
    value = payload.get(field)
    if value is None or (isinstance(value, str) and value in allowed):
        return ""
    return f"'{field}' must be one of {', '.join(sorted(allowed))}."


def _report_campaign(payload: Mapping[str, Any], campaign: Campaign | None):
    """(spec, error). The spec named by the payload's campaign_id, the one passed
    in, or the default (post-op: reports filed before campaign ids existed)."""
    if campaign is not None:
        return campaign, ""
    cid = payload.get("campaign_id")
    if cid in (None, ""):
        cid = DEFAULT_CAMPAIGN_ID
    try:
        return load_campaign(cid), ""
    except (OSError, ValueError):
        return None, "'campaign_id' is not a known use case."


def validate_report(
    payload: Any,
    *,
    call_id_pattern: re.Pattern[str] | str = DEFAULT_CALL_ID_PATTERN,
    campaign: Campaign | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """(clean payload, errors). The clean payload is None whenever errors is non-empty.

    * required: patient_id, follow_up, follow_up_reason, survey_responses (keys)
    * patient_id matches ``PATIENT_ID_RE`` (it becomes part of a document id)
    * follow_up is a known tier; outcome, dob_verification_result and channel are in
      their enums when present (None counts as absent)
    * call_sid, when present and non-empty, matches ``call_id_pattern`` (review P-5:
      otherwise any string becomes a call doc id with call_status success)
    * questionnaire_completed carries every answer and sub-answer the use case's
      spec requires (``campaign``, else the payload's ``campaign_id``, else post-op)
    * campaign_id names a known spec; spec_version is a positive integer
    * unknown keys dropped; a malformed session_uuid is dropped, not an error (it is
      only a best-effort join key); null optional fields are dropped
    """
    if not isinstance(payload, Mapping):
        return None, ["Body must be a JSON object."]
    missing = [f for f in REQUIRED_FIELDS if f not in payload]
    if missing:
        return None, [f"Missing required fields: {', '.join(missing)}."]

    errors: list[str] = []
    patient_id = payload["patient_id"]
    if not isinstance(patient_id, str) or not PATIENT_ID_RE.match(patient_id):
        errors.append("'patient_id' must match ^[A-Za-z0-9_.-]{1,64}$.")
    if payload["follow_up"] not in FOLLOW_UP_TIERS:
        errors.append(f"'follow_up' must be one of {', '.join(FOLLOW_UP_TIERS)}.")
    for field, allowed in (
        ("outcome", OUTCOMES),
        ("dob_verification_result", DOB_VERIFICATION_RESULTS),
        ("channel", CHANNELS),
    ):
        if err := _enum_error(payload, field, allowed):
            errors.append(err)

    pattern = re.compile(call_id_pattern) if isinstance(call_id_pattern, str) else call_id_pattern
    call_sid = payload.get("call_sid")
    if call_sid not in (None, "") and (
        not isinstance(call_sid, str) or not pattern.fullmatch(call_sid)
    ):
        errors.append("'call_sid' is not a valid call id.")

    spec, spec_error = _report_campaign(payload, campaign)
    if spec_error:
        errors.append(spec_error)
    sv = payload.get("spec_version")
    if sv is not None and (isinstance(sv, bool) or not isinstance(sv, int) or sv < 1):
        errors.append("'spec_version' must be a positive integer.")
    if payload.get("outcome") == "questionnaire_completed" and spec is not None:
        errors.extend(_completeness_errors(payload.get("survey_responses"), spec))
    if (reasons := payload.get("follow_up_reasons")) is not None:
        errors.extend(reasons_errors(reasons))

    if errors:
        return None, errors

    clean = {k: v for k, v in payload.items() if k in CLIENT_FIELDS}
    for optional in (
        "call_sid",
        "outcome",
        "dob_verification_result",
        "channel",
        "campaign_id",
        "spec_version",
    ):
        if clean.get(optional) in (None, ""):
            clean.pop(optional, None)
    session_uuid = clean.get("session_uuid")
    if not (isinstance(session_uuid, str) and SESSION_UUID_RE.match(session_uuid)):
        clean.pop("session_uuid", None)
    return clean, []


def report_doc_id(patient_id: str, now: datetime) -> str:
    """``{patient_id}_{UTC timestamp to the microsecond}``."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return f"{patient_id}_{now.astimezone(UTC).strftime('%Y%m%d_%H%M%S_%f')}"


def report_settles_call(outcome: str | None) -> bool:
    """Does a report with this outcome mean the patient was reached?"""
    return outcome not in NON_REACHING_OUTCOMES


def report_call_patch(clean: Mapping[str, Any], report_id: str) -> tuple[dict[str, Any], bool]:
    """(call-doc patch, settles) for a validated report. Merge with ``from_report=True``.

    A report that did not reach the patient (wrong person, identity failure,
    consent declined) still attaches, but carries no call_status: the caller must
    then leave the call-list entry alone too.
    """
    outcome = clean.get("outcome")
    settles = report_settles_call(outcome)
    patch: dict[str, Any] = {"report_doc_id": report_id}
    if settles:
        patch["call_status"] = LEFT_VMAIL if outcome == "voicemail_left" else SUCCESS
    for key in CALL_DOC_REPORT_FIELDS:
        if clean.get(key) is not None:
            patch[key] = clean[key]
    return strip_phi_for_call_doc(patch), settles


# ---------------------------------------------------------------------------
# Nurse verdict (mirrors the UI's verdictFor with the C-12 fix)
# ---------------------------------------------------------------------------

#: Outcomes where no questionnaire was answered (by the patient).
INCOMPLETE_OUTCOMES: Mapping[str, str] = {
    "voicemail_left": "Voicemail left — patient not reached.",
    "unable_to_reach_wrong_person": "Wrong number or unknown recipient.",
    "reschedule_requested": "Patient asked to be called back later.",
    "consent_declined": "Patient declined to continue.",
    "identity_verification_failed": "Identity could not be verified; answers not attributable.",
}

_TIERS: Mapping[str, tuple[str, str, str, bool]] = {
    "emergency": (
        "EMERGENCY",
        "Life-threatening symptom reported; 911 advised.",
        "emergency",
        True,
    ),
    "urgent_same_day": (
        "Urgent — call back today",
        "Needs a same-day clinical callback.",
        "urgent",
        True,
    ),
    "routine_next_business_day": (
        "Routine — next business day",
        "Nurse callback when convenient.",
        "routine",
        True,
    ),
    "informational": ("No action required", "Noted for the record only.", "info", False),
    "none": ("No action required", "No concerns reported.", "clear", False),
}


@dataclass(frozen=True)
class Verdict:
    headline: str
    reason: str
    tone: str  # emergency | urgent | routine | info | clear | incomplete
    action_required: bool


def verdict_for(call: Mapping[str, Any]) -> Verdict:
    """The single most important thing a nurse reads about a call.

    Review C-12: the action tiers are checked FIRST. The old order checked the
    incomplete outcomes first, so ``consent_declined`` + ``follow_up: emergency``
    rendered grey "No answers collected" while the banner counted an emergency.
    ``identity_verification_failed`` is an incomplete outcome: with
    ``follow_up: none`` it used to render green "No concerns reported" for a
    questionnaire the wrong person answered.
    """
    reason = call.get("follow_up_reason") or ""
    follow_up = call.get("follow_up")
    outcome = call.get("outcome")

    if follow_up in ACTION_TIERS:
        headline, default, tone, action = _TIERS[follow_up]
        return Verdict(headline, reason or default, tone, action)
    if outcome in INCOMPLETE_OUTCOMES:
        return Verdict(
            "No answers collected", reason or INCOMPLETE_OUTCOMES[outcome], "incomplete", False
        )
    if follow_up in _TIERS:
        headline, default, tone, action = _TIERS[follow_up]
        return Verdict(headline, reason or default, tone, action)
    if call.get("call_status"):
        return Verdict(
            "No follow-up recorded",
            reason or "The call ended without a follow-up tier.",
            "incomplete",
            False,
        )
    return Verdict(
        "Call in progress", reason or "Waiting for the call to complete.", "incomplete", False
    )
