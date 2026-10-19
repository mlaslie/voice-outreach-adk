"""Review and sign-off rules (ported from the old proxy's sign-off, B-5, B-6/D-1, B-7
and P0-review tests)."""

from datetime import UTC, datetime

from voa_core import lifecycle, review

NOW = "2026-10-18T15:00:00+00:00"
NOW_DT = datetime(2026, 10, 18, 15, tzinfo=UTC)
PHONE = "+12025550109"


def _assessed(**kw):
    return {"outcome": "questionnaire_completed", **kw}


# --- predicates -------------------------------------------------------------------------


def test_what_counts_as_an_assessment():
    assert review.is_assessment(_assessed(follow_up="none"))
    assert review.is_assessment({"follow_up": "urgent_same_day"})
    assert review.is_assessment({"follow_up": "none", "follow_up_override": {"to": "none"}})
    assert not review.is_assessment({"outcome": "voicemail_left", "follow_up": "none"})
    assert not review.is_assessment({})


def test_closed_reviews_and_exports():
    assert review.review_closed({"review": {"status": "signed_off"}})
    assert review.review_closed({"review": {"status": "superseded"}})
    assert not review.review_closed({"review": {"status": "reopened"}})
    assert review.has_export({"status": "signed_off"})
    assert review.has_export({"status": "superseded", "resolved": True})
    assert not review.has_export({"status": "superseded"})
    assert not review.has_export(None)


def test_open_follow_ups_stay_open_until_a_nurse_closes_them():
    rows = [
        {"doc_id": "a", "follow_up": "urgent_same_day", "initiated_at": "2026-01-01"},
        {"doc_id": "b", "follow_up": "emergency", "initiated_at": "2026-01-02"},
        {"doc_id": "c", "follow_up": "urgent_same_day", "review": {"status": "signed_off"}},
        {"doc_id": "d", "follow_up": "none"},
        {"doc_id": "e", "follow_up": "routine_next_business_day", "review": {"status": "reopened"}},
    ]
    open_ = review.sort_open_follow_ups(r for r in rows if review.is_open_follow_up(r))
    assert [r["doc_id"] for r in open_] == ["b", "a", "e"]


def test_one_patient_is_one_mrn_and_phone_only_matches_calls_without_one():
    """B-6: matching an MRN-less call by phone closed another patient's emergency."""
    assert review.same_patient({"patient_id": "P1"}, {"patient_id": "P1"})
    assert not review.same_patient({"patient_id": "P1", "to": PHONE}, {"to": PHONE})
    assert review.same_patient({"to": PHONE}, {"to": PHONE})
    assert not review.same_patient({}, {})
    assert review.patient_filter({"patient_id": "P1", "to": PHONE}) == ("patient_id", "P1")
    assert review.patient_filter({"to": PHONE}) == ("to", PHONE)
    assert review.patient_filter({}) is None


def test_a_signer_must_be_verified():
    """B-7: a sign-off is never filed as "unverified"."""
    assert review.verified_signer("rn@example.org") == "rn@example.org"
    for bad in (None, "", "  ", "dashboard", "nurse-assistant (user not verified)"):
        assert review.verified_signer(bad) is None


# --- sign-off ---------------------------------------------------------------------------


def _plan(call_id, doc, others=(), **kw):
    kw.setdefault("signer", "rn@example.org")
    return review.plan_sign_off(call_id, doc, list(others), now=NOW, **kw)


def test_a_cleared_call_signs_off_and_exports_without_a_task():
    doc = _assessed(
        patient_id="P-1",
        follow_up="none",
        survey_responses={"pain_present": "no", "fever_reported": "no", "_raw": {"x": 1}},
    )
    p = _plan("C1", doc)
    assert p.status == "signed_off" and p.review["resolved"] is False
    assert p.review["by"] == "rn@example.org"
    ehr = p.exports["C1"]
    assert ehr["status"] == "pending_export"
    assert ehr["questionnaire_response"]["resourceType"] == "QuestionnaireResponse"
    assert {i["linkId"] for i in ehr["questionnaire_response"]["item"]} == {
        "pain_present",
        "fever_reported",
    }
    assert "task" not in ehr


def test_refusals():
    assert _plan("C1", None).status == "not_found"
    assert _plan("C1", {"outcome": "voicemail_left", "follow_up": "none"}).status == (
        "not_an_assessment"
    )
    signed = _assessed(follow_up="none", review={"status": "signed_off"})
    assert _plan("C1", signed).status == "already_signed_off"
    changed = _plan("C1", _assessed(follow_up="none"), expected_follow_up="urgent_same_day")
    assert changed.status == "changed" and changed.follow_up == "none"


