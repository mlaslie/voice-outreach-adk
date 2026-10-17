import re
from datetime import UTC, datetime

import pytest
from voa_core.lifecycle import merge_call_doc
from voa_core.reports import (
    ACTION_TIERS,
    CALL_DOC_REPORT_FIELDS,
    FOLLOW_UP_TIERS,
    OUTCOMES,
    Verdict,
    report_call_patch,
    report_doc_id,
    validate_report,
    verdict_for,
)

NOW = datetime(2026, 7, 22, 18, 5, 1, 123456, tzinfo=UTC)
CALL_ID = "CA" + "1" * 32
TWILIO_LIKE = re.compile(r"^(CA[0-9a-fA-F]{32}|local_[0-9_]+)$")

VALID = {
    "patient_id": "patient-42",
    "follow_up": "urgent_same_day",
    "follow_up_reason": "Reported uncontrolled pain.",
    "survey_responses": {"overall_feeling": "poor", "pain_present": "yes"},
}

COMPLETE_ANSWERS = {
    "overall_feeling": "good",
    "pain_present": "yes",
    "pain_severity": 3,
    "pain_location": "lower back",
    "pain_trend": "better",
    "fever_reported": "no",
    "incision_concerns": "no",
}


def _ok(payload, **kw):
    clean, errors = validate_report(payload, **kw)
    assert errors == [], errors
    return clean


def _errs(payload, **kw):
    clean, errors = validate_report(payload, **kw)
    assert clean is None
    assert errors
    return " ".join(errors)


# --- basic validation -------------------------------------------------------------


def test_valid_submission_passes_through():
    assert _ok(VALID) == VALID


def test_non_object_body_rejected():
    assert "JSON object" in _errs(["not", "an", "object"])
    assert "JSON object" in _errs(None)


def test_missing_required_fields_rejected():
    msg = _errs({"patient_id": "p1"})
    assert "follow_up" in msg and "survey_responses" in msg


@pytest.mark.parametrize("bad", ["../../etc/passwd", "a/b", "", "x" * 65, 42, None, "a b"])
def test_bad_patient_id_rejected(bad):
    assert "patient_id" in _errs({**VALID, "patient_id": bad})


def test_unknown_fields_stripped():
    clean = _ok(
        {
            **VALID,
            "recording_uri": "gs://attacker/pwned.wav",  # server-managed
            "conversation_id": "00000000-0000-0000-0000-000000000000",
            "submitted_at": "2020-01-01",
            "arbitrary_key": "anything",
        }
    )
    assert set(clean) == set(VALID)


def test_session_uuid_kept_when_well_formed_dropped_when_junk():
    assert (
        _ok({**VALID, "session_uuid": "ab12cd34-0000-0000-0000-000000000042"})["session_uuid"]
        == "ab12cd34-0000-0000-0000-000000000042"
    )
    assert "session_uuid" not in _ok({**VALID, "session_uuid": "not a uuid! $$"})
    assert "session_uuid" not in _ok({**VALID, "session_uuid": None})


# --- enums ----------------------------------------------------------------------------


@pytest.mark.parametrize("tier", FOLLOW_UP_TIERS)
def test_every_follow_up_tier_is_accepted(tier):
    assert _ok({**VALID, "follow_up": tier})["follow_up"] == tier


@pytest.mark.parametrize("bad", ["yes", "URGENT", "", None, 1])
def test_unknown_follow_up_rejected(bad):
    assert "follow_up" in _errs({**VALID, "follow_up": bad})


@pytest.mark.parametrize("outcome", sorted(OUTCOMES - {"questionnaire_completed"}))
def test_known_outcomes_accepted(outcome):
    assert _ok({**VALID, "outcome": outcome})["outcome"] == outcome


def test_unknown_outcome_rejected_and_null_outcome_dropped():
    assert "outcome" in _errs({**VALID, "outcome": "patient_was_great"})
    # The SMS questionnaire files partials with no outcome at all.
    assert "outcome" not in _ok({**VALID, "outcome": None})


@pytest.mark.parametrize("dob", ["verified", "mismatch", "refused", "skipped"])
def test_dob_results_accepted(dob):
    assert _ok({**VALID, "dob_verification_result": dob})["dob_verification_result"] == dob


def test_dob_result_must_be_the_enum_never_a_date():
    assert "dob_verification_result" in _errs({**VALID, "dob_verification_result": "1950-03-15"})


