"""Voice-agent tools.

Design rule: the model never sees identifiers or the date of birth, and never
decides the triage tier. Tools read patient context from session state
(``state["patient"]``), validate, and return short instructions for what to say.

All tools are BLOCKING: on gemini-3.8-live the default is NON_BLOCKING, which lets
the model keep talking before the tool returns ("spoke before submitting").
"""

from __future__ import annotations

import logging
import time
from typing import Any

from google.adk.tools import FunctionTool, ToolContext
from google.genai import types
from voa_core.dob import DobResult, check_dob, speakable

from .call_facts import facts_for


def _campaign(tool_context: ToolContext):
    cid = tool_context.state.get("campaign_id")
    if not cid:
        return None
    from voa_core.campaign import load_campaign

    return load_campaign(cid)


log = logging.getLogger(__name__)

QUESTION_IDS = (
    "overall_feeling",
    "pain_present",
    "pain_severity",
    "pain_location",
    "pain_trend",
    "fever_reported",
    "incision_concerns",
    "incision_description",
)
OUTCOMES = (
    "questionnaire_completed",
    "reschedule_requested",
    "unable_to_reach_wrong_person",
    "identity_verification_failed",
    "consent_declined",
    "emergency_ended",
    "voicemail_left",
)
MAX_DOB_ATTEMPTS = 2
_MUST_BE_TYPED = {"pain_present", "pain_severity", "pain_trend", "incision_concerns"}


def _yes(value: Any) -> bool:
    return str(value).strip().lower() in {"yes", "y", "true", "yeah", "yep"}


def _audit(tool_context: ToolContext, name: str, **fields: Any) -> None:
    """Append to the per-call tool audit trail (field names and verdicts only, no PHI)."""
    trail = list(tool_context.state.get("tool_audit", []))
    trail.append({"t": time.monotonic(), "tool": name, **fields})
    tool_context.state["tool_audit"] = trail


async def verify_identity(spoken_dob: str, tool_context: ToolContext) -> dict:
    """Check the date of birth the caller just said against the record.

    Args:
        spoken_dob: Exactly what the caller said for their date of birth, e.g. "March 15th 1950".

    Returns:
        result: "match", "needs_readback", "retry" or "mismatch_final".
    """
    patient = tool_context.state.get("patient", {})
    attempts = int(tool_context.state.get("dob_attempts", 0)) + 1
    tool_context.state["dob_attempts"] = attempts
    check = check_dob(spoken_dob, patient.get("dob", ""))

    if check.result is DobResult.MATCH:
        tool_context.state["dob_verification_result"] = "verified"
        _audit(tool_context, "verify_identity", result="match", attempt=attempts)
        return {"result": "match"}
    if check.result is DobResult.UNPARSEABLE and attempts <= MAX_DOB_ATTEMPTS:
        tool_context.state["dob_attempts"] = attempts - 1  # an unclear answer costs no attempt
        _audit(tool_context, "verify_identity", result="retry_unparseable", attempt=attempts)
        return {"result": "retry", "say": "Ask for the full date of birth, including the year."}
    if attempts < MAX_DOB_ATTEMPTS:
        _audit(tool_context, "verify_identity", result="retry_mismatch", attempt=attempts)
        heard = speakable(check.heard) if check.heard else None
        return {"result": "needs_readback", "heard": heard} if heard else {"result": "retry"}
    tool_context.state["dob_verification_result"] = "mismatch"
    _audit(tool_context, "verify_identity", result="mismatch_final", attempt=attempts)
    return {"result": "mismatch_final"}


async def record_answer(question_id: str, answer: str, tool_context: ToolContext) -> dict:
    """Record the caller's answer to one questionnaire question.

    Args:
        question_id: One of overall_feeling, pain_present, pain_severity, pain_location,
            pain_trend, fever_reported, incision_concerns, incision_description.
        answer: The caller's answer, briefly. Use "yes"/"no" for yes/no questions, a number
            for pain_severity, and "better"/"same"/"worse" for pain_trend.
    """
    if question_id not in QUESTION_IDS:
        return {
            "status": "error",
            "message": f"unknown question_id; use one of {', '.join(QUESTION_IDS)}",
        }
    # Normalize with the same deterministic parsers the SMS survey uses, so voice and
    # text answers are typed identically and triage sees one vocabulary.
    from voa_core import survey

    parsed: object = answer
    if question_id in survey._PARSERS:
        parsed = survey.parse_answer(question_id, answer)
        if parsed is None and question_id == survey.Q_FEVER:
            parsed = "unknown"
        if parsed is None and question_id in _MUST_BE_TYPED:
            _audit(tool_context, "record_answer", question_id=question_id, status="unclear")
            return {
                "status": "unclear",
                "message": f"The answer to {question_id} was unclear. Ask the question again.",
            }
        if parsed is None:
            parsed = answer
    answers = dict(tool_context.state.get("answers", {}))
    answers[question_id] = parsed
    raw = dict(answers.get(survey.RAW_KEY, {}))
    raw[question_id] = answer
    answers[survey.RAW_KEY] = raw
    tool_context.state["answers"] = answers
    _audit(tool_context, "record_answer", question_id=question_id, status="recorded")
    return {"status": "recorded"}


