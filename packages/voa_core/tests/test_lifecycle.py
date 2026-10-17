from datetime import UTC, datetime

import pytest
from voa_core.lifecycle import (
    CALL_DOC_PHI_FIELDS,
    DEFAULT_VM_DROP_VERDICTS,
    EVENT_TERMINAL_STATUS,
    NON_REACHING_OUTCOMES,
    REPORT_PROMOTABLE,
    TERMINAL_STATUSES,
    AmdVerdict,
    CallEventKind,
    VerdictAction,
    apply_amd_verdict,
    call_event_patch,
    call_leg_still_live,
    classify_completed,
    effective_call_patch,
    is_machine,
    late_verdict_promotes,
    merge_call_doc,
    normalize_amd_verdict,
    release_marks_amd_released,
    stale_status_dropped,
    strip_phi_for_call_doc,
    verdict_action,
)

NOW = datetime(2026, 7, 22, 18, 5, tzinfo=UTC)
T0 = "2026-07-22T18:00:00+00:00"
T1 = "2026-07-22T18:01:00+00:00"


# --- constants --------------------------------------------------------------


def test_terminal_and_promotable_sets():
    assert TERMINAL_STATUSES == {
        "success",
        "left_vmail",
        "rna",
        "busy",
        "interrupted",
        "failed",
        "canceled",
    }
    assert REPORT_PROMOTABLE == {"interrupted", "rna", "left_vmail", "busy"}
    assert REPORT_PROMOTABLE < TERMINAL_STATUSES
    # A report can still overrule a wrong machine verdict.
    assert "left_vmail" in REPORT_PROMOTABLE
    assert NON_REACHING_OUTCOMES == {
        "unable_to_reach_wrong_person",
        "identity_verification_failed",
        "consent_declined",
    }


def test_busy_and_canceled_are_distinct():
    assert EVENT_TERMINAL_STATUS[CallEventKind.BUSY] == "busy"
    assert EVENT_TERMINAL_STATUS[CallEventKind.CANCELED] == "canceled"
    assert EVENT_TERMINAL_STATUS[CallEventKind.NO_ANSWER] == "rna"


# --- merge ----------------------------------------------------------------


def test_first_write_creates_doc_with_updated_at():
    doc = merge_call_doc(None, {"ringing_at": T0, "last_provider_status": "ringing"}, now=NOW)
    assert doc == {
        "ringing_at": T0,
        "last_provider_status": "ringing",
        "updated_at": NOW.isoformat(),
    }


def test_duplicate_ringing_is_a_noop():
    assert merge_call_doc({"ringing_at": T0}, {"ringing_at": T1}, now=NOW) is None


@pytest.mark.parametrize("field", ["ringing_at", "answered_at", "ended_at"])
def test_timestamps_are_set_if_absent(field):
    doc = merge_call_doc({field: T0}, {field: T1, "x": 1}, now=NOW)
    assert doc[field] == T0 and doc["x"] == 1


def test_success_not_overwritten_by_stale_completed_but_telemetry_kept():
    existing = {"call_status": "success", "answered_at": T0}
    doc = merge_call_doc(existing, {"call_status": "interrupted", "ended_at": T1}, now=NOW)
    assert doc["call_status"] == "success"
    assert doc["ended_at"] == T1
    assert stale_status_dropped(existing, {"call_status": "interrupted"})


def test_terminal_not_demoted_success():
    doc = merge_call_doc(
        {"call_status": "success"}, {"call_status": "rna", "ended_at": T1}, now=NOW
    )
    assert doc["call_status"] == "success" and doc["ended_at"] == T1


def test_terminal_not_demoted_left_vmail_is_no_write():
    assert (
        merge_call_doc({"call_status": "left_vmail"}, {"call_status": "interrupted"}, now=NOW)
        is None
    )


def test_completed_then_report_promotes_to_left_vmail():
    existing = {"call_status": "interrupted", "answered_at": T0}
    patch = {"call_status": "left_vmail", "outcome": "voicemail_left"}
    assert merge_call_doc(existing, patch, now=NOW, from_report=True)["call_status"] == "left_vmail"


@pytest.mark.parametrize("current", sorted(REPORT_PROMOTABLE))
def test_report_promotes_every_promotable_status_to_success(current):
    patch = {"call_status": "success", "outcome": "questionnaire_completed"}
    doc = merge_call_doc({"call_status": current}, patch, now=NOW, from_report=True)
    assert doc["call_status"] == "success"