def test_channel_enum():
    assert _ok({**VALID, "channel": "sms"})["channel"] == "sms"
    assert "channel" in _errs({**VALID, "channel": "pigeon"})


# --- P-5: call id format ------------------------------------------------------------------


def test_valid_call_id_kept():
    assert _ok({**VALID, "call_sid": CALL_ID})["call_sid"] == CALL_ID


@pytest.mark.parametrize("bad", ["calls/../x", "a/b", "has space", "x" * 65, 123, ["CA1"]])
def test_malformed_call_id_rejected(bad):
    assert "call_sid" in _errs({**VALID, "call_sid": bad})


def test_absent_or_empty_call_id_is_fine_and_dropped():
    # The SMS sidecar sends call_sid=None when its session has no call.
    assert "call_sid" not in _ok({**VALID, "call_sid": None})
    assert "call_sid" not in _ok({**VALID, "call_sid": ""})


def test_provider_specific_call_id_pattern():
    assert _ok({**VALID, "call_sid": CALL_ID}, call_id_pattern=TWILIO_LIKE)["call_sid"] == CALL_ID
    assert _ok({**VALID, "call_sid": "local_1_2"}, call_id_pattern=TWILIO_LIKE.pattern)
    assert "call_sid" in _errs({**VALID, "call_sid": "CA123"}, call_id_pattern=TWILIO_LIKE)
    # fullmatch: a valid id with a trailing newline is not valid
    assert "call_sid" in _errs({**VALID, "call_sid": CALL_ID + "\n"}, call_id_pattern=TWILIO_LIKE)


# --- completeness backstop -----------------------------------------------------------------


def _completed(answers):
    return {**VALID, "outcome": "questionnaire_completed", "survey_responses": answers}


def test_complete_questionnaire_passes():
    assert _ok(_completed(COMPLETE_ANSWERS))["outcome"] == "questionnaire_completed"


@pytest.mark.parametrize("empty", [{}, None, "all good", []])
def test_completed_requires_populated_answers(empty):
    assert "populated" in _errs(_completed(empty))


@pytest.mark.parametrize(
    "drop", ["overall_feeling", "pain_present", "fever_reported", "incision_concerns"]
)
def test_completed_requires_every_wellness_answer(drop):
    answers = {k: v for k, v in COMPLETE_ANSWERS.items() if k != drop}
    assert drop in _errs(_completed(answers))


@pytest.mark.parametrize("drop", ["pain_severity", "pain_location", "pain_trend"])
def test_pain_yes_requires_sub_answers(drop):
    answers = {k: v for k, v in COMPLETE_ANSWERS.items() if k != drop}
    assert drop in _errs(_completed(answers))


def test_pain_no_needs_no_sub_answers():
    answers = {k: v for k, v in COMPLETE_ANSWERS.items() if not k.startswith("pain_")}
    _ok(_completed({**answers, "pain_present": "no"}))


@pytest.mark.parametrize("sev", [-1, 11, "7", 7.5, None, True])
def test_pain_severity_must_be_an_int_0_to_10(sev):
    assert "pain_severity" in _errs(_completed({**COMPLETE_ANSWERS, "pain_severity": sev}))


@pytest.mark.parametrize("sev", [0, 10])
def test_pain_severity_bounds_inclusive(sev):
    _ok(_completed({**COMPLETE_ANSWERS, "pain_severity": sev}))


def test_incision_yes_requires_a_description():
    answers = {**COMPLETE_ANSWERS, "incision_concerns": "yes"}
    assert "incision_description" in _errs(_completed(answers))
    _ok(_completed({**answers, "incision_description": "slight redness"}))


def test_backstop_applies_only_to_questionnaire_completed():
    _ok({**VALID, "outcome": "voicemail_left", "survey_responses": {}})


# --- report -> call doc --------------------------------------------------------------------


def test_report_doc_id():
    assert report_doc_id("patient-42", NOW) == "patient-42_20260722_180501_123456"
    with pytest.raises(ValueError):
        report_doc_id("p", datetime(2026, 1, 1))


def test_completed_report_settles_the_call_and_copies_answers():
    clean = _ok(
        {
            **_completed(COMPLETE_ANSWERS),
            "call_sid": CALL_ID,
            "follow_up": "none",
            "dob_verification_result": "verified",
            "channel": "voice",
        }
    )
    patch, settles = report_call_patch(clean, "rid")
    assert settles
    assert patch["call_status"] == "success"
    assert patch["report_doc_id"] == "rid"
    assert patch["outcome"] == "questionnaire_completed"
    assert patch["dob_verification_result"] == "verified"
    # The dashboard reads the call doc: without this every answer reads "not asked".
    assert patch["survey_responses"] == COMPLETE_ANSWERS
    assert "survey_responses" in CALL_DOC_REPORT_FIELDS
    assert "patient_id" not in patch and "call_sid" not in patch