def test_resolving_a_follow_up_needs_an_outcome_note_and_exports_a_task():
    doc = _assessed(patient_id="P-2", follow_up="urgent_same_day")
    for note in (None, "", "ok", "   ok  "):
        assert _plan("C2", doc, note=note).status == "note_required"
    assert _plan("C2", doc, bulk=True).status == "needs_individual_review"
    p = _plan("C2", doc, note="Spoke with   patient; started antibiotics via PCP.")
    assert p.status == "signed_off" and p.review["resolved"] is True
    assert p.review["note"] == "Spoke with patient; started antibiotics via PCP."
    task = p.exports["C2"]["task"]
    assert task["resourceType"] == "Task" and task["note"].startswith("Spoke with patient")


def _history():
    base = {"patient_id": "P-9", "outcome": "questionnaire_completed"}
    return {
        "OLD_CLEAR": {**base, "initiated_at": "2026-08-21T21:21:00Z", "follow_up": "none"},
        "OLD_URGENT": {
            **base,
            "initiated_at": "2026-08-21T23:10:00Z",
            "follow_up": "urgent_same_day",
        },
        "LATEST": {**base, "initiated_at": "2026-10-16T16:57:00Z", "follow_up": "urgent_same_day"},
        # A voicemail after it is not an assessment, so it does not replace the latest.
        "VM": {
            **base,
            "initiated_at": "2026-10-17T10:00:00Z",
            "outcome": "voicemail_left",
            "follow_up": "none",
        },
        "OTHER_PATIENT": {
            **base,
            "patient_id": "P-1",
            "initiated_at": "2026-10-18T10:00:00Z",
            "follow_up": "emergency",
        },
    }


def test_an_earlier_report_cannot_be_signed_off():
    h = _history()
    p = _plan("OLD_CLEAR", h["OLD_CLEAR"], h.items())
    assert p.status == "superseded_by_later" and p.latest == "LATEST"


def test_signing_the_latest_closes_and_resolves_the_earlier_ones():
    h = _history()
    p = _plan("LATEST", h["LATEST"], h.items(), note="Called back; afebrile now, BG 140.")
    assert p.status == "signed_off"
    assert sorted(p.superseded) == ["OLD_CLEAR", "OLD_URGENT"]
    assert p.earlier_needs == ["OLD_URGENT"] and p.review["also_resolved"] == ["OLD_URGENT"]
    for sid in ("OLD_CLEAR", "OLD_URGENT"):
        c = p.closes[sid]
        assert c["status"] == "superseded" and c["superseded_by"] == "LATEST"
    # D-1: the earlier urgent report is resolved with the nurse's note and goes to
    # the EHR as closed; the earlier cleared one is covered by the latest.
    assert p.closes["OLD_URGENT"]["resolved"] is True
    assert "afebrile" in p.closes["OLD_URGENT"]["note"]
    assert set(p.exports) == {"LATEST", "OLD_URGENT"}
    assert p.exports["OLD_URGENT"]["closed_with"] == "LATEST"


def test_a_later_clean_report_needs_a_note_while_an_earlier_emergency_is_open():
    base = {"patient_id": "P-8", "outcome": "questionnaire_completed"}
    rows = {
        "EMERG": {**base, "initiated_at": "2026-10-10T10:00:00Z", "follow_up": "emergency"},
        "CLEAR": {**base, "initiated_at": "2026-10-17T10:00:00Z", "follow_up": "none"},
    }
    assert _plan("CLEAR", rows["CLEAR"], rows.items()).status == "note_required"
    assert _plan("CLEAR", rows["CLEAR"], rows.items(), bulk=True).status == (
        "needs_individual_review"
    )
    p = _plan("CLEAR", rows["CLEAR"], rows.items(), note="Spoke with Ann: ED visit, home now.")
    assert p.status == "signed_off" and p.earlier_needs == ["EMERG"]
    assert p.closes["EMERG"]["resolved"] is True and set(p.exports) == {"CLEAR", "EMERG"}


def test_a_call_without_an_mrn_never_closes_a_patient_with_one():
    rows = {
        "MRN": {
            "patient_id": "P-111",
            "to": PHONE,
            "initiated_at": "2026-10-10T10:00:00Z",
            "outcome": "questionnaire_completed",
            "follow_up": "emergency",
        },
        "NOMRN": {
            "to": PHONE,
            "initiated_at": "2026-10-17T10:00:00Z",
            "outcome": "questionnaire_completed",
            "follow_up": "none",
        },
    }
    p = _plan("NOMRN", rows["NOMRN"], rows.items())
    assert p.status == "signed_off" and p.superseded == []


