"""Call triage for the nurse assistant.

Which calls need a callback, which answers are abnormal, and the headline a nurse
reads first. Ported from the previous assistant (itself a port of the dashboard's
``triage.ts`` / ``status.ts``), with two changes:

* the verdict's ORDER and TONE come from :func:`voa_core.reports.verdict_for`, the
  backend's rule (review C-12: action tiers are checked before incomplete
  outcomes, and ``identity_verification_failed`` is incomplete). Only the wording
  is the assistant's own, in charting language;
* every timestamp read tolerates ``None`` and garbage (review A-2).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from voa_core import reports
from voa_core.lifecycle import TERMINAL_STATUSES

STATUS_LABELS = {
    "success": "Completed",
    "left_vmail": "Left voicemail",
    "rna": "No answer",
    "busy": "Line busy",
    "failed": "Call failed",
    "interrupted": "Call dropped",
    "canceled": "Canceled",
}

#: Tiers that need an RN to act, most urgent first (the backend's list).
FOLLOW_UP_TIERS: tuple[str, ...] = reports.ACTION_TIERS

# Disposition wording, as a nurse would chart it. The tier values come from the
# voice agent and must not change.
FOLLOW_UP_LABELS = {
    "emergency": "Emergent: 911 advised",
    "urgent_same_day": "Urgent: RN callback today",
    "routine_next_business_day": "Routine: RN callback next business day",
    "informational": "FYI: no callback needed",
    "none": "No follow-up needed",
}
FOLLOW_UP_SHORT = {
    "emergency": "emergent",
    "urgent_same_day": "urgent",
    "routine_next_business_day": "routine",
}

SEVERE = re.compile(
    r"(terrible|awful|unbearable|excruciating|worst|severe|really bad|very bad)", re.I
)
NEGATIVE = re.compile(
    r"\b(poor|bad|rough|miserable|horrible|worse|struggling|not (?:good|great|well|ok|okay)|"
    r"unwell|lousy|weak|exhausted)\b",
    re.I,
)
POSITIVE = re.compile(
    r"\b(good|great|fine|ok|okay|well|better|excellent|fantastic|wonderful|alright|all right|"
    r"no complaints)\b",
    re.I,
)


def overall_level(text: str) -> str:
    """The patient's own words are free text: a colour, not a tier."""
    if SEVERE.search(text):
        return "alert"
    if NEGATIVE.search(text):
        return "watch"
    if POSITIVE.search(text):
        return "normal"
    return "unknown"


def is_active(call: dict) -> bool:
    return not call.get("call_status") or call["call_status"] not in TERMINAL_STATUSES


def status_label(call: dict) -> str:
    s = call.get("call_status")
    return STATUS_LABELS.get(s, s) if s else "Call in progress"


def follow_up_label(tier: str | None) -> str:
    return FOLLOW_UP_LABELS.get(tier, tier) if tier else "No disposition recorded"


def needs_follow_up(call: dict) -> bool:
    return call.get("follow_up") in FOLLOW_UP_TIERS


def parse_time(raw: Any) -> datetime | None:
    """An ISO timestamp, timezone-aware, or None if missing or garbled (A-2)."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def call_time(call: dict) -> datetime | None:
    """When the call was placed. A call doc with ``initiated_at: null`` used to
    raise TypeError and turn every list into the generic error card."""
    return parse_time(call.get("initiated_at"))


def ts(call: dict) -> float:
    dt = call_time(call)
    return dt.timestamp() if dt else 0.0


def latest_call_per_patient(calls: list[dict]) -> list[dict]:
    """Only a patient's most recent call speaks for them."""
    latest: dict[str, dict] = {}
    for c in calls:
        key = f"pid:{c['patient_id']}" if c.get("patient_id") else f"doc:{c.get('doc_id')}"
        if key not in latest or ts(c) > ts(latest[key]):
            latest[key] = c
    return list(latest.values())


def is_assessment(call: dict) -> bool:
    """Does this call say anything about the patient's condition?

    A raised flag always does. A nurse's tier change does. But "no follow-up" /
    "FYI" counts only on a completed questionnaire: the voice agent files
    follow_up="none" on a VOICEMAIL, and that must never clear an urgent flag
    from an earlier call.
    """
    return (
        needs_follow_up(call)
        or bool(call.get("follow_up_override"))
        or (bool(call.get("follow_up")) and call.get("outcome") == "questionnaire_completed")
    )


def latest_assessed_call_per_patient(calls: list[dict]) -> list[dict]:
    """A patient's most recent call that carries a disposition. A redial that never
    reached the patient says nothing about them and must not hide an earlier flag."""
    return latest_call_per_patient([c for c in calls if is_assessment(c)])


def follow_up_calls(calls: list[dict]) -> list[dict]:
    """Outstanding callbacks, most severe first, then newest first."""
    out = [c for c in latest_assessed_call_per_patient(calls) if needs_follow_up(c)]
    return sorted(out, key=lambda c: (FOLLOW_UP_TIERS.index(c["follow_up"]), -ts(c)))


def _s(v) -> str | None:
    return None if v is None or v == "" else str(v)