def test_voicemail_report_settles_as_left_vmail():
    patch, settles = report_call_patch(_ok({**VALID, "outcome": "voicemail_left"}), "rid")
    assert settles and patch["call_status"] == "left_vmail"


def test_report_without_outcome_settles_as_success():
    patch, settles = report_call_patch(_ok(VALID), "rid")
    assert settles and patch["call_status"] == "success"


@pytest.mark.parametrize(
    "outcome",
    ["unable_to_reach_wrong_person", "identity_verification_failed", "consent_declined"],
)
def test_a_report_that_did_not_reach_the_patient_does_not_settle(outcome):
    clean = _ok({**VALID, "outcome": outcome, "follow_up": "none", "survey_responses": {}})
    patch, settles = report_call_patch(clean, "rid")
    assert not settles
    assert "call_status" not in patch
    assert patch["report_doc_id"] == "rid"  # the report still attaches
    # and the merge leaves the call's status where it was
    doc = merge_call_doc({"call_status": "rna"}, patch, now=NOW, from_report=True)
    assert doc["call_status"] == "rna" and doc["outcome"] == outcome


def test_report_promotes_rna_to_success_through_the_merge():
    patch, _ = report_call_patch(_ok(_completed(COMPLETE_ANSWERS)), "rid")
    doc = merge_call_doc({"call_status": "rna"}, patch, now=NOW, from_report=True)
    assert doc["call_status"] == "success"


def test_report_patch_never_carries_a_dob():
    patch, _ = report_call_patch({**_ok(VALID), "patient_dob": "1950-03-15"}, "rid")
    assert "patient_dob" not in patch


# --- C-12: verdict_for ---------------------------------------------------------------------


def test_action_tiers_listed_in_order():
    assert ACTION_TIERS == FOLLOW_UP_TIERS[:3]


@pytest.mark.parametrize(
    "outcome",
    [
        "consent_declined",
        "unable_to_reach_wrong_person",
        "voicemail_left",
        "reschedule_requested",
        "identity_verification_failed",
    ],
)
@pytest.mark.parametrize(
    "tier,tone",
    [
        ("emergency", "emergency"),
        ("urgent_same_day", "urgent"),
        ("routine_next_business_day", "routine"),
    ],
)
def test_an_action_tier_is_never_masked_by_an_incomplete_outcome(outcome, tier, tone):
    v = verdict_for({"outcome": outcome, "follow_up": tier, "call_status": "success"})
    assert v.tone == tone
    assert v.action_required is True


def test_identity_failure_with_no_tier_is_not_green():
    v = verdict_for({"outcome": "identity_verification_failed", "follow_up": "none"})
    assert v.tone == "incomplete"
    assert v.headline == "No answers collected"
    assert "No concerns" not in v.reason


def test_identity_failure_from_the_voice_mismatch_path_is_urgent():
    v = verdict_for({"outcome": "identity_verification_failed", "follow_up": "urgent_same_day"})
    assert v.tone == "urgent" and v.action_required


def test_incomplete_outcome_beats_informational_and_none():
    for tier in ("informational", "none"):
        v = verdict_for({"outcome": "voicemail_left", "follow_up": tier})
        assert v.tone == "incomplete" and not v.action_required


def test_plain_tiers():
    assert verdict_for({"follow_up": "none", "outcome": "questionnaire_completed"}) == Verdict(
        "No action required", "No concerns reported.", "clear", False
    )
    assert verdict_for({"follow_up": "informational"}).tone == "info"
    emergency = verdict_for({"follow_up": "emergency", "follow_up_reason": "Chest pain."})
    assert emergency.reason == "Chest pain." and emergency.headline == "EMERGENCY"


def test_no_tier():
    assert verdict_for({"call_status": "rna"}).headline == "No follow-up recorded"
    assert verdict_for({}).headline == "Call in progress"
    assert verdict_for({"follow_up": "bogus"}).tone == "incomplete"


@pytest.mark.parametrize("field", ["outcome", "dob_verification_result", "channel", "follow_up"])
def test_unhashable_enum_values_are_errors_not_crashes(field):
    assert field in _errs({**VALID, field: ["x"]})