def test_promotion_needs_from_report():
    doc = merge_call_doc({"call_status": "rna"}, {"call_status": "success"}, now=NOW)
    assert doc is None


def test_report_cannot_promote_to_anything_but_success_or_left_vmail():
    p = effective_call_patch({"call_status": "rna"}, {"call_status": "failed"}, from_report=True)
    assert "call_status" not in p


def test_report_on_non_promotable_terminal_keeps_enrichment():
    existing = {"call_status": "failed"}
    patch = {"call_status": "success", "outcome": "questionnaire_completed", "follow_up": "none"}
    doc = merge_call_doc(existing, patch, now=NOW, from_report=True)
    assert doc["call_status"] == "failed"
    assert doc["outcome"] == "questionnaire_completed"


def test_non_terminal_status_is_overwritten():
    doc = merge_call_doc({"call_status": "dialing"}, {"call_status": "rna"}, now=NOW)
    assert doc["call_status"] == "rna"


def test_merge_does_not_mutate_inputs():
    existing = {"call_status": "success"}
    patch = {"call_status": "rna", "ended_at": T1}
    merge_call_doc(existing, patch, now=NOW)
    assert existing == {"call_status": "success"}
    assert patch == {"call_status": "rna", "ended_at": T1}


def test_naive_now_is_rejected():
    with pytest.raises(ValueError):
        merge_call_doc(None, {"x": 1}, now=datetime(2026, 1, 1))


# --- C-2: DOB never on the call doc ---------------------------------------------


def test_strip_phi_removes_dob_and_keeps_the_rest():
    ctx = {
        "patient_name": "Pat Example",
        "patient_id": "PT-1",
        "patient_dob": "1950-03-15",
        "is_diabetic": True,
        "operation_name": "Knee arthroscopy",
    }
    out = strip_phi_for_call_doc(ctx)
    assert "patient_dob" not in out
    assert out["patient_name"] == "Pat Example" and out["is_diabetic"] is True
    assert ctx["patient_dob"] == "1950-03-15"  # input untouched
    assert strip_phi_for_call_doc(None) == {}
    assert "patient_dob" in CALL_DOC_PHI_FIELDS


def test_merge_never_writes_a_dob():
    patch = {
        "call_status": "dialing",
        "patient_dob": "1950-03-15",
        "patient_context": {"patient_id": "PT-1", "patient_dob": "1950-03-15"},
    }
    doc = merge_call_doc(None, patch, now=NOW)
    assert "patient_dob" not in doc
    assert doc["patient_context"] == {"patient_id": "PT-1"}
    assert "1950" not in repr(doc)


def test_merge_scrubs_a_legacy_dob_already_on_the_doc():
    existing = {"patient_context": {"patient_id": "PT-1", "patient_dob": "1950-03-15"}}
    doc = merge_call_doc(existing, {"call_status": "dialing"}, now=NOW)
    assert doc["patient_context"] == {"patient_id": "PT-1"}


def test_a_dob_only_patch_writes_nothing():
    assert (
        merge_call_doc({"call_status": "dialing"}, {"patient_dob": "1950-03-15"}, now=NOW) is None
    )


# --- provider events ------------------------------------------------------------


def test_ringing_sets_timestamp():
    p = call_event_patch("ringing", now=NOW, provider_status="ringing")
    assert p == {"last_provider_status": "ringing", "ringing_at": NOW.isoformat()}


def test_answered_sets_answered_at_only():
    p = call_event_patch(CallEventKind.ANSWERED, now=NOW, provider_status="in-progress")
    assert p["answered_at"] == NOW.isoformat()
    assert p["last_provider_status"] == "in-progress"
    assert "ringing_at" not in p and "call_status" not in p


def test_queued_records_only_the_raw_status():
    assert call_event_patch("queued", now=NOW) == {"last_provider_status": "queued"}
    assert call_event_patch("unknown", now=NOW, provider_status="weird") == {
        "last_provider_status": "weird"
    }


def test_completed_parses_duration_and_classifies():
    p = call_event_patch("completed", now=NOW, current={"answered_at": T0}, duration_seconds="152")
    assert p["duration_seconds"] == 152
    assert p["call_status"] == "interrupted"
    assert p["ended_at"] == NOW.isoformat()


def test_completed_ignores_a_junk_duration():
    p = call_event_patch("completed", now=NOW, duration_seconds="12s")
    assert "duration_seconds" not in p
    assert p["call_status"] == "rna"