def _missing(answers: dict[str, str]) -> list[str]:
    need = ["overall_feeling", "pain_present", "fever_reported", "incision_concerns"]
    if _yes(answers.get("pain_present")):
        need += ["pain_severity", "pain_location", "pain_trend"]
    if _yes(answers.get("incision_concerns")):
        need.append("incision_description")
    return [q for q in need if not str(answers.get(q, "")).strip()]


async def submit_report(outcome: str, tool_context: ToolContext) -> dict:
    """File the call report. Call this BEFORE saying the closing or goodbye.

    Args:
        outcome: questionnaire_completed, reschedule_requested, unable_to_reach_wrong_person,
            identity_verification_failed, consent_declined, emergency_ended or voicemail_left.

    Returns:
        status "ok" with the exact closing sentence to say, or status "incomplete" with the
        questions that still need answers.
    """
    if outcome not in OUTCOMES:
        return {"status": "error", "message": f"outcome must be one of {', '.join(OUTCOMES)}"}
    # Guard (spike finding): the model sometimes asks a question and, in the same breath,
    # takes an exit without waiting. Exits other than emergency/voicemail need the
    # caller to have answered the last question.
    facts = facts_for(getattr(getattr(tool_context, "session", None), "id", ""))
    if facts and outcome not in {"emergency_ended", "voicemail_left"}:
        if facts.unanswered_question or (facts.caller_turns == 0):
            _audit(tool_context, "submit_report", status="refused_unanswered", outcome=outcome)
            return {
                "status": "refused",
                "message": "You asked the caller a question they have not answered yet. "
                "Stop, wait for their answer, and continue the call.",
            }
    answers = dict(tool_context.state.get("answers", {}))
    campaign = _campaign(tool_context)
    patient = tool_context.state.get("patient", {})
    visible = {k: v for k, v in answers.items() if not k.startswith("_")}
    missing = campaign.missing(patient, visible) if campaign else _missing(visible)
    if outcome == "questionnaire_completed" and missing:
        _audit(tool_context, "submit_report", status="incomplete", missing=missing)
        return {"status": "incomplete", "ask_these_questions": missing}

    if outcome == "emergency_ended":
        tier, reason = "emergency", "red flag reported during call"
    elif outcome == "identity_verification_failed":
        tier, reason = "urgent_same_day", "identity could not be verified"
    elif outcome == "questionnaire_completed":
        from voa_core import survey

        t = survey.triage(answers)
        tier, reason = t.follow_up, t.reason
    else:
        tier, reason = "none", outcome.replace("_", " ")

    report = {
        "outcome": outcome,
        "follow_up": tier,
        "follow_up_reason": reason,
        "dob_verification_result": tool_context.state.get("dob_verification_result", "skipped"),
        "survey_responses": {k: v for k, v in answers.items() if not k.startswith("_")},
    }
    # Same validator the store applies: if it would reject the report, tell the model
    # now (while the caller is still on the line) instead of failing silently later.
    from voa_core import reports

    _clean, errors = reports.validate_report(
        {
            **report,
            "patient_id": str(tool_context.state.get("patient", {}).get("patient_id") or "unknown"),
        }
    )
    if errors:
        _audit(tool_context, "submit_report", status="invalid", errors=errors)
        return {"status": "incomplete", "problems": errors}
    tool_context.state["report"] = report
    _audit(tool_context, "submit_report", status="ok", outcome=outcome, follow_up=tier)
    closing = (
        "Thank you. One of our nurses will be in touch with you soon. Take care, goodbye."
        if tier in {"urgent_same_day", "routine_next_business_day"}
        else "Thank you, that's everything. Nothing else is required from you today. "
        "Take care, goodbye."
    )
    return (
        {"status": "ok", "closing": closing}
        if outcome == "questionnaire_completed"
        else {"status": "ok"}
    )


async def end_call(reason: str, tool_context: ToolContext) -> dict:
    """Hang up. Call only after your final sentence has been spoken.

    Args:
        reason: A few words on why the call is ending.
    """
    if "report" not in tool_context.state:
        _audit(tool_context, "end_call", refused=True)
        return {"status": "refused", "message": "Call submit_report first."}
    tool_context.state["end_requested"] = True
    _audit(tool_context, "end_call", refused=False)
    return {"status": "ending"}


def build_tools() -> list[FunctionTool]:
    tools = []
    for fn in (verify_identity, record_answer, submit_report, end_call):
        tool = FunctionTool(fn)
        tool.behavior = types.Behavior.BLOCKING
        tools.append(tool)
    return tools
