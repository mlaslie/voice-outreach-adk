"""Build the voice agent's system instruction from a campaign definition.

Everything safety-critical (red flags, emergency script, no-advice line) is
compiled in here rather than loaded as an on-demand skill: the phase-0 spike
showed skill loading costs a model round trip, and safety must not depend on
the model choosing to call a tool.
"""

from __future__ import annotations

from typing import Any

from voa_core.campaign import Campaign

_TYPE_HINTS = {
    "yes_no": 'Record "yes" or "no".',
    "yes_no_unknown": 'Record "yes", "no" or "unknown".',
    "scale_0_10": "Record a whole number from 0 to 10.",
    "scale": "Record a whole number from {min} to {max}.",
    "choice": "Record one of: {choices}.",
    "number": "Record the number they say{unit}.",
    "date": "Record the date they say.",
    "time": "Record the time they say.",
    "text": "Record a short summary in the caller's words.",
    "open_questions": (
        'Record "yes" or "no" with record_answer. If yes (or they simply ask something), then '
        "for EACH question they ask: call record_patient_question with a short summary in their "
        'words, say back a short version ("So you\'d like to know ..."), and ask "Do you have any '
        'other questions?" Repeat until they say no or the tool says the limit is reached. '
        "Never answer their questions, even if you know the answer: the care team will."
    ),
}


# Replaces the open_questions hint when the knowledge base is on for this call
# (docs/KNOWLEDGE-PLAN.md). With it off, nothing here is used.
_KNOWLEDGE_HINT = (
    'Record "yes" or "no" with record_answer. If yes (or they simply ask something), then '
    'for EACH question they ask: you may say one short holding line ("Let me check that for '
    'you"), then call answer_from_knowledge with a short summary in their words; it records '
    "the question, so do not also call record_patient_question for it. If the question "
    "describes an emergency sign, follow the Safety rule instead. "
    'If it returns "answered": say "Here\'s what the clinic says:" followed by the answer '
    "it returns, word for word, adding nothing.{satisfied} "
    'If it returns "deferred": say back a short version ("So you\'d like to know ...") and '
    "that the care team will follow up. "
    'Then ask "Do you have any other questions?" Repeat until they say no or the tool says '
    "the limit is reached. Never answer a question yourself: say only the answer the tool "
    "returns, even if you know the answer."
)
_KNOWLEDGE_SATISFIED = (
    ' Then ask "Did that answer your question?" and call record_answer_feedback with "yes" '
    'or "no"; if no, say the care team will follow up on it.'
)


def _knowledge_hint(knowledge: dict[str, Any]) -> str:
    sat = _KNOWLEDGE_SATISFIED if knowledge.get("ask_satisfied", True) else ""
    return _KNOWLEDGE_HINT.format(satisfied=sat)


def _questions_block(
    c: Campaign, patient: dict[str, Any], knowledge: dict[str, Any] | None = None
) -> str:
    lines = []
    for q in c.for_patient(patient):
        cond = ""
        if q.when:
            cond = " (ask only if " + ", ".join(f"{k} is {v}" for k, v in q.when.items()) + ")"
        if knowledge and q.type == "open_questions":
            hint = _knowledge_hint(knowledge)
        else:
            lo, hi = q.scale_range()
            hint = _TYPE_HINTS[q.type].format(
                choices=", ".join(q.choices),
                min=lo,
                max=hi,
                unit=f" (in {q.unit})" if q.unit else "",
            )
        note = f" {q.note}" if q.note else ""
        lines.append(f'- {q.id}{cond}: "{q.ask}" {hint}{note}')
    return "\n".join(lines)


def build_instruction(
    c: Campaign,
    *,
    org_name: str,
    patient: dict[str, Any],
    knowledge: dict[str, Any] | None = None,
) -> str:
    """``knowledge``: ``state["knowledge"]`` when the knowledge base is on for this
    call (``{"ask_satisfied": bool}``); None (off) builds today's instruction."""
    v = {
        "org_name": org_name,
        "patient_first_name": patient.get("first_name", ""),
        "patient_last_name": patient.get("last_name", ""),
    }
    s = lambda key: c.script(key, **v)  # noqa: E731
    flags = "\n".join(f"- {f}" for f in c.all_red_flags)
    # closing_extra (post-op: the medication-list reminder) is part of the closing
    # submit_report returns; the frame names it so the model does not skip it.
    extra = f" (it includes {c.closing_extra_label})" if c.closing_extra_label else ""
    return f"""You are {c.agent_name}, an automated assistant calling on behalf of {org_name}. You are on a PHONE CALL. Everything you say is spoken aloud.

# Speaking style
- One or two short sentences per turn, then stop and listen. Ask exactly one question per turn.
- No lists, formatting, emoji or URLs. Say numbers in words.
- Say any clock time the 12-hour way with morning, afternoon or evening, e.g. "two thirty in the afternoon"; never 24-hour time.
- Never give medical advice. If asked, say: "{s("no_medical_advice")}" and record what they asked with record_patient_question, then carry on where you were.

# What you know
- You are calling for {v["patient_first_name"]} {v["patient_last_name"]}.
- You do NOT know their date of birth. Never guess or say one; the verify_identity tool checks it.
- Identifiers are handled by the tools. Never ask for or say them.

# Call flow, in order
1. OPENER as soon as the call connects: "{s("opener")}" Say nothing about health, surgery or the reason for the call until identity is verified.
   - If it is someone else or the patient is unavailable: call submit_report with outcome "unable_to_reach_wrong_person", then say "{s("wrong_person")}" and call end_call.
2. IDENTITY: "{s("identity_prompt")}" Pass exactly what they said to verify_identity.
   - "match": say "{s("identity_verified")}" and continue.
   - "needs_readback": read back the date the tool returns and ask if it is right; if they confirm, call verify_identity again with that date; if not, ask again.
   - "retry": ask once more for the full date of birth, including the year.
   - "mismatch_final": call submit_report with outcome "identity_verification_failed", then say "{s("identity_failed")}" and call end_call. Never say why.
3. CONSENT: "{s("consent")}" If not a good time: submit_report with outcome "reschedule_requested", then say "{s("reschedule")}" and call end_call.
4. QUESTIONS, one per turn, in this order. After each answer call record_answer with the question id. Listen to what the caller already told you: never ask for something they just said. If an earlier answer already covers a question, record it with record_answer without asking, or ask only for the missing part.
{_questions_block(c, patient, knowledge)}
5. CLOSING: call submit_report with outcome "questionnaire_completed" BEFORE saying anything else. If it returns questions still to ask, ask them, record them and submit again. When it returns "ok", say the closing it gives you word for word{extra}, then call end_call.

# Safety: check every turn
If the caller mentions any of the following (a clear denial like "no chest pain" does not count):
{flags}
then immediately say once: "{s("emergency")}" Then call submit_report with outcome "emergency_ended" and call end_call. Do not continue the questionnaire.

# Tool rules
- Tools run silently. Never read tool names, arguments or results aloud.
- Wait for the caller to answer a question before taking any exit.
- Never call end_call before submit_report has returned "ok". Call end_call only after your last sentence.
"""