def test_completed_with_verdict_in_the_same_event_is_voicemail():
    # The verdict is not on the doc yet; the event's own copy must be used.
    p = call_event_patch(
        "completed", now=NOW, current={"answered_at": T0}, answered_by="machine_end_beep"
    )
    assert p["call_status"] == "left_vmail"
    assert p["answered_by"] == "machine_end_beep"
    assert p["amd_at"] == NOW.isoformat()


def test_failed_captures_error_fields():
    p = call_event_patch("failed", now=NOW, failure_code=32009, failure_message="Dial call failed")
    assert p["call_status"] == "failed"
    assert p["failure_code"] == "32009"
    assert p["failure_message"] == "Dial call failed"
    assert "ended_at" in p


@pytest.mark.parametrize(
    "kind,status", [("busy", "busy"), ("canceled", "canceled"), ("no_answer", "rna")]
)
def test_terminal_events(kind, status):
    p = call_event_patch(kind, now=NOW, failure_code="486")
    assert p["call_status"] == status and "ended_at" in p
    assert "failure_code" not in p  # only failed carries failure details


def test_bad_event_kind_raises():
    with pytest.raises(ValueError):
        call_event_patch("exploded", now=NOW)


# --- classify_completed ---------------------------------------------------------


def test_classify_no_answer_no_report_is_rna():
    assert classify_completed({}) == "rna"
    assert classify_completed(None) == "rna"


def test_classify_answered_no_report_is_interrupted():
    assert classify_completed({"answered_at": T0}) == "interrupted"


def test_classify_report_outcomes():
    assert (
        classify_completed({"outcome": "questionnaire_completed", "answered_at": T0}) == "success"
    )
    assert classify_completed({"outcome": "voicemail_left"}) == "left_vmail"


@pytest.mark.parametrize(
    "answered_by",
    ["machine_start", "machine_end_beep", "machine_end_silence", "machine_end_other", "fax"],
)
def test_classify_machine_verdicts_as_voicemail(answered_by):
    assert classify_completed({"answered_at": T0, "answered_by": answered_by}) == "left_vmail"


@pytest.mark.parametrize("answered_by", ["human", "unknown", "banana", ""])
def test_classify_non_machine_verdicts_fall_through(answered_by):
    assert classify_completed({"answered_at": T0, "answered_by": answered_by}) == "interrupted"


def test_classify_report_wins_over_amd():
    doc = {"answered_at": T0, "answered_by": "machine_start", "outcome": "questionnaire_completed"}
    assert classify_completed(doc) == "success"


def test_classify_hint_beats_the_stored_doc():
    doc = {"answered_at": T0}
    assert classify_completed(doc, "machine_end_beep") == "left_vmail"
    assert classify_completed(doc, "human") == "interrupted"
    assert classify_completed({"answered_at": T0, "answered_by": "machine_start"}, "human") == (
        "interrupted"
    )


def test_classify_respects_a_released_machine_verdict():
    assert classify_completed({"answered_by": "machine_end_beep"}) == "left_vmail"
    released = {"answered_by": "machine_end_silence", "amd_released": True, "answered_at": T0}
    assert classify_completed(released) == "interrupted"


def test_classify_accepts_normalised_verdicts():
    assert classify_completed({"answered_at": T0, "answered_by": "machine_beep"}) == "left_vmail"


# --- AMD normalisation and action -------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("human", AmdVerdict.HUMAN),
        ("machine_end_beep", AmdVerdict.MACHINE_BEEP),
        ("machine_end_silence", AmdVerdict.MACHINE_AMBIGUOUS),
        ("machine_end_other", AmdVerdict.MACHINE_AMBIGUOUS),
        ("machine_start", AmdVerdict.MACHINE_START),
        ("fax", AmdVerdict.FAX),
        ("unknown", AmdVerdict.UNKNOWN),
        ("  HUMAN ", AmdVerdict.HUMAN),
        ("machine_beep", AmdVerdict.MACHINE_BEEP),  # idempotent
        ("machine_ambiguous", AmdVerdict.MACHINE_AMBIGUOUS),
        (AmdVerdict.FAX, AmdVerdict.FAX),
        ("machine_something_new", AmdVerdict.MACHINE_START),
        ("banana", AmdVerdict.UNKNOWN),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_normalize_amd_verdict(raw, expected):
    assert normalize_amd_verdict(raw) == expected


def test_is_machine():
    assert all(is_machine(v) for v in ("machine_start", "machine_end_beep", "fax"))
    assert not any(is_machine(v) for v in ("human", "unknown", "banana", "", None))


