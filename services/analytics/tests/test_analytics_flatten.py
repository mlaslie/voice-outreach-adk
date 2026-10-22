"""De-identification and field derivation of the analytics flattening. Fictional data."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from voa_analytics import flatten as fl
from voa_analytics import schema

KEY = b"test-analytics-key"
TZ = "America/New_York"

# Every PHI-looking value below must never appear in any output row.
PHI = {
    "name": "Jordan Rivera",
    "first": "Jordan",
    "last": "Rivera",
    "phone": "+12025550101",
    "phone_digits": "2025550101",
    "dob": "1950-03-15",
    "mrn": "MRN-778812",
    "incision": "the stitches near my belly button are oozing yellow",
    "swelling": "my left calf is swollen and hot",
    "pain_loc": "lower right abdomen",
    "overall": "tired but Rivera family is helping",
    "question1": "Can I take ibuprofen with my warfarin tonight?",
    "question2": "When is my follow-up with Dr. Okafor?",
    "reason_sentence": "Patient reports fever reported, Jordan sounded scared.",
    "nurse_reason": "Called Jordan back, wife says wound looks worse",
    "agent_reason": "Jordan said chest tightness at 3am",
    "sms_reason": "texted +12025550101 but it bounced",
    "operation": "Laparoscopic appendectomy",
    "nurse": "nurse.kim@example.org",
}


def phi_call() -> dict:
    return {
        "call_sid": "CA0001",
        "to": PHI["phone"],
        "from": "+12025550199",
        "initiated_at": "2026-10-14T02:30:00.000+00:00",  # 13 Oct, 22:30 in New York
        "answered_at": "2026-10-14T02:30:12+00:00",
        "ended_at": "2026-10-14T02:40:00+00:00",
        "duration_seconds": 588,
        "patient_id": PHI["mrn"],
        "patient_name": PHI["name"],
        "patient_dob": PHI["dob"],
        "patient_context": {
            "first_name": PHI["first"],
            "last_name": PHI["last"],
            "patient_dob": PHI["dob"],
            "operation_name": PHI["operation"],
            "source": "epic-feed",
        },
        "placed_via": "run",
        "entry_id": "e" * 32,
        "call_status": "success",
        "outcome": "questionnaire_completed",
        "answered_by": "human",
        "dob_verification_result": "verified",
        "channel": "voice",
        "follow_up": "emergency",
        "follow_up_reason": PHI["reason_sentence"],
        "follow_up_reasons": [
            {"tier": "urgent_same_day", "text": "fever reported", "source": "triage"},
            {"tier": "urgent_same_day", "text": "pain 8/10", "source": "triage"},
            {"tier": "emergency", "text": PHI["nurse_reason"], "source": "nurse"},
            {"tier": "emergency", "text": PHI["agent_reason"], "source": "agent"},
            # A triage entry carrying digits beyond a score is dropped, to be safe.
            {"tier": "urgent_same_day", "text": "call 2025550101", "source": "triage"},
        ],
        "agent_follow_up": "urgent_same_day",
        "follow_up_override": {
            "at": "2026-10-14T03:00:00+00:00",
            "from": "urgent_same_day",
            "to": "emergency",
            "reason": PHI["nurse_reason"],
            "set_by": PHI["nurse"],
        },
        "report_doc_id": f"{PHI['mrn']}_20261014_023955_123456",
        "review": {
            "status": "signed_off",
            "by": PHI["nurse"],
            "at": "2026-10-14T04:10:00+00:00",
            "note": PHI["nurse_reason"],
        },
        "sms_follow_up": {"status": "not_sent", "reason": PHI["sms_reason"]},
        "survey_responses": {
            "overall_feeling": PHI["overall"],
            "pain_present": "yes",
            "pain_severity": 8,
            "pain_location": PHI["pain_loc"],
            "pain_trend": "worse",
            "fever_reported": "yes",
            "incision_concerns": "yes",
            "incision_description": PHI["incision"],
            "legs_swollen": "yes",
            "swelling_description": PHI["swelling"],
            "sleeping_well": "no",
            "blood_sugar_status": "high",
            "has_questions": "yes",
            "patient_questions": [
                {"text": PHI["question1"], "category": "medication", "priority": "urgent_same_day"},
                {
                    "text": PHI["question2"],
                    "category": "appointment",
                    "priority": "routine_next_business_day",
                },
            ],
        },
    }


TIMELINE = {
    "timeline": [
        {"event": "media.start", "ms": 1000},
        {"event": "amd.verdict", "ms": 1100, "verdict": "human"},
        {"event": "agent.audio_start", "ms": 1850},
        {"event": "tool.call", "ms": 5000, "name": "record_answer"},
        {"event": "tool.response", "ms": 5010, "name": "record_answer", "status": "ok"},
        {"event": "agent.interrupted", "ms": 6000},
        {"event": "agent.audio_start", "ms": 9000},
        {"event": "agent.interrupted", "ms": 9500},
        {"event": "tool.call", "ms": 12000, "name": "submit_report"},
        {"event": "live.error", "ms": 13000},
        {"event": "live.event_error", "ms": 13500, "code": "x"},
        {"event": "call.ended", "ms": 20000},
    ]
}


def flat_one(doc, events=None, call_id="CA0001"):
    return fl.flatten([(call_id, doc)], {call_id: events} if events else {}, key=KEY, tz=TZ)


def all_values(out: fl.Flattened) -> str:
    return json.dumps([out.calls, out.reasons, out.questions], default=str)


def test_no_phi_in_any_output_row():
    out = flat_one(phi_call(), TIMELINE)
    blob = all_values(out)
    for label, value in PHI.items():
        assert value not in blob, f"{label} leaked into analytics rows"
    # the raw patient id never appears, only its keyed hash
    assert out.calls[0]["patient_key"] == hmac.new(KEY, b"MRN-778812", hashlib.sha256).hexdigest()


def test_rows_match_schema_exactly():
    out = flat_one(phi_call(), TIMELINE)
    for rows, table in (
        (out.calls, schema.FACT_CALLS),
        (out.reasons, schema.FACT_REASONS),
        (out.questions, schema.FACT_QUESTIONS),
    ):
        assert rows
        for row in rows:
            assert tuple(row) == table.column_names


def test_call_row_fields():
    row = flat_one(phi_call(), TIMELINE).calls[0]
    assert row["day"] == "2026-10-13"  # clinic-local date, not the UTC one
    assert row["initiated_at"] == "2026-10-14T02:30:00+00:00"
    assert row["source"] == "epic-feed"
    assert row["placed_via"] == "run" and row["channel"] == "voice" and not row["is_test"]
    assert row["duration_s"] == 588
    assert row["answered"] is True and row["answered_by"] == "human"
    assert row["dob_result"] == "verified"
    assert row["sms_follow_up_status"] == "not_sent"
    assert row["sms_follow_up_reason"] is None  # held a phone number: dropped
    assert row["has_questions"] is True
    assert (row["questions_immediate"], row["questions_next_day"]) == (1, 1)
    assert row["pain_score"] == 8 and row["pain_trend"] == "worse"
    assert (row["fever"], row["incision"], row["swelling"], row["sleeping_well"]) == (
        "yes",
        "yes",
        "yes",
        "no",
    )
    assert row["swelling_urgent"] is True  # one side ("left calf")
    assert row["sugar_status"] == "high"


def test_reasons_keep_only_generic_triage_phrases():
    out = flat_one(phi_call())
    assert out.reasons == [
        {"call_id": "CA0001", "day": "2026-10-13", "tier": "urgent_same_day", "text": t}
        for t in ("fever reported", "pain 8/10")
    ]


def test_questions_keep_category_and_priority_only():
    out = flat_one(phi_call())
    assert out.questions == [
        {
            "call_id": "CA0001",
            "day": "2026-10-13",
            "category": "medication",
            "priority": "urgent_same_day",
        },
        {
            "call_id": "CA0001",
            "day": "2026-10-13",
            "category": "appointment",
            "priority": "routine_next_business_day",
        },
    ]


def test_generic_sms_reason_is_kept():
    doc = phi_call()
    doc["sms_follow_up"] = {
        "status": "not_sent",
        "reason": "this number already got a follow-up text in the last 24 hours",
    }
    assert flat_one(doc).calls[0]["sms_follow_up_reason"].endswith("last 24 hours")


@pytest.mark.parametrize(
    ("agent", "final", "direction"),
    [
        ("urgent_same_day", "emergency", "up"),
        ("routine_next_business_day", "none", "down"),
        ("informational", "routine_next_business_day", "up"),
        ("emergency", "urgent_same_day", "down"),
        ("urgent_same_day", "urgent_same_day", "same"),
    ],
)
def test_override_direction(agent, final, direction):
    doc = phi_call()
    doc["agent_follow_up"], doc["follow_up"] = agent, final
    row = flat_one(doc).calls[0]
    assert row["overridden"] is True
    assert row["agent_follow_up"] == agent
    assert row["override_direction"] == direction


def test_no_override():
    doc = phi_call()
    doc.pop("follow_up_override")
    doc.pop("agent_follow_up")
    row = flat_one(doc).calls[0]
    assert row["overridden"] is False
    assert row["agent_follow_up"] == "emergency" == row["follow_up"]
    assert row["override_direction"] is None


def test_late_report_cleared_override_is_not_an_override():
    # A later report escalated past the nurse's tier: the API clears the override
    # and records the report's tier as agent_follow_up.
    doc = phi_call()
    doc["follow_up_override"] = None
    doc["agent_follow_up"] = "emergency"
    row = flat_one(doc).calls[0]
    assert row["overridden"] is False and row["override_direction"] is None


def test_minutes_to_signoff_from_later_of_end_and_report():
    # ended 02:40:00; report 02:39:55 -> clock starts 02:40:00; signed 04:10 -> 90 min
    row = flat_one(phi_call()).calls[0]
    assert row["signed_off_at"] == "2026-10-14T04:10:00+00:00"
    assert row["minutes_to_signoff"] == 90.0


def test_minutes_to_signoff_by_text_report_starts_at_the_report():
    doc = phi_call()
    doc["report_doc_id"] = "MRN-778812_20261014_033000_000000"  # report 03:30
    assert flat_one(doc).calls[0]["minutes_to_signoff"] == 40.0


def test_minutes_to_signoff_report_only():
    doc = phi_call()
    doc.pop("ended_at")
    # report at 02:39:55.123456 -> 04:10:00 is 90.08 minutes
    assert flat_one(doc).calls[0]["minutes_to_signoff"] == 90.08


def test_no_signoff_when_review_not_signed_off():
    doc = phi_call()
    doc["review"] = {"status": "reopened", "at": "2026-10-14T05:00:00+00:00"}
    row = flat_one(doc).calls[0]
    assert row["review_status"] == "reopened"
    assert row["signed_off_at"] is None and row["minutes_to_signoff"] is None


def test_timeline_metrics():
    row = flat_one(phi_call(), TIMELINE).calls[0]
    assert row["first_audio_ms"] == 850  # first agent audio minus media start
    assert row["interruptions"] == 2
    assert row["tool_calls"] == 2
    assert row["live_errors"] == 2
    assert row["voicemail_played"] is False


def test_timeline_voicemail_and_missing_timeline():
    vm = {"timeline": [{"event": "media.start", "ms": 0}, {"event": "vm.play", "ms": 4000}]}
    row = flat_one(phi_call(), vm).calls[0]
    assert row["voicemail_played"] is True and row["first_audio_ms"] is None
    row = flat_one(phi_call()).calls[0]
    assert row["first_audio_ms"] is None and row["interruptions"] is None
    assert row["voicemail_played"] is None


def test_sparse_doc_and_skips():
    out = fl.flatten(
        [
            ("CA0002", {"initiated_at": "2026-10-13T15:00:00+00:00", "placed_via": "test_call"}),
            ("CA0003", {"patient_id": "P-1"}),  # no placement time
            ("bad id/with slash", {"initiated_at": "2026-10-13T15:00:00+00:00"}),
        ],
        {},
        key=KEY,
        tz=TZ,
    )
    assert out.skipped == 2
    row = out.calls[0]
    assert row["patient_key"] is None and row["is_test"] is True
    assert row["answered"] is False and row["has_questions"] is False
    assert row["pain_score"] is None and row["channel"] == "voice"


def test_pain_zero_when_no_pain():
    doc = phi_call()
    doc["survey_responses"] = {"pain_present": "no"}
    assert flat_one(doc).calls[0]["pain_score"] == 0


def test_free_text_in_enum_fields_is_dropped():
    doc = phi_call()
    doc["outcome"] = "patient Jordan Rivera said no"
    doc["answered_by"] = PHI["phone"]
    doc["entry_id"] = "e " + PHI["name"]
    row = flat_one(doc).calls[0]
    assert row["outcome"] is None and row["answered_by"] is None and row["entry_id"] is None


def test_key_required():
    with pytest.raises(ValueError):
        fl.flatten([], {}, key=b"", tz=TZ)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), ("es-US", "es-US"), ("es", "es-US"), ("zh", "zh"), ("<x>", None)],
)
def test_language_used(value, expected):
    doc = phi_call()
    if value is not None:
        doc["language_used"] = value
    assert flat_one(doc).calls[0]["language_used"] == expected