def _n(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def findings_for(call: dict) -> list[dict]:
    """Each answer as {label, value, detail, level}; level is
    alert | watch | normal | unknown | unasked. Clinical answers first."""
    r = call.get("survey_responses") or {}

    def has(k: str) -> bool:
        return r.get(k) not in (None, "")

    out: list[dict] = []

    def add(label, value, level, detail=None):
        out.append({"label": label, "value": value, "level": level, "detail": detail})

    if not has("pain_present"):
        add("Pain", "Not assessed", "unasked")
    elif str(r["pain_present"]).lower() == "no":
        add("Pain", "Denies pain", "normal")
    else:
        sev = _n(r.get("pain_severity"))
        trend = (_s(r.get("pain_trend")) or "").lower() or None
        note = _s(r.get("pain_severity_note"))
        loc = _s(r.get("pain_location"))
        level = "watch"
        if sev is not None:
            level = "alert" if sev >= 8 else "watch" if sev >= 4 else "normal"
        elif note and SEVERE.search(note):
            level = "alert"
        elif note:
            level = "unknown"
        if trend in ("worse", "worsening", "getting worse"):
            level = "alert"
        value = (
            f"Reports pain {sev:g}/10"
            if sev is not None
            else "Reports pain, not rated 0–10"
            if note
            else "Reports pain"
        )
        detail = " · ".join(x for x in (loc and f"at {loc}", trend) if x) or note
        add("Pain", value, level, detail)

    fever = (_s(r.get("fever_reported")) or "").lower()
    if not has("fever_reported"):
        add("Fever", "Not assessed", "unasked")
    elif fever == "yes":
        add("Fever", "Reports fever", "alert")
    elif fever == "no":
        add("Fever", "Denies fever", "normal")
    else:
        add("Fever", "Patient unsure", "unknown")

    inc = (_s(r.get("incision_concerns")) or "").lower()
    if not has("incision_concerns"):
        add("Incision / site", "Not assessed", "unasked")
    elif inc == "yes":
        add("Incision / site", "Reports concern", "alert", _s(r.get("incision_description")))
    elif inc == "no":
        add("Incision / site", "Denies concerns", "normal")
    else:
        add("Incision / site", "Patient unsure", "unknown")

    ctx = call.get("patient_context") or {}
    if ctx.get("is_diabetic") is True or has("blood_sugar_status"):
        bs = (_s(r.get("blood_sugar_status")) or "").lower()
        if not has("blood_sugar_status"):
            add("Blood glucose", "Not assessed", "unasked")
        elif bs == "in_range":
            add("Blood glucose", "Within usual range", "normal")
        elif bs in ("high", "low"):
            add(
                "Blood glucose",
                "Reports elevated BG" if bs == "high" else "Reports low BG",
                "alert",
            )
        else:
            add("Blood glucose", "Patient unsure", "unknown")

    dob = (call.get("dob_verification_result") or "").lower()
    if dob:
        value = {
            "verified": "Verified by DOB",
            "mismatch": "DOB mismatch: identity NOT verified",
            "refused": "Declined DOB verification",
        }.get(dob, "Not verified")
        level = "normal" if dob == "verified" else "alert" if dob == "mismatch" else "watch"
        add("Identity", value, level)

    overall = _s(r.get("overall_feeling"))
    if overall:
        quoted = overall if len(overall) <= 90 else overall[:90] + "…"
        add("Patient statement", f"“{quoted}”", overall_level(overall))
    return out


# --------------------------------------------------------------------------- verdict

_HEADLINES = {
    "emergency": (
        "EMERGENT: 911 advised",
        "Patient reported a red-flag symptom; instructed to call 911.",
    ),
    "urgent": ("Urgent: RN callback today", "Needs a same-day RN callback."),
    "routine": ("Routine: RN callback next business day", "RN callback within one business day."),
    "info": ("No callback needed (FYI)", "Documented for the record only."),
    "clear": ("No follow-up needed", "No concerns reported."),
}
_INCOMPLETE = {
    "voicemail_left": "Left voicemail; patient not reached.",
    "unable_to_reach_wrong_person": "Wrong number or patient unknown at this number.",
    "reschedule_requested": "Patient requested a call back at another time.",
    "consent_declined": "Patient declined the follow-up assessment.",
    "identity_verification_failed": "Identity not verified by DOB; answers not attributable.",
}


def verdict_for(call: dict) -> dict:
    """{headline, reason, tone, action_required}: taken from the voice agent's tier,
    never re-derived. Tone and precedence are the backend's (voa_core.reports)."""
    v = reports.verdict_for(call)
    own = call.get("follow_up_reason") or ""
    if v.tone in _HEADLINES:
        headline, default = _HEADLINES[v.tone]
        return {
            "headline": headline,
            "reason": own or default,
            "tone": v.tone,
            "action_required": v.action_required,
        }
    outcome = call.get("outcome")
    if outcome in reports.INCOMPLETE_OUTCOMES:
        headline = "Assessment not completed"
        default = _INCOMPLETE.get(outcome, reports.INCOMPLETE_OUTCOMES[outcome])
    elif call.get("call_status"):
        headline, default = (
            "No disposition recorded",
            "The call ended without a follow-up disposition.",
        )
    else:
        headline, default = "Call in progress", "Awaiting call completion."
    return {
        "headline": headline,
        "reason": own or default,
        "tone": "incomplete",
        "action_required": False,
    }