@pytest.mark.parametrize(
    "verdict,expected",
    [
        ("human", VerdictAction.RELEASE),
        ("unknown", VerdictAction.RELEASE),
        ("machine_end_beep", VerdictAction.DROP),
        # Also what a human who talks past the speech threshold then pauses looks like.
        ("machine_end_silence", VerdictAction.RELEASE),
        ("machine_end_other", VerdictAction.RELEASE),
        ("machine_start", VerdictAction.RELEASE),
        ("fax", VerdictAction.HANGUP),
        ("", VerdictAction.HOLD),
        ("   ", VerdictAction.HOLD),
        (None, VerdictAction.HOLD),
        ("banana", VerdictAction.RELEASE),  # junk is never a machine
    ],
)
def test_verdict_action_mapping(verdict, expected):
    assert verdict_action(verdict, True, DEFAULT_VM_DROP_VERDICTS) == expected


def test_drop_is_disabled_by_the_kill_switch():
    assert verdict_action("machine_end_beep", False) == VerdictAction.RELEASE
    # Fax hangs up regardless: there is nobody to talk to.
    assert verdict_action("fax", False) == VerdictAction.HANGUP


def test_drop_verdicts_can_be_widened_with_either_spelling():
    assert verdict_action("machine_end_silence", True, {"machine_ambiguous"}) == VerdictAction.DROP
    assert (
        verdict_action("machine_end_silence", True, {"machine_end_beep", "machine_end_silence"})
        == VerdictAction.DROP
    )


def test_amd_released_only_for_ambiguous_verdicts():
    assert release_marks_amd_released("machine_end_silence")
    assert release_marks_amd_released("machine_end_other")
    for v in ("machine_start", "machine_end_beep", "human", "unknown", "fax", ""):
        assert not release_marks_amd_released(v)


# --- late verdicts ---------------------------------------------------------------


def test_late_machine_verdict_promotes_interrupted_to_voicemail():
    doc, promoted = apply_amd_verdict({"call_status": "interrupted"}, "machine_start", now=NOW)
    assert promoted
    assert doc["call_status"] == "left_vmail"
    assert doc["answered_by"] == "machine_start"
    assert doc["amd_at"] == NOW.isoformat() == doc["updated_at"]


def test_late_verdict_never_overwrites_a_completed_questionnaire():
    existing = {"call_status": "interrupted", "outcome": "questionnaire_completed"}
    doc, promoted = apply_amd_verdict(existing, "machine_start", now=NOW)
    assert not promoted
    assert doc["call_status"] == "interrupted"
    assert doc["answered_by"] == "machine_start"


def test_late_promotion_respects_a_released_verdict():
    assert late_verdict_promotes({"call_status": "interrupted"}, "machine_end_silence")
    assert not late_verdict_promotes(
        {"call_status": "interrupted", "amd_released": True}, "machine_end_silence"
    )


@pytest.mark.parametrize("status", ["rna", "success", "left_vmail", "busy", None])
def test_only_interrupted_is_promoted(status):
    assert not late_verdict_promotes({"call_status": status}, "machine_end_beep")


def test_human_verdict_never_promotes():
    assert not late_verdict_promotes({"call_status": "interrupted"}, "human")


def test_amd_verdict_on_a_missing_doc_creates_it():
    doc, promoted = apply_amd_verdict(None, "human", now=NOW)
    assert not promoted and doc["answered_by"] == "human" and "call_status" not in doc


# --- liveness ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "doc,expected",
    [
        ({"call_status": "success"}, False),
        ({"call_status": "rna"}, False),
        ({}, True),  # still in flight
        ({"call_status": "ringing"}, True),
        (None, False),  # no call doc at all
    ],
)
def test_call_leg_liveness(doc, expected):
    assert call_leg_still_live(doc) is expected


def test_liveness_fails_safe_on_a_read_error():
    assert call_leg_still_live(None, read_failed=True) is True


@pytest.mark.parametrize(
    "outcome", ["identity_verification_failed", "unable_to_reach_wrong_person", "consent_declined"]
)
def test_non_reaching_report_before_completed_is_not_success(outcome):
    # Real-call regression: the report lands first, then Twilio's "completed".
    doc = {"answered_at": "2026-10-17T20:30:00+00:00", "outcome": outcome, "answered_by": "human"}
    assert classify_completed(doc) == "interrupted"
    now = datetime(2026, 10, 17, 20, 31, tzinfo=UTC)
    patch = call_event_patch(CallEventKind.COMPLETED, now=now, current=doc)
    assert patch["call_status"] == "interrupted"
