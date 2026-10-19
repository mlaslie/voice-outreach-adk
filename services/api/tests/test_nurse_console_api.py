"""Backend the nurse console needs, ported from the old proxy (18-19 Oct 2026):

* sign-off and review (A-1.6..A-1.8, B-5, B-6/D-1, B-7) and open follow-ups;
* B-2: a nurse's cancel stops the dial (and a run cancel stops its tasks);
* B-8: calling hours enforced at dial time, with carry-over at closing time;
* any patient can be put in a call run;
* the fixes from the code review of that work.

Fictional data only. MemoryStore + MemoryTaskQueue (tasks run by POSTing them, as
Cloud Tasks would) + FakeVoice + a fixed clock.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from voa_api.app import create_app
from voa_api.context import Api
from voa_core.config import load_config
from voa_core.store import MemoryStore, now_iso
from voa_core.tasks import MemoryTaskQueue
from voa_providers.fake import FakeVoice

NOW = datetime(2026, 3, 3, 15, 0, tzinfo=UTC)  # Tuesday 10:00 in New York: inside hours
NIGHT = datetime(2026, 3, 4, 3, 30, tzinfo=UTC)  # Tuesday 22:30 in New York: outside
PHONE = "+12025550101"
PHONE2 = "+12025550102"
NURSE = {"X-Acting-User": "rn@example.org"}
ENTRY = {
    "phone": PHONE,
    "patient_name": "Jordan Rivera",
    "patient_id": "P-001",
    "patient_dob": "1950-03-15",
}


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


def make(env="test"):
    cfg = load_config(
        environ={
            "VOA_ENV": env,
            "VOA__GCP__PROJECT_ID": "test-project",
            "VOA__SERVICES__GATEWAY_URL": "https://gw.example.org",
            "VOA__SERVICES__TASKS_INVOKER_SA": "invoker@example.iam.gserviceaccount.com",
            "VOA__SERVICES__UI_SA": "ui@example.iam.gserviceaccount.com",
            "VOA__DIALING__ALLOWLIST": f"{PHONE},{PHONE2}",
            "VOA__DIALING__MAX_CONCURRENT_CALLS": "5",
        }
    )
    clock = Clock(NOW)
    api = Api(cfg=cfg, store=MemoryStore(), tasks=MemoryTaskQueue(), voice=FakeVoice(), clock=clock)
    client = TestClient(create_app(cfg, api))
    client.__enter__()
    return client, api, clock


def run(coro):
    return asyncio.run(coro)


def drain(client, api, path="/tasks/dial"):
    out = []
    for t in [t for t in api.tasks.tasks if t["path"] == path]:
        api.tasks.tasks.remove(t)
        out.append(client.post(t["path"], json=t["payload"]))
    return out


def calls(api):
    return dict(run(api.store.query("calls")))


def outbox(api):
    return dict(run(api.store.query("ehr_outbox")))


def put(api, cid, doc):
    run(api.store.set("calls", cid, doc))


def entry(api, eid):
    return run(api.store.get("call_list", eid))


def add(client, **kw):
    return client.post("/api/call-list", json={**ENTRY, **kw}).json()["entry_id"]


# =========================================================================== sign-off

S_CLEAR, S_URGENT, S_VM, S_SMS = ("CA" + c * 32 for c in "1234")


@pytest.fixture()
def four():
    client, api, _ = make()
    put(
        api,
        S_CLEAR,
        {
            "patient_id": "P-1",
            "patient_name": "Gregory Tan",
            "outcome": "questionnaire_completed",
            "follow_up": "none",
            "call_status": "success",
            "survey_responses": {"pain_present": "no", "fever_reported": "no", "_raw": {"x": 1}},
        },
    )
    put(
        api,
        S_URGENT,
        {
            "patient_id": "P-2",
            "outcome": "questionnaire_completed",
            "follow_up": "urgent_same_day",
            "follow_up_reason": "Fever and incision redness.",
        },
    )
    put(api, S_VM, {"patient_id": "P-3", "outcome": "voicemail_left", "follow_up": "none"})
    put(
        api,
        S_SMS,
        {
            "patient_id": "P-4",
            "outcome": "questionnaire_completed",
            "follow_up": "informational",
            "channel": "sms",
        },
    )
    return client, api


def test_a_cleared_call_signs_off_and_reaches_the_outbox(four):
    client, api = four
    r = client.post(f"/api/calls/{S_CLEAR}/sign-off", json={}, headers=NURSE)
    assert r.status_code == 200, r.text
    review = calls(api)[S_CLEAR]["review"]
    assert review["status"] == "signed_off" and review["by"] == "rn@example.org"
    assert review["resolved"] is False and r.json()["review"] == review
    ehr = outbox(api)[S_CLEAR]
    assert ehr["status"] == "pending_export"
    assert {i["linkId"] for i in ehr["questionnaire_response"]["item"]} == {
        "pain_present",
        "fever_reported",
    }
    assert "task" not in ehr
    # Shown to the console like any call field.
    assert client.get(f"/api/calls/{S_CLEAR}").json()["review"]["status"] == "signed_off"


def test_twice_is_refused(four):
    client, _ = four
    client.post(f"/api/calls/{S_CLEAR}/sign-off", json={}, headers=NURSE)
    assert client.post(f"/api/calls/{S_CLEAR}/sign-off", json={}, headers=NURSE).status_code == 409


def test_a_voicemail_has_nothing_to_sign_off(four):
    client, api = four
    r = client.post(f"/api/calls/{S_VM}/sign-off", json={}, headers=NURSE)
    assert r.status_code == 409 and "no assessment" in r.json()["detail"]
    assert S_VM not in outbox(api)
    assert (
        client.post(f"/api/calls/{'CA' + '9' * 32}/sign-off", json={}, headers=NURSE).status_code
        == 404
    )
    assert client.post("/api/calls/not-a-call/sign-off", json={}, headers=NURSE).status_code == 400


def test_resolving_a_follow_up_needs_an_outcome_note(four):
    client, api = four
    for note in (None, "", "ok"):
        r = client.post(f"/api/calls/{S_URGENT}/sign-off", json={"note": note}, headers=NURSE)
        assert r.status_code == 422
    assert "review" not in calls(api)[S_URGENT]
    r = client.post(
        f"/api/calls/{S_URGENT}/sign-off",
        json={"note": "Spoke with patient; started antibiotics via PCP."},
        headers=NURSE,
    )
    assert r.status_code == 200
    assert calls(api)[S_URGENT]["review"]["resolved"] is True
    task = outbox(api)[S_URGENT]["task"]
    assert task["resourceType"] == "Task" and task["note"].startswith("Spoke with patient")
    # Resolved: it leaves the open follow-ups.
    assert client.get("/api/follow-ups/open").json()["count"] == 0


def test_a_changed_tier_since_the_nurse_looked_is_refused(four):
    client, _ = four
    r = client.post(
        f"/api/calls/{S_CLEAR}/sign-off",
        json={"expected_follow_up": "urgent_same_day"},
        headers=NURSE,
    )
    assert r.status_code == 409 and "changed" in r.json()["detail"]


def test_a_tier_change_after_sign_off_reopens_it(four):
    client, api = four
    client.post(f"/api/calls/{S_CLEAR}/sign-off", json={}, headers=NURSE)
    r = client.post(
        f"/api/calls/{S_CLEAR}/follow-up",
        json={"follow_up": "urgent_same_day", "reason": "Patient called back with fever."},
        headers={"X-Acting-User": "rn2@example.org"},
    )
    assert r.status_code == 200, r.text
    review = calls(api)[S_CLEAR]["review"]
    assert review["status"] == "reopened" and review["reopened_by"] == "rn2@example.org"
    assert outbox(api)[S_CLEAR]["status"] == "withdrawn"
    # It can be signed off again, now as a follow-up, so with a note.
    r = client.post(
        f"/api/calls/{S_CLEAR}/sign-off", json={"note": "Called back; afebrile now."}, headers=NURSE
    )
    assert r.status_code == 200 and calls(api)[S_CLEAR]["review"]["resolved"] is True
    assert outbox(api)[S_CLEAR]["status"] == "pending_export"


def test_bulk_signs_off_cleared_calls_only(four):
    client, api = four
    r = client.post(
        "/api/calls/sign-off",
        json={"call_sids": [S_CLEAR, S_SMS, S_URGENT, S_VM, "nope"]},
        headers=NURSE,
    )
    assert r.status_code == 200
    by = {x["call_sid"]: x["status"] for x in r.json()["results"]}
    assert by == {
        S_CLEAR: "signed_off",
        S_SMS: "signed_off",
        S_URGENT: "needs_individual_review",
        S_VM: "not_an_assessment",
        "nope": "invalid_sid",
    }
    assert r.json()["signed_off"] == 2 and set(outbox(api)) == {S_CLEAR, S_SMS}


def test_a_sign_off_without_a_verified_signer_is_refused(four):
    """B-7: a signature needs a signer; it is never filed as "unverified"."""
    client, api = four
    for headers, body in (
        ({}, {}),
        ({}, {"signed_by": "  "}),
        ({"X-Acting-User": "nurse-assistant (user not verified)"}, {}),
    ):
        r = client.post(f"/api/calls/{S_CLEAR}/sign-off", json=body, headers=headers)
        assert r.status_code == 422 and "signer" in r.json()["detail"]
    assert client.post("/api/calls/sign-off", json={"call_sids": [S_CLEAR]}).status_code == 422
    assert "review" not in calls(api)[S_CLEAR] and not outbox(api)
    # In dev/test (no IAP in front) the body's signer is accepted.
    r = client.post(f"/api/calls/{S_CLEAR}/sign-off", json={"signed_by": "rn.lee"})
    assert r.status_code == 200 and calls(api)[S_CLEAR]["review"]["by"] == "rn.lee"


def test_outside_dev_only_the_signed_in_user_signs():
    import base64
    import json

    client, api, _ = make(env="demo")
    put(
        api,
        S_CLEAR,
        {"patient_id": "P-1", "outcome": "questionnaire_completed", "follow_up": "none"},
    )
    claims = base64.urlsafe_b64encode(
        json.dumps({"email": "ui@example.iam.gserviceaccount.com"}).encode()
    )
    ui = {"Authorization": f"Bearer x.{claims.decode().rstrip('=')}.y"}
    r = client.post(f"/api/calls/{S_CLEAR}/sign-off", json={"signed_by": "rn.lee"}, headers=ui)
    assert r.status_code == 422
    r = client.post(f"/api/calls/{S_CLEAR}/sign-off", json={}, headers={**ui, **NURSE})
    assert r.status_code == 200 and calls(api)[S_CLEAR]["review"]["by"] == "rn@example.org"


# --- one patient, several reports: only the latest is signed off ------------------------

OLD_CLEAR, OLD_URGENT, LATEST, LATER_VM = ("CA" + c * 32 for c in "abcd")


@pytest.fixture()
def history():
    """Cleared by text in August, urgent by phone the same evening, urgent by text in
    October, then a voicemail (not an assessment)."""
    client, api, _ = make()
    base = {"patient_id": "P-9", "outcome": "questionnaire_completed"}
    put(api, OLD_CLEAR, {**base, "initiated_at": "2026-08-21T21:21:00Z", "follow_up": "none"})
    put(
        api,
        OLD_URGENT,
        {**base, "initiated_at": "2026-08-21T23:10:00Z", "follow_up": "urgent_same_day"},
    )
    put(
        api,
        LATEST,
        {**base, "initiated_at": "2026-10-16T16:57:00Z", "follow_up": "urgent_same_day"},
    )
    put(
        api,
        LATER_VM,
        {
            **base,
            "initiated_at": "2026-10-17T10:00:00Z",
            "outcome": "voicemail_left",
            "follow_up": "none",
        },
    )
    return client, api


def test_an_earlier_report_cannot_be_signed_off(history):
    client, api = history
    r = client.post(f"/api/calls/{OLD_CLEAR}/sign-off", json={}, headers=NURSE)
    assert r.status_code == 409 and "later report" in r.json()["detail"]
    r = client.post("/api/calls/sign-off", json={"call_sids": [OLD_CLEAR]}, headers=NURSE)
    assert r.json()["results"][0]["status"] == "superseded_by_later"
    assert "review" not in calls(api)[OLD_CLEAR] and not outbox(api)


def test_signing_the_latest_closes_the_earlier_ones(history):
    client, api = history
    r = client.post(
        f"/api/calls/{LATEST}/sign-off",
        json={"note": "Called back; afebrile now, BG 140."},
        headers=NURSE,
    )
    assert r.status_code == 200, r.text
    assert sorted(r.json()["superseded"]) == sorted([OLD_CLEAR, OLD_URGENT])
    assert r.json()["also_resolved"] == [OLD_URGENT]
    docs = calls(api)
    for sid in (OLD_CLEAR, OLD_URGENT):
        rv = docs[sid]["review"]
        assert rv["status"] == "superseded" and rv["superseded_by"] == LATEST
        assert rv["by"] == "rn@example.org" and docs[sid]["review_history"][-1] == rv
    assert docs[OLD_URGENT]["review"]["resolved"] is True
    assert "afebrile" in docs[OLD_URGENT]["review"]["note"]
    assert set(outbox(api)) == {LATEST, OLD_URGENT}
    assert outbox(api)[OLD_URGENT]["closed_with"] == LATEST
    assert (
        client.post(f"/api/calls/{OLD_CLEAR}/sign-off", json={}, headers=NURSE).status_code == 409
    )


def test_a_later_clear_report_needs_a_note_while_an_earlier_emergency_is_open():
    client, api, _ = make()
    emerg, clear = "CA" + "e" * 32, "CA" + "f" * 32
    base = {"patient_id": "P-8", "outcome": "questionnaire_completed"}
    put(api, emerg, {**base, "initiated_at": "2026-10-10T10:00:00Z", "follow_up": "emergency"})
    put(api, clear, {**base, "initiated_at": "2026-10-17T10:00:00Z", "follow_up": "none"})
    r = client.post(f"/api/calls/{clear}/sign-off", json={}, headers=NURSE)
    assert r.status_code == 422 and "earlier report" in r.json()["detail"]
    r = client.post("/api/calls/sign-off", json={"call_sids": [clear]}, headers=NURSE)
    assert r.json()["results"][0]["status"] == "needs_individual_review"
    assert "review" not in calls(api)[emerg] and not outbox(api)
    # D-1: still open, whatever came after it.
    assert [c["doc_id"] for c in client.get("/api/follow-ups/open").json()["calls"]] == [emerg]
    r = client.post(
        f"/api/calls/{clear}/sign-off",
        json={"note": "Spoke with the patient: ED visit, discharged home."},
        headers=NURSE,
    )
    assert r.status_code == 200 and r.json()["also_resolved"] == [emerg]
    assert calls(api)[emerg]["review"]["resolved"] is True
    assert set(outbox(api)) == {clear, emerg}
    assert client.get("/api/follow-ups/open").json()["count"] == 0


def test_a_call_without_an_mrn_never_closes_a_patient_with_one():
    client, api, _ = make()
    phone = "+12025550109"
    with_mrn, without = "CA" + "1" * 31 + "a", "CA" + "2" * 31 + "a"
    put(
        api,
        with_mrn,
        {
            "patient_id": "P-111",
            "to": phone,
            "initiated_at": "2026-10-10T10:00:00Z",
            "outcome": "questionnaire_completed",
            "follow_up": "emergency",
        },
    )
    put(
        api,
        without,
        {
            "to": phone,
            "initiated_at": "2026-10-17T10:00:00Z",
            "outcome": "questionnaire_completed",
            "follow_up": "none",
        },
    )
    r = client.post(f"/api/calls/{without}/sign-off", json={}, headers=NURSE)
    assert r.status_code == 200, r.text
    assert r.json()["superseded"] == [] and "review" not in calls(api)[with_mrn]


# --- review fixes and B-5 --------------------------------------------------------------

E1, E2 = "CA" + "5" * 32, "CA" + "6" * 32


def test_a_reopened_earlier_report_can_be_resolved_once_the_later_is_closed():
    """No deadlock: the later report is signed off, the earlier one is then
    re-tiered (reopened); it must be resolvable itself."""
    client, api, _ = make()
    base = {"patient_id": "P-1", "outcome": "questionnaire_completed"}
    put(api, E1, {**base, "initiated_at": "2026-10-10T10:00:00Z", "follow_up": "none"})
    put(api, E2, {**base, "initiated_at": "2026-10-17T10:00:00Z", "follow_up": "none"})
    assert client.post(f"/api/calls/{E2}/sign-off", json={}, headers=NURSE).status_code == 200
    assert calls(api)[E1]["review"]["status"] == "superseded"
    r = client.post(
        f"/api/calls/{E1}/follow-up",
        json={"follow_up": "urgent_same_day", "reason": "Listened again: fever mentioned."},
        headers=NURSE,
    )
    assert r.status_code == 200 and calls(api)[E1]["review"]["status"] == "reopened"
    assert [c["doc_id"] for c in client.get("/api/follow-ups/open").json()["calls"]] == [E1]
    r = client.post(
        f"/api/calls/{E1}/sign-off", json={"note": "Called back; afebrile now."}, headers=NURSE
    )
    assert r.status_code == 200, r.text
    assert calls(api)[E1]["review"]["status"] == "signed_off" and E1 in outbox(api)


def test_reopening_a_resolved_superseded_report_withdraws_its_export():
    client, api, _ = make()
    put(
        api,
        E1,
        {
            "patient_id": "P-1",
            "outcome": "questionnaire_completed",
            "follow_up": "urgent_same_day",
            "review": {"status": "superseded", "resolved": True, "note": "x"},
        },
    )
    run(api.store.set("ehr_outbox", E1, {"status": "pending_export"}))
    r = client.post(
        f"/api/calls/{E1}/follow-up",
        json={"follow_up": "emergency", "reason": "Worse on re-listen."},
        headers=NURSE,
    )
    assert r.status_code == 200 and outbox(api)[E1]["status"] == "withdrawn"


def test_re_tiering_a_merely_superseded_report_reopens_it_without_an_export():
    client, api, _ = make()
    put(
        api,
        E1,
        {
            "follow_up": "none",
            "outcome": "questionnaire_completed",
            "patient_id": "P-1",
            "review": {"status": "superseded", "superseded_by": E2},
        },
    )
    r = client.post(
        f"/api/calls/{E1}/follow-up",
        json={"follow_up": "urgent_same_day", "reason": "Listened again: fever mentioned."},
        headers=NURSE,
    )
    assert r.status_code == 200 and calls(api)[E1]["review"]["status"] == "reopened"
    assert E1 not in outbox(api)


# --- open follow-ups ------------------------------------------------------------------


def test_open_follow_ups_are_found_and_stay_open_until_resolved():
    client, api, _ = make()

    def c(sid, pid, at, **kw):
        put(api, sid, {"patient_id": pid, "initiated_at": at, **kw})

    q = "questionnaire_completed"
    c("CA" + "1" * 32, "P1", "2026-01-01T10:00:00Z", outcome=q, follow_up="urgent_same_day")
    # P2: urgent, then a later clean questionnaire: STILL open (D-1).
    c("CA" + "2" * 32, "P2", "2026-01-01T10:00:00Z", outcome=q, follow_up="urgent_same_day")
    c("CA" + "3" * 32, "P2", "2026-01-02T10:00:00Z", outcome=q, follow_up="none")
    # P3: emergency, then a voicemail filed with "none": still open.
    c("CA" + "4" * 32, "P3", "2026-01-01T10:00:00Z", outcome=q, follow_up="emergency")
    c("CA" + "5" * 32, "P3", "2026-01-02T10:00:00Z", outcome="voicemail_left", follow_up="none")
    # P4: resolved (signed off): closed.
    c(
        "CA" + "6" * 32,
        "P4",
        "2026-01-01T10:00:00Z",
        outcome=q,
        follow_up="urgent_same_day",
        review={"status": "signed_off", "resolved": True},
    )
    # The DOB is never served.
    c(
        "CA" + "7" * 32,
        "P5",
        "2026-01-03T10:00:00Z",
        outcome=q,
        follow_up="routine_next_business_day",
        patient_context={"patient_dob": "1950-01-01", "patient_name": "X"},
        patient_dob="1950-01-01",
    )
    body = client.get("/api/follow-ups/open").json()
    ids = [x["doc_id"] for x in body["calls"]]
    assert ids[0] == "CA" + "4" * 32
    assert set(ids) == {"CA" + d * 32 for d in "1247"} and body["count"] == 4
    assert "1950-01-01" not in str(body)
    assert len(client.get("/api/follow-ups/open", params={"limit": 2}).json()["calls"]) == 2


def test_open_follow_ups_are_not_capped():
    client, api, _ = make()
    for i in range(320):
        put(
            api,
            f"CA{i:032x}",
            {
                "follow_up": "urgent_same_day",
                "patient_id": f"P{i}",
                "initiated_at": f"2026-10-{1 + i % 28:02d}T10:00:00Z",
            },
        )
    assert len(client.get("/api/follow-ups/open").json()["calls"]) == 320


# =========================================================================== B-2


def test_a_member_cancelled_after_the_run_queued_its_dial_is_not_rung():
    client, api, _ = make()
    a, b = add(client), add(client, patient_id="P-002", phone=PHONE2)
    assert client.post("/api/call-list/run", json={}).status_code == 200
    r = client.post(
        "/api/call-list/cancel",
        json={"entry_ids": [b], "reason": "Readmitted overnight."},
        headers=NURSE,
    )
    assert r.json()["results"] == [{"entry_id": b, "status": "canceled"}]
    results = [x.json() for x in drain(client, api)]
    assert {"skipped": "entry canceled"} in results
    assert [p[1].to for p in api.voice.placed] == [PHONE]
    assert entry(api, b)["list_status"] == "canceled" and entry(api, b)["attempts"] == 0
    run_doc = client.get("/api/call-list/run").json()["run"]
    assert run_doc["skipped"] == [b] and run_doc["status"] == "running"  # a's call is live
    assert entry(api, a)["list_status"] == "calling"


def test_a_run_of_only_cancelled_members_finishes_instead_of_hanging():
    client, api, _ = make()
    a = add(client)
    client.post("/api/call-list/run", json={})
    client.post("/api/call-list/cancel", json={"entry_ids": [a], "reason": "Patient asked."})
    drain(client, api)
    drain(client, api)  # a redelivered task counts once
    run_doc = client.get("/api/call-list/run").json()["run"]
    assert run_doc["status"] == "completed" and run_doc["skipped"] == [a]
    assert not api.voice.placed


def test_a_cancelled_run_stops_its_queued_dials():
    client, api, _ = make()
    add(client)
    add(client, patient_id="P-002", phone=PHONE2)
    client.post("/api/call-list/run", json={})
    assert client.post("/api/call-list/run/cancel").json()["run"]["status"] == "canceled"
    assert all(x.json() == {"skipped": "run canceled"} for x in drain(client, api))
    assert not api.voice.placed


def test_a_result_arriving_after_a_cancel_keeps_the_cancel():
    client, api, _ = make()
    a = add(client)
    client.post("/api/call-list/run", json={})
    call_id = drain(client, api)[0].json()["call_id"]
    # A nurse cannot cancel a patient on the phone ...
    r = client.post(
        "/api/call-list/cancel", json={"entry_ids": [a], "reason": "Readmitted overnight."}
    )
    assert r.json()["results"] == [{"entry_id": a, "status": "in_progress"}]
    # ... but a row cancelled by the time the result lands stays cancelled.
    run(api.store.merge("call_list", a, {"list_status": "canceled"}))
    run(api.store.merge("calls", call_id, {"call_status": "left_vmail"}))
    client.post("/tasks/call-terminal", json={"call_id": call_id})
    e = entry(api, a)
    assert e["list_status"] == "canceled" and e["last_call_status"] == "left_vmail"
    assert not e.get("next_recall_at")  # and is not auto-recalled


def test_a_result_from_an_older_attempt_does_not_reset_a_newer_one():
    client, api, _ = make()
    a = add(client)
    client.post("/api/call-list/run", json={})
    call_id = drain(client, api)[0].json()["call_id"]
    run(api.store.merge("call_list", a, {"attempts": 2}))  # a newer attempt is calling
    run(api.store.merge("calls", call_id, {"call_status": "rna"}))
    client.post("/tasks/call-terminal", json={"call_id": call_id})
    assert entry(api, a)["list_status"] == "calling"
    assert calls(api)[call_id]["entry_attempt"] == 1


def test_a_nurse_naming_a_finished_or_cancelled_row_dials_it():
    client, api, _ = make()
    a = add(client)
    run(api.store.merge("call_list", a, {"list_status": "canceled"}))
    r = client.post("/api/call-list/run", json={"entry_ids": [a]})
    assert r.status_code == 200 and r.json()["run"]["explicit"] is True
    assert "call_id" in drain(client, api)[0].json()


def test_a_member_of_a_run_being_dialled_can_be_cancelled():
    client, api, _ = make()
    a, b = add(client), add(client, patient_id="P-002", phone=PHONE2)
    run(
        api.store.set(
            "call_runs",
            "r" * 32,
            {"status": "dispatching", "entry_ids": [a, b], "scheduled_for": NOW.isoformat()},
        )
    )
    r = client.post(
        "/api/call-list/cancel", json={"entry_ids": [b], "reason": "Readmitted overnight."}
    )
    assert r.json()["results"] == [{"entry_id": b, "status": "canceled"}]
    assert entry(api, b)["canceled"]["run_id"] == "r" * 32
    assert run(api.store.get("call_runs", "r" * 32))["entry_ids"] == [a, b]  # record left as it was


# =========================================================================== B-8


def _run(api, rid, when, ids, **kw):
    run(
        api.store.set(
            "call_runs",
            rid,
            {"scheduled_for": when.isoformat(), "entry_ids": ids, "status": "scheduled", **kw},
        )
    )


def test_a_run_due_outside_calling_hours_moves_instead_of_dialling():
    client, api, clock = make()
    a = add(client)
    rid = "1" * 32
    _run(api, rid, NIGHT - timedelta(minutes=5), [a])
    clock.at = NIGHT
    r = client.post("/cron/dispatch-due").json()
    assert r["started"] is False
    assert "Outside calling hours" in r["reason"] and "moved 1" in r["reason"]
    moved = run(api.store.get("call_runs", rid))
    # Next opening: Wednesday 09:00 New York (14:00Z).
    assert moved["status"] == "scheduled" and moved["scheduled_for"] == "2026-03-04T14:00:00+00:00"
    assert "due outside calling hours" in moved["note"]
    assert not api.tasks.tasks and not api.voice.placed


def test_a_time_without_a_zone_does_not_break_the_tick():
    client, api, clock = make()
    a = add(client)
    run(
        api.store.set(
            "call_runs",
            "2" * 32,
            {"scheduled_for": "2026-03-03T14:00:00", "entry_ids": [a], "status": "scheduled"},
        )
    )
    run(
        api.store.set(
            "call_runs",
            "3" * 32,
            {"scheduled_for": "garbage", "entry_ids": [a], "status": "scheduled"},
        )
    )
    r = client.post("/cron/dispatch-due").json()
    assert r["call_run_id"] == "2" * 32 and r["dialled"] == 1


def test_a_run_crossing_closing_time_carries_the_rest_over_and_records_when_it_finished():
    client, api, clock = make()
    a, b, c = add(client), add(client, patient_id="P-2"), add(client, patient_id="P-3")
    rid = "4" * 32
    _run(api, rid, NOW, [a, b, c], auto_recall=True)
    r = client.post("/cron/dispatch-due").json()
    assert r["call_run_id"] == rid and r["dialled"] == 3
    tasks = [t for t in api.tasks.tasks if t["path"] == "/tasks/dial"]
    api.tasks.tasks.clear()
    first = client.post(tasks[0]["path"], json=tasks[0]["payload"]).json()
    assert "call_id" in first
    # Closing time passes before the other two get their turn.
    clock.at = NIGHT
    for t in tasks[1:]:
        assert client.post(t["path"], json=t["payload"]).json() == {
            "skipped": "outside calling hours"
        }
    run(api.store.merge("calls", first["call_id"], {"call_status": "success"}))
    client.post("/tasks/call-terminal", json={"call_id": first["call_id"]})
    list_run = client.get("/api/call-list/run").json()["run"]
    assert list_run["status"] == "completed" and sorted(list_run["deferred"]) == sorted([b, c])
    runs = {i: d for i, d in run(api.store.query("call_runs"))}
    parent = runs.pop(rid)
    assert parent["entry_ids"] == [a] and sorted(parent["carried_over"]) == sorted([b, c])
    assert parent["finished_at"] == now_iso(NIGHT)
    ((_, cont),) = [
        (i, d) for i, d in runs.items() if not d.get("auto_recall") or d.get("carried_from")
    ]
    assert sorted(cont["entry_ids"]) == sorted([b, c]) and cont["carried_from"] == rid
    assert cont["auto_recall"] is True and cont["status"] == "scheduled"
    assert cont["scheduled_for"] == "2026-03-04T14:00:00+00:00"
    assert len(api.voice.placed) == 1


def test_carry_over_moves_only_patients_still_waiting():
    client, api, clock = make()
    a, b = add(client), add(client, patient_id="P-2")
    rid = "5" * 32
    _run(api, rid, NOW, [a, b])
    client.post("/cron/dispatch-due")
    clock.at = NIGHT
    drain(client, api)
    # b was cancelled after closing time: it is not put in tomorrow's run.
    run(api.store.merge("call_list", b, {"list_status": "canceled"}))
    from voa_api.routes_tasks import carry_over

    assert run(carry_over(api, [b], rid)) is None
    assert run(api.store.get("call_runs", rid))["carried_over"] == []
    assert len(run(api.store.query("call_runs"))) == 2  # the parent and a's continuation


def test_a_nurses_own_run_is_not_limited_by_calling_hours():
    client, api, clock = make()
    add(client)
    clock.at = NIGHT
    client.post("/api/call-list/run", json={})
    assert "call_id" in drain(client, api)[0].json()
    assert client.post("/api/place-call", json=ENTRY).status_code == 200


def test_the_dialer_refuses_an_automatic_dial_outside_hours():
    from voa_api.dialer import OutsideCallingHours, dial
    from voa_api.models import PlaceCallRequest
    from voa_core.compliance import RecallSettings

    client, api, clock = make()
    clock.at = NIGHT
    with pytest.raises(OutsideCallingHours):
        run(dial(api, PlaceCallRequest(**ENTRY), placed_via="call_list", hours=RecallSettings()))
    assert not api.voice.placed


def test_a_run_with_nobody_pending_is_closed_at_once():
    client, api, _ = make()
    a = add(client)
    run(api.store.merge("call_list", a, {"list_status": "completed"}))
    rid = "6" * 32
    _run(api, rid, NOW, [a])
    r = client.post("/cron/dispatch-due").json()
    assert r == {"started": False, "call_run_id": rid, "dialled": 0}
    doc = run(api.store.get("call_runs", rid))
    assert doc["status"] == "dispatched" and doc["finished_at"] == now_iso(NOW)


# =========================================================================== any patient in a run


def _when():
    return (NOW + timedelta(hours=2)).isoformat()


def test_a_cancelled_or_reached_patient_can_be_put_in_a_run():
    client, api, _ = make()
    a, b = add(client), add(client, patient_id="P-2")
    run(api.store.merge("call_list", a, {"list_status": "canceled", "canceled": {"reason": "x"}}))
    run(api.store.merge("call_list", b, {"list_status": "completed"}))
    r = client.post("/api/call-runs", json={"scheduled_for": _when(), "entry_ids": [a, b]})
    assert r.status_code == 201, r.text
    assert entry(api, a)["list_status"] == "pending" and entry(api, a)["canceled"] is None
    assert entry(api, b)["list_status"] == "pending"


def test_a_patient_on_the_phone_or_unknown_cannot_be_added():
    client, api, _ = make()
    a = add(client)
    run(api.store.merge("call_list", a, {"list_status": "calling"}))
    r = client.post("/api/call-runs", json={"scheduled_for": _when(), "entry_ids": [a]})
    assert r.status_code == 409 and "On the phone" in r.json()["detail"]
    r = client.post("/api/call-runs", json={"scheduled_for": _when(), "entry_ids": ["f" * 32]})
    assert r.status_code == 400 and "Unknown patients" in r.json()["detail"]


def test_a_patient_already_in_another_scheduled_run_cannot_be_added():
    client, api, _ = make()
    a = add(client)
    assert (
        client.post("/api/call-runs", json={"scheduled_for": _when(), "entry_ids": [a]}).status_code
        == 201
    )
    r = client.post("/api/call-runs", json={"scheduled_for": _when(), "entry_ids": [a]})
    assert r.status_code == 409 and "Already in another scheduled run" in r.json()["detail"]


def test_adding_a_cancelled_patient_to_an_existing_run_requeues_them():
    client, api, _ = make()
    a, b = add(client), add(client, patient_id="P-2")
    rid = client.post("/api/call-runs", json={"scheduled_for": _when(), "entry_ids": [b]}).json()[
        "run_id"
    ]
    run(api.store.merge("call_list", a, {"list_status": "canceled"}))
    r = client.patch(f"/api/call-runs/{rid}", json={"entry_ids": [b, a]})
    assert r.status_code == 200, r.text
    assert entry(api, a)["list_status"] == "pending"


def test_removing_patients_from_a_run_outside_the_current_hours_is_allowed():
    """Only a new time or an added patient must land inside calling hours."""
    client, api, _ = make()
    a, b = add(client), add(client, patient_id="P-2")
    rid = "7" * 32
    late = NOW + timedelta(hours=12)  # 22:00 New York
    _run(api, rid, late, [a, b])
    assert client.patch(f"/api/call-runs/{rid}", json={"entry_ids": [a]}).status_code == 200
    r = client.patch(f"/api/call-runs/{rid}", json={"entry_ids": [a, b]})
    assert r.status_code == 400 and "Outside calling hours" in r.json()["detail"]
    assert client.patch(f"/api/call-runs/{rid}", json={"label": "Evening"}).status_code == 200


# =========================================================================== text booking vs recall


def test_a_text_booking_takes_the_patient_out_of_the_recall_run():
    from voa_api.routes_tasks import drop_text_booked_from_recalls

    client, api, _ = make()
    a, b = add(client), add(client, patient_id="P-2", phone=PHONE2)
    run(api.store.set("sms_sessions", PHONE, {"state": "scheduled"}))
    later = NOW + timedelta(hours=2)
    _run(api, "8" * 32, later, [a, b], auto_recall=True)
    _run(api, "9" * 32, later, [a])  # a nurse's run is left alone
    assert run(drop_text_booked_from_recalls(api)) == 1
    assert run(api.store.get("call_runs", "8" * 32))["entry_ids"] == [b]
    assert run(api.store.get("call_runs", "9" * 32))["entry_ids"] == [a]
    assert entry(api, a)["recall_skipped"] == "patient booked a callback by text"


def test_a_recall_run_left_empty_is_deleted_on_the_next_tick():
    client, api, _ = make()
    a = add(client)
    run(api.store.set("sms_sessions", PHONE, {"state": "dialing"}))
    _run(api, "8" * 32, NOW + timedelta(hours=2), [a], auto_recall=True)
    client.post("/cron/dispatch-due")
    assert run(api.store.get("call_runs", "8" * 32)) is None
