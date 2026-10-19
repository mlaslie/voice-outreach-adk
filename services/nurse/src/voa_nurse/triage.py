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
from voa_core.patient_questions import CATEGORY_LABELS

# Same-day swelling (one leg, worsening, painful): the backend's rule, for colour.
from voa_core.survey import swelling_is_urgent

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


# --------------------------------------------------------------------------- reasons

#: Every tier a reason can carry, most urgent first.
REASON_TIER_ORDER: tuple[str, ...] = (
    "emergency",
    "urgent_same_day",
    "routine_next_business_day",
    "informational",
    "none",
)
REASON_SOURCES = {"triage": "triage", "agent": "automated caller", "nurse": "nurse"}


def _reason_list(raw: Any) -> list[dict]:
    """Normalise a ``follow_up_reasons`` list: entries with text only, most urgent
    first (stable, so the backend's own order breaks ties). An unknown tier sorts
    last and is kept, never dropped."""
    if not isinstance(raw, list):
        return []
    out = []
    for r in raw:
        if isinstance(r, str):
            r = {"text": r}
        if not isinstance(r, dict) or not _s(r.get("text")):
            continue
        tier = r.get("tier") if isinstance(r.get("tier"), str) else None
        out.append(
            {
                "tier": tier,
                "text": str(r["text"]).strip(),
                "source": r.get("source") if r.get("source") in REASON_SOURCES else "triage",
                "set_by": _s(r.get("set_by")),
                "at": _s(r.get("at")),
            }
        )
    rank = {t: i for i, t in enumerate(REASON_TIER_ORDER)}
    return sorted(out, key=lambda r: rank.get(r["tier"], len(rank)))


def _single(text: Any, tier: Any) -> list[dict]:
    t = _s(text)
    if not t:
        return []
    return [
        {
            "tier": tier if isinstance(tier, str) else None,
            "text": t.strip(),
            "source": "agent",
            "set_by": None,
            "at": None,
        }
    ]


def follow_up_reasons(call: dict) -> list[dict]:
    """Why this call needs (or does not need) a nurse, as
    [{tier, text, source, set_by, at}], most urgent first.

    Calls made before ``follow_up_reasons`` existed carry one sentence in
    ``follow_up_reason``: it becomes one reason. Its tier is the agent's own
    (``agent_follow_up``) when a nurse has since changed the call's tier, so the
    agent's words are never shown under the nurse's tier."""
    reasons = _reason_list(call.get("follow_up_reasons"))
    if reasons:
        return reasons
    tier = call.get("follow_up")
    if call.get("follow_up_override") and "agent_follow_up" in call:
        tier = call.get("agent_follow_up")
    return _single(call.get("follow_up_reason"), tier)


def agent_original_reasons(call: dict) -> list[dict]:
    """The automated assessment's reasons, kept by the API when a nurse replaced
    them; empty when no nurse has replaced them."""
    if "agent_follow_up_reasons" not in call and "agent_follow_up_reason" not in call:
        return []
    return _reason_list(call.get("agent_follow_up_reasons")) or _single(
        call.get("agent_follow_up_reason"), call.get("agent_follow_up")
    )


def reasons_set_by_nurse(reasons: list[dict]) -> bool:
    return any(r["source"] == "nurse" for r in reasons)


def reason_summary(call: dict) -> str:
    """The most urgent reason, plus "+N more" when there are others; "" if none."""
    reasons = follow_up_reasons(call)
    if not reasons:
        return ""
    more = len(reasons) - 1
    return reasons[0]["text"] + (f" · +{more} more" if more else "")


def reasons_data(reasons: list[dict]) -> list[dict]:
    """For the model: tier label, text and who gave the reason."""
    return [
        {
            "tier": r["tier"],
            "tier_label": follow_up_label(r["tier"]),
            "text": r["text"],
            "source": REASON_SOURCES[r["source"]],
            **({"set_by": r["set_by"]} if r["set_by"] else {}),
        }
        for r in reasons
    ]


# --------------------------------------------------------------------------- questions

Q_IMMEDIATE = "urgent_same_day"
Q_NEXT_DAY = "routine_next_business_day"
#: How the priority of a patient question reads to a nurse.
QUESTION_PRIORITY_LABELS = {Q_IMMEDIATE: "Immediate", Q_NEXT_DAY: "Next business day"}


