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
    """The call's use-case spec (``state["campaign_id"]``, set by the gateway for
    every call); post-op when a state has none (tests, spikes)."""
    from voa_core.campaign import DEFAULT_CAMPAIGN_ID, load_campaign

    return load_campaign(tool_context.state.get("campaign_id") or DEFAULT_CAMPAIGN_ID)


log = logging.getLogger(__name__)

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
# What an unreadable answer does is decided by the question's TYPE (or named
# parser): voa_core.parsers.voice_on_unclear.


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
        if facts := facts_for(getattr(getattr(tool_context, "session", None), "id", "")):
            facts.identity_verified = True
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
        question_id: The id of the question from your instructions, e.g. overall_feeling,
            pain_present, pain_severity, legs_swollen, sleeping_well or has_questions.
        answer: The caller's answer, briefly. Use "yes"/"no" for yes/no questions, a number
            for pain_severity, "better"/"same"/"worse" for pain_trend, and the caller's own
            numbers or words for blood_sugar_status.
    """
    campaign = _campaign(tool_context)
    ids = campaign.question_ids
    question = campaign.question(question_id)
    if question is None:
        return {
            "status": "error",
            "message": f"unknown question_id; use one of {', '.join(ids)}",
        }
    # Normalize with the same deterministic parsers the SMS survey uses (by question
    # type), so voice and text answers are typed identically and triage sees one
    # vocabulary.
    from voa_core import parsers, survey

    parsed: object = parsers.parse(question, answer)
    if parsed is None:
        on_unclear = parsers.voice_on_unclear(question)
        if on_unclear == "unknown":
            parsed = "unknown"
        elif on_unclear == "reask":
            _audit(tool_context, "record_answer", question_id=question_id, status="unclear")
            return {
                "status": "unclear",
                "message": f"The answer to {question_id} was unclear. Ask the question again.",
            }
        else:
            parsed = answer
    answers = dict(tool_context.state.get("answers", {}))
    answers[question_id] = parsed
    if rf := parsers.raw_field(question):
        # The status is for triage; the nurse wants the patient's own words.
        answers[rf] = answer.strip()[: survey.MAX_FREE_TEXT]
    raw = dict(answers.get(survey.RAW_KEY, {}))
    raw[question_id] = answer
    answers[survey.RAW_KEY] = raw
    tool_context.state["answers"] = answers
    _audit(tool_context, "record_answer", question_id=question_id, status="recorded")
    return {"status": "recorded"}


async def record_patient_question(question: str, tool_context: ToolContext) -> dict:
    """Record one question the caller has for the care team. Never answer it yourself.

    Args:
        question: A short summary of the caller's question, in their words,
            e.g. "how much Tylenol can I take".

    Returns:
        status "recorded" (say back a short version, then ask if they have any other
        questions) or "limit" (tell them the care team will go over the rest).
    """
    from voa_core import patient_questions, survey

    if not (question or "").strip():
        return {"status": "error", "message": "Pass the caller's question."}
    answers = dict(tool_context.state.get("answers", {}))
    qs = list(answers.get(survey.F_PATIENT_QUESTIONS, []))
    if len(qs) >= patient_questions.MAX_QUESTIONS:
        _audit(tool_context, "record_patient_question", status="limit")
        return {
            "status": "limit",
            "say": "Tell them the care team will go over the rest of their questions, "
            "then move on.",
        }
    entry = patient_questions.record(question)
    if qs and qs[-1].get("outcome") and qs[-1].get("text") == entry["text"]:
        # Already recorded by answer_from_knowledge (knowledge base on): not twice.
        return {"status": "recorded", "count": len(qs)}
    qs.append(entry)
    answers[survey.F_PATIENT_QUESTIONS] = qs
    for qid in _campaign(tool_context).open_question_ids:
        answers.setdefault(qid, "yes")
    tool_context.state["answers"] = answers
    # Field names and the triage only: the question itself is PHI-adjacent.
    _audit(
        tool_context,
        "record_patient_question",
        status="recorded",
        category=entry["category"],
        priority=entry["priority"],
        count=len(qs),
    )
    return {"status": "recorded", "count": len(qs)}


_DEFAULT_SCRIPTS = {
    "closing_extra": "Before I let you go, please bring a list of your current "
    "medications to your post-op follow-up clinic visit.",
    "closing_questions": "Thank you for your questions. Our care team will follow up with you "
    "to get them answered. Take care, goodbye.",
    "closing_follow_up": "Thank you. One of our nurses will be in touch with you soon. "
    "Take care, goodbye.",
    "closing_no_follow_up": "Thank you, that's everything. Nothing else is required from you "
    "today. Take care, goodbye.",
}


def closing_for(campaign, tier: str, asked_questions: bool) -> str:
    """``closing_extra`` (post-op: the medication-list reminder), then the
    questions / follow-up / all-clear goodbye."""

    def s(key: str) -> str:
        if campaign is not None:
            return campaign.scripts.get(key, "")
        return _DEFAULT_SCRIPTS[key]

    if asked_questions:
        end = s("closing_questions")
    elif tier in {"urgent_same_day", "routine_next_business_day"}:
        end = s("closing_follow_up")
    else:
        end = s("closing_no_follow_up")
    extra = s("closing_extra")
    return f"{extra} {end}" if extra else end


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
    if facts and outcome not in {"emergency_ended", "voicemail_left"} and facts.amd_pending:
        _audit(tool_context, "submit_report", status="refused_amd_pending", outcome=outcome)
        return {
            "status": "refused",
            "message": "The call is still connecting. Do not end it; continue the conversation.",
        }
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
    missing = campaign.missing(patient, visible)
    if outcome == "questionnaire_completed" and missing:
        _audit(tool_context, "submit_report", status="incomplete", missing=missing)
        return {"status": "incomplete", "ask_these_questions": missing}

    from voa_core import reports

    if outcome == "emergency_ended":
        tier, reason = "emergency", "red flag reported during call"
        reasons = reports.single_reason(tier, reason)
    elif outcome == "identity_verification_failed":
        tier, reason = "urgent_same_day", "identity could not be verified"
        reasons = reports.single_reason(tier, reason)
    elif outcome == "questionnaire_completed":
        from voa_core import survey

        t = survey.triage(answers, campaign, patient)
        tier, reason, reasons = t.follow_up, t.reason, t.reasons
    else:
        tier, reason, reasons = "none", outcome.replace("_", " "), []

    report = {
        "outcome": outcome,
        "follow_up": tier,
        "follow_up_reason": reason,
        "follow_up_reasons": reasons,
        "dob_verification_result": tool_context.state.get("dob_verification_result", "skipped"),
        "survey_responses": {k: v for k, v in answers.items() if not k.startswith("_")},
    }
    # Same validator the store applies: if it would reject the report, tell the model
    # now (while the caller is still on the line) instead of failing silently later.
    _clean, errors = reports.validate_report(
        {
            **report,
            "patient_id": str(tool_context.state.get("patient", {}).get("patient_id") or "unknown"),
        },
        campaign=campaign,
    )
    if errors:
        _audit(tool_context, "submit_report", status="invalid", errors=errors)
        return {"status": "incomplete", "problems": errors}
    tool_context.state["report"] = report
    _audit(tool_context, "submit_report", status="ok", outcome=outcome, follow_up=tier)
    # Questions answered from the clinic's Q&A need no care-team follow-up.
    from voa_core import patient_questions

    qs = list(answers.get("patient_questions") or [])
    pending = [q for q in qs if not isinstance(q, dict) or patient_questions.needs_follow_up(q)]
    said_yes = any(_yes(answers.get(qid)) for qid in campaign.open_question_ids)
    asked = bool(pending) or (said_yes and not qs)
    closing = closing_for(campaign, tier, asked)
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
    for fn in (verify_identity, record_answer, record_patient_question, submit_report, end_call):
        tool = FunctionTool(fn)
        tool.behavior = types.Behavior.BLOCKING
        tools.append(tool)
    return tools