def test_a_reopened_earlier_report_is_not_blocked_by_a_closed_later_one():
    """No deadlock (P0 review): only an OPEN later report replaces a report."""
    base = {"patient_id": "P-1", "outcome": "questionnaire_completed"}
    rows = {
        "E1": {
            **base,
            "initiated_at": "2026-10-10T10:00:00Z",
            "follow_up": "urgent_same_day",
            "review": {"status": "reopened"},
        },
        "E2": {
            **base,
            "initiated_at": "2026-10-17T10:00:00Z",
            "follow_up": "none",
            "review": {"status": "signed_off"},
        },
    }
    p = _plan("E1", rows["E1"], rows.items(), note="Called back; afebrile now.")
    assert p.status == "signed_off" and p.superseded == []


# --- reopening --------------------------------------------------------------------------


def test_a_tier_change_reopens_a_closed_review():
    patch, withdraw = review.reopen_after_tier_change(
        {"review": {"status": "signed_off", "by": "rn"}}, now=NOW, by="rn2@example.org"
    )
    assert patch["review"]["status"] == "reopened" and patch["review"]["reopened_by"] == (
        "rn2@example.org"
    )
    assert patch["review_history"][-1] == patch["review"] and withdraw is True
    # A superseded report is reopened too; only a resolved one had an export.
    patch, withdraw = review.reopen_after_tier_change(
        {"review": {"status": "superseded"}}, now=NOW, by="rn"
    )
    assert patch["review"]["status"] == "reopened" and withdraw is False
    _, withdraw = review.reopen_after_tier_change(
        {"review": {"status": "superseded", "resolved": True}}, now=NOW, by="rn"
    )
    assert withdraw is True
    assert review.reopen_after_tier_change({}, now=NOW, by="rn") == ({}, False)


def test_a_late_emergency_reopens_a_signed_off_call():
    existing = _assessed(follow_up="none", review={"status": "signed_off", "by": "rn"})
    p, withdraw = review.merge_late_report(
        existing, _assessed(follow_up="emergency", follow_up_reason="Chest pain."), now=NOW
    )
    assert p["follow_up"] == "emergency" and withdraw is True
    assert p["review"]["status"] == "reopened" and p["review"]["reopened_by"] == "report"


def test_the_same_report_again_does_not_reopen():
    existing = _assessed(follow_up="none", review={"status": "signed_off"})
    p, withdraw = review.merge_late_report(existing, _assessed(follow_up="none"), now=NOW)
    assert "review" not in p and withdraw is False


def test_a_nurses_tier_stands_against_a_milder_report():
    existing = _assessed(follow_up="urgent_same_day", follow_up_override={"by": "rn"})
    p, _ = review.merge_late_report(
        existing, _assessed(follow_up="none", follow_up_reason="All fine."), now=NOW
    )
    assert "follow_up" not in p and "follow_up_reason" not in p
    assert p["agent_follow_up"] == "none" and "kept" in p["follow_up_history"][-1]["reason"]


def test_a_more_severe_report_escalates_and_clears_the_override():
    existing = _assessed(follow_up="routine_next_business_day", follow_up_override={"by": "rn"})
    p, _ = review.merge_late_report(existing, _assessed(follow_up="urgent_same_day"), now=NOW)
    assert p["follow_up"] == "urgent_same_day" and p["follow_up_override"] is None
    assert p["follow_up_history"][-1]["to"] == "urgent_same_day"


def test_a_milder_late_report_does_not_downgrade_a_signed_off_tier():
    existing = _assessed(
        follow_up="emergency", review={"status": "signed_off", "resolved": True, "note": "ED."}
    )
    p, withdraw = review.merge_late_report(
        existing, _assessed(follow_up="none", survey_responses={"pain_present": "no"}), now=NOW
    )
    assert "follow_up" not in p and p["agent_follow_up"] == "none"
    assert "kept" in p["follow_up_history"][-1]["reason"]
    assert p["review"]["status"] == "reopened" and withdraw is True  # new answers: look again


def test_lifecycle_report_merge_applies_the_late_report_rules():
    cur = {
        "call_status": "left_vmail",
        **_assessed(follow_up="none", review={"status": "signed_off", "by": "rn"}),
    }
    new = lifecycle.merge_call_doc(
        cur,
        {"call_status": "success", **_assessed(follow_up="emergency")},
        now=NOW_DT,
        from_report=True,
    )
    assert new["follow_up"] == "emergency" and new["review"]["status"] == "reopened"
    assert new["review"]["export_withdrawn"] is True
    # A non-report write (a provider callback) never touches the review.
    same = lifecycle.merge_call_doc(cur, {"last_provider_status": "completed"}, now=NOW_DT)
    assert same["review"]["status"] == "signed_off"