def patient_questions(call: dict) -> list[dict]:
    """The patient's questions for the care team, immediate first, as
    {text, category, category_label, priority, priority_label}.

    Lenient with odd entries: a question with text is always shown. An unknown
    priority is treated as Immediate (the backend's "unclear -> same day" rule),
    an unknown category as Other.
    """
    raw = (call.get("survey_responses") or {}).get("patient_questions")
    if not isinstance(raw, list):
        return []
    out = []
    for q in raw:
        if isinstance(q, str):
            q = {"text": q}
        if not isinstance(q, dict) or not _s(q.get("text")):
            continue
        prio = q.get("priority") if q.get("priority") in QUESTION_PRIORITY_LABELS else Q_IMMEDIATE
        cat = q.get("category") if q.get("category") in CATEGORY_LABELS else "other"
        out.append(
            {
                "text": str(q["text"]).strip(),
                "category": cat,
                "category_label": CATEGORY_LABELS.get(cat, "Other"),
                "priority": prio,
                "priority_label": QUESTION_PRIORITY_LABELS[prio],
            }
        )
    return sorted(out, key=lambda q: q["priority"] != Q_IMMEDIATE)  # stable: asked order kept


def has_uncaptured_questions(call: dict) -> bool:
    """The patient said they have questions but none was recorded."""
    r = call.get("survey_responses") or {}
    return str(r.get("has_questions") or "").lower() == "yes" and not patient_questions(call)


def questions_summary(call: dict) -> str | None:
    """ "2 questions · 1 immediate" for worklist rows; None when there is nothing to say."""
    qs = patient_questions(call)
    if qs:
        n, n_now = len(qs), sum(q["priority"] == Q_IMMEDIATE for q in qs)
        text = f"{n} question{'s' * (n != 1)}"
        return f"{text} · {n_now} immediate" if n_now else text
    if has_uncaptured_questions(call):
        return "Has questions, none captured"
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
        # Since 19 Oct 2026 pain_location is the patient's own description of the
        # pain ("sharp, around the knee"); older calls hold a bare site. Quote both.
        detail = " · ".join(x for x in (loc and f"“{loc}”", trend) if x) or note
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

    # Questions added 19 Oct 2026: older calls lack them and read "Not asked".
    legs = (_s(r.get("legs_swollen")) or "").lower()
    swell = _s(r.get("swelling_description"))
    if not has("legs_swollen"):
        add("Leg swelling", "Not asked", "unasked")
    elif legs == "yes":
        level = "alert" if swell and swelling_is_urgent(swell) else "watch"
        add("Leg swelling", "Reports leg swelling", level, swell and f"“{swell}”")
    elif legs == "no":
        add("Leg swelling", "Denies leg swelling", "normal")
    else:
        add("Leg swelling", "Patient unsure", "unknown")

    sleep = (_s(r.get("sleeping_well")) or "").lower()
    if not has("sleeping_well"):
        add("Sleep", "Not asked", "unasked")
    elif sleep == "yes":
        add("Sleep", "Sleeping well", "normal")
    elif sleep == "no":
        add("Sleep", "Not sleeping well", "watch")
    else:
        add("Sleep", "Patient unsure", "unknown")

    ctx = call.get("patient_context") or {}
    reading = _s(r.get("blood_sugar_reading"))
    if ctx.get("is_diabetic") is True or has("blood_sugar_status") or reading:
        bs = (_s(r.get("blood_sugar_status")) or "").lower()
        bg_detail = reading and f"Patient's reading: “{reading}”"
        if not has("blood_sugar_status"):
            add(
                "Blood glucose",
                "Not assessed" if not reading else "Reading given, not rated",
                "unasked" if not reading else "unknown",
                bg_detail,
            )
        elif bs == "in_range":
            add("Blood glucose", "Within usual range", "normal", bg_detail)
        elif bs in ("high", "low"):
            add(
                "Blood glucose",
                "Reports elevated BG" if bs == "high" else "Reports low BG",
                "alert",
                bg_detail,
            )
        else:
            add("Blood glucose", "Patient unsure", "unknown", bg_detail)

    qs = patient_questions(call)
    asked = (_s(r.get("has_questions")) or "").lower()
    if qs:
        n_now = sum(q["priority"] == Q_IMMEDIATE for q in qs)
        add(
            "Patient questions",
            questions_summary(call),
            "alert" if n_now else "watch",
            "; ".join(f"{q['category_label']}: “{q['text']}”" for q in qs),
        )
    elif asked == "yes":
        add("Patient questions", "Has questions, none captured", "watch", "Call to ask.")
    elif asked == "no":
        add("Patient questions", "No questions", "normal")
    elif not has("has_questions"):
        add("Patient questions", "Not asked", "unasked")
    else:
        add("Patient questions", "Patient unsure", "unknown")

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
    own = call.get("follow_up_reason") or reason_summary(call)
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
