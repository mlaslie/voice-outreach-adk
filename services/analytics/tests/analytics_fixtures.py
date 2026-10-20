"""A small fictional analytics dataset with hand-computed expectations.

Range: 2026-10-12 .. 2026-10-14 (Mon..Wed), today 2026-10-15 (Thu).

Patients:
  A: c1 rna (10-12), c2 completed by voice (10-13)        -> reached, 2 attempts
  B: c3 machine/voicemail + text invite sent (10-12), then
     c4 a questionnaire completed by text (channel sms)   -> reached, 2 attempts
  C: c5 answered by human, DOB mismatch (10-13)           -> unreached, 1 attempt
  D: c6 completed by voice, emergency (10-14)             -> reached, 1 attempt
  (no patient key) c7: rna, entry e7 (10-14)              -> unreached, 1 attempt
  c8 is outside the range (10-11) and must be ignored.
"""

from __future__ import annotations

from datetime import date


def call(call_id, day, **kw):
    base = {
        "call_id": call_id,
        "patient_key": None,
        "entry_id": None,
        "placed_via": "run",
        "channel": "voice",
        "day": day,
        "initiated_at": f"{day}T14:00:00+00:00",
        "outcome": None,
        "answered": False,
        "answered_by": None,
        "dob_result": None,
        "follow_up": None,
        "overridden": False,
        "override_direction": None,
        "review_status": None,
        "signed_off_at": None,
        "minutes_to_signoff": None,
        "sms_follow_up_status": None,
        "pain_score": None,
        "fever": None,
        "incision": None,
        "swelling": None,
        "swelling_urgent": False,
        "sleeping_well": None,
        "sugar_status": None,
    }
    return {**base, **kw}


START, END, TODAY = date(2026, 10, 12), date(2026, 10, 14), date(2026, 10, 15)

CALLS = [
    call("c1", "2026-10-12", patient_key="kA", entry_id="eA"),
    call(
        "c2",
        "2026-10-13",
        patient_key="kA",
        entry_id="eA",
        initiated_at="2026-10-13T15:00:00+00:00",
        answered=True,
        answered_by="human",
        dob_result="verified",
        outcome="questionnaire_completed",
        follow_up="urgent_same_day",
        overridden=True,
        override_direction="down",
        review_status="signed_off",
        signed_off_at="2026-10-13T18:00:00+00:00",
        minutes_to_signoff=30.0,
        pain_score=8,
        fever="yes",
        sleeping_well="no",
    ),
    call(
        "c3",
        "2026-10-12",
        patient_key="kB",
        entry_id="eB",
        answered=True,
        answered_by="machine_end_beep",
        sms_follow_up_status="sent",
        follow_up="none",
    ),
    call(
        "c4",
        "2026-10-12",
        patient_key="kB",
        entry_id="eB",
        initiated_at="2026-10-12T16:00:00+00:00",
        channel="sms",
        answered=True,
        answered_by="human",
        dob_result="verified",
        outcome="questionnaire_completed",
        follow_up="routine_next_business_day",
        pain_score=5,
        swelling="yes",
        sugar_status="low",
    ),
    call(
        "c5",
        "2026-10-13",
        patient_key="kC",
        entry_id="eC",
        answered=True,
        answered_by="human",
        dob_result="mismatch",
        outcome="identity_verification_failed",
        follow_up="urgent_same_day",
        review_status="signed_off",
        signed_off_at="2026-10-13T16:00:00+00:00",
        minutes_to_signoff=10.0,
    ),
    call(
        "c6",
        "2026-10-14",
        patient_key="kD",
        entry_id="eD",
        answered=True,
        answered_by="human",
        dob_result="verified",
        outcome="questionnaire_completed",
        follow_up="emergency",
        pain_score=0,
        incision="yes",
        swelling="yes",
        swelling_urgent=True,
        sugar_status="high",
        overridden=True,
        override_direction="up",
    ),
    call("c7", "2026-10-14", entry_id="e7"),
    call("c8", "2026-10-11", patient_key="kA", outcome="questionnaire_completed"),
]

REASONS = [
    {"call_id": "c2", "day": "2026-10-13", "tier": "urgent_same_day", "text": "pain 8/10"},
    {"call_id": "c2", "day": "2026-10-13", "tier": "urgent_same_day", "text": "fever reported"},
    {"call_id": "c2", "day": "2026-10-13", "tier": "informational", "text": "not sleeping well"},
    {
        "call_id": "c4",
        "day": "2026-10-12",
        "tier": "routine_next_business_day",
        "text": "pain 5/10",
    },
    {"call_id": "c6", "day": "2026-10-14", "tier": "urgent_same_day", "text": "fever reported"},
    {"call_id": "c8", "day": "2026-10-11", "tier": "urgent_same_day", "text": "fever reported"},
]

QUESTIONS = [
    {"call_id": "c2", "day": "2026-10-13", "category": "medication", "priority": "urgent_same_day"},
    {
        "call_id": "c4",
        "day": "2026-10-12",
        "category": "appointment",
        "priority": "routine_next_business_day",
    },
    {"call_id": "c6", "day": "2026-10-14", "category": "medication", "priority": "urgent_same_day"},
    {"call_id": "c8", "day": "2026-10-11", "category": "diet", "priority": "urgent_same_day"},
]
