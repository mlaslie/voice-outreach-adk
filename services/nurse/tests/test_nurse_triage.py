"""Triage: which calls need a callback, which answers are abnormal, and the verdict
(precedence from voa_core.reports, wording in charting language)."""

import pytest
from voa_core import reports
from voa_nurse import triage

CALL = {
    "doc_id": "CA" + "1" * 32,
    "patient_id": "P-1001",
    "initiated_at": "2026-10-07T14:00:00+00:00",
    "call_status": "success",
    "outcome": "questionnaire_completed",
    "follow_up": "urgent_same_day",
    "follow_up_reason": "Fever and pain 8/10.",
    "dob_verification_result": "verified",
    "patient_context": {"is_diabetic": True},
    "survey_responses": {
        "pain_present": "yes",
        "pain_severity": 8,
        "pain_trend": "worse",
        "fever_reported": "yes",
        "incision_concerns": "no",
        "blood_sugar_status": "high",
        "overall_feeling": "Pretty rough",
    },
}
LATER_CLEAN = {
    **CALL,
    "doc_id": "CA" + "2" * 32,
    "initiated_at": "2026-10-08T14:00:00+00:00",
    "follow_up": "none",
    "follow_up_reason": "",
}


def test_a_clean_redial_clears_the_patient():
    assert triage.follow_up_calls([CALL]) == [CALL]
    assert triage.follow_up_calls([CALL, LATER_CLEAN]) == []


def test_findings_flag_the_dangerous_answers():
    levels = {f["label"]: f["level"] for f in triage.findings_for(CALL)}
    assert levels["Pain"] == "alert"
    assert levels["Fever"] == "alert"
    assert levels["Incision / site"] == "normal"
    assert levels["Blood glucose"] == "alert"
    assert triage.verdict_for(CALL)["headline"] == "Urgent: RN callback today"


def test_an_unanswered_redial_does_not_hide_an_urgent_patient():
    survey = {
        "doc_id": "CAs",
        "patient_id": "P-1",
        "initiated_at": "2026-10-16T15:50:00+00:00",
        "call_status": "success",
        "channel": "sms",
        "follow_up": "urgent_same_day",
        "follow_up_reason": "Fever and high blood sugar.",
    }
    vm = {
        "doc_id": "CAv",
        "patient_id": "P-1",
        "initiated_at": "2026-10-16T17:50:00+00:00",
        "call_status": "left_vmail",
        "outcome": "voicemail_left",
    }
    assert [c["doc_id"] for c in triage.follow_up_calls([survey, vm])] == ["CAs"]
    vm_report = {
        **vm,
        "doc_id": "CAr",
        "initiated_at": "2026-10-16T21:08:00+00:00",
        "follow_up": "none",
        "follow_up_reason": "Voicemail left; no patient contact.",
    }
    assert [c["doc_id"] for c in triage.follow_up_calls([survey, vm, vm_report])] == ["CAs"]
    clean = {
        **vm,
        "doc_id": "CAc",
        "call_status": "success",
        "outcome": "questionnaire_completed",
        "follow_up": "none",
    }
    assert triage.follow_up_calls([survey, clean]) == []
    nurse = {
        **vm_report,
        "doc_id": "CAn",
        "follow_up_override": {"to": "none", "reason": "Spoke with patient."},
    }
    assert triage.follow_up_calls([survey, nurse]) == []


def test_calls_without_a_timestamp_do_not_break_the_lists():
    """Review A-2: initiated_at null used to raise TypeError."""
    broken = {**CALL, "doc_id": "CA" + "e" * 32, "patient_id": "P-9", "initiated_at": None}
    garbled = {**CALL, "doc_id": "CA" + "f" * 32, "patient_id": "P-8", "initiated_at": "yesterday"}
    numeric = {**CALL, "doc_id": "CA" + "d" * 32, "patient_id": "P-7", "initiated_at": 12345}
    assert len(triage.follow_up_calls([CALL, broken, garbled, numeric])) == 4
    assert triage.call_time(broken) is None and triage.call_time(garbled) is None
    assert triage.call_time(numeric) is None


def test_verdict_precedence_is_the_backends():
    """Review C-12, via voa_core.reports: an action tier wins over an incomplete
    outcome, and identity_verification_failed never renders as cleared."""
    escalated = {**CALL, "outcome": "consent_declined", "follow_up": "emergency"}
    assert triage.verdict_for(escalated)["tone"] == "emergency"
    assert triage.verdict_for(escalated)["headline"] == "EMERGENT: 911 advised"
    wrong_person = {
        **CALL,
        "outcome": "identity_verification_failed",
        "follow_up": "none",
        "follow_up_reason": "",
    }
    v = triage.verdict_for(wrong_person)
    assert v["tone"] == "incomplete" and v["headline"] == "Assessment not completed"
    for c in (CALL, escalated, wrong_person, {**CALL, "follow_up": "none"}):
        assert triage.verdict_for(c)["tone"] == reports.verdict_for(c).tone


def test_verdict_wording_is_clinical():
    assert (
        triage.verdict_for({**CALL, "follow_up_reason": ""})["reason"]
        == "Needs a same-day RN callback."
    )
    vm = {"call_status": "left_vmail", "outcome": "voicemail_left"}
    assert triage.verdict_for(vm)["reason"] == "Left voicemail; patient not reached."
    assert triage.verdict_for({"call_status": "failed"})["headline"] == "No disposition recorded"
    assert triage.verdict_for({})["headline"] == "Call in progress"


@pytest.mark.parametrize(
    "words,level",
    [
        ("very poor", "watch"),
        ("Pretty rough", "watch"),
        ("terrible", "alert"),
        ("great", "normal"),
        ("fair", "unknown"),
    ],
)
def test_the_patients_own_words_are_not_always_green(words, level):
    f = next(
        x
        for x in triage.findings_for({**CALL, "survey_responses": {"overall_feeling": words}})
        if x["label"] == "Patient statement"
    )
    assert f["level"] == level


def test_pain_severity_none_or_bool_is_not_a_rating():
    for sev in (None, True, "n/a"):
        r = {"pain_present": "yes", "pain_severity": sev}
        f = next(x for x in triage.findings_for({"survey_responses": r}) if x["label"] == "Pain")
        assert f["value"] == "Reports pain"
