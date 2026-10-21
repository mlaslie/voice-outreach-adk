"""campaign_id end to end through the API (docs/USE-CASES-PLAN.md, step 1):
entries, runs, ingest, place-call and test-call -> the dialer -> the staging and
call docs; and a missed call's SMS follow-up on the call's use case."""

from datetime import timedelta

import test_api as A
import test_sms as S
from fastapi.testclient import TestClient
from voa_api.app import create_app
from voa_api.context import Api
from voa_core import copy as messages
from voa_core.config import load_config
from voa_core.store import MemoryStore
from voa_core.tasks import MemoryTaskQueue
from voa_providers.fake import FakeVoice

run = A.run


def staged(api, call_id):
    return run(api.store.get("pending_calls", call_id)), run(api.store.get("calls", call_id))


def test_place_call_records_the_use_case_and_rejects_an_unknown_one():
    client, api = A.make()
    r = client.post("/api/place-call", json={**A.ENTRY, "campaign_id": "pre_op"})
    assert r.status_code == 200, r.text
    pending, call = staged(api, r.json()["call_sid"])
    assert pending["campaign_id"] == "pre_op" and pending["spec_version"] == 1
    assert call["campaign_id"] == "pre_op" and call["spec_version"] == 1
    # Default: post-op, as before.
    r = client.post("/api/place-call", json=A.ENTRY)
    pending, call = staged(api, r.json()["call_sid"])
    assert pending["campaign_id"] == call["campaign_id"] == "post_op"
    for bad in ("nope", "../post_op", 3):
        r = client.post("/api/place-call", json={**A.ENTRY, "campaign_id": bad})
        assert r.status_code == 422, bad
    assert client.post("/api/call-list", json={**A.ENTRY, "campaign_id": "x"}).status_code == 422
    bulk = [{**A.ENTRY, "campaign_id": "nope"}]
    assert client.post("/api/call-list/bulk", json=bulk).status_code == 422


def test_entries_and_runs_carry_the_use_case_to_the_dial():
    client, api = A.make(max_concurrent_calls=5)
    own = client.post("/api/call-list", json={**A.ENTRY, "campaign_id": "pre_op"}).json()
    assert own["campaign_id"] == "pre_op"
    plain = client.post("/api/call-list", json={**A.ENTRY, "patient_id": "P-002"}).json()
    assert plain["campaign_id"] is None
    # A run's campaign applies to entries without their own; an entry's own wins.
    r = client.post("/api/call-list/run", json={"campaign_id": "pre_op"})
    assert r.status_code == 200 and r.json()["run"]["campaign_id"] == "pre_op"
    results = A.drain(client, api)
    by_entry = {}
    for res in results:
        call = run(api.store.get("calls", res.json()["call_id"]))
        by_entry[call["entry_id"]] = call["campaign_id"]
    assert by_entry == {own["entry_id"]: "pre_op", plain["entry_id"]: "pre_op"}
    assert client.post("/api/call-list/run", json={"campaign_id": "nope"}).status_code == 422


def test_a_run_without_a_campaign_dials_entries_as_their_own_or_post_op():
    client, api = A.make(max_concurrent_calls=5)
    own = client.post("/api/call-list", json={**A.ENTRY, "campaign_id": "pre_op"}).json()
    plain = client.post("/api/call-list", json={**A.ENTRY, "patient_id": "P-002"}).json()
    client.post("/api/call-list/run", json={})
    calls = [run(api.store.get("calls", r.json()["call_id"])) for r in A.drain(client, api)]
    got = {c["entry_id"]: c["campaign_id"] for c in calls}
    assert got == {own["entry_id"]: "pre_op", plain["entry_id"]: "post_op"}


def test_a_scheduled_run_keeps_its_use_case():
    client, api = A.make()
    e1 = client.post("/api/call-list", json=A.ENTRY).json()["entry_id"]
    when = (A.NOW + timedelta(hours=1)).isoformat()
    r = client.post(
        "/api/call-runs", json={"scheduled_for": when, "entry_ids": [e1], "campaign_id": "pre_op"}
    )
    assert r.status_code in (200, 201), r.text
    assert r.json()["campaign_id"] == "pre_op"
    api.clock = lambda: A.NOW + timedelta(hours=1, minutes=1)
    assert client.post("/cron/dispatch-due").json()["dialled"] == 1
    (res,) = A.drain(client, api)
    assert run(api.store.get("calls", res.json()["call_id"]))["campaign_id"] == "pre_op"


def test_ingest_tranches_and_calls_name_the_use_case():
    import test_ingest as I

    client, api, _ = I.make()
    body = {
        "tranches": [
            {
                "label": "Pre-op",
                "scheduled_for": I.LATER,
                "campaign": "pre_op",
                "calls": [I.call("a"), I.call("b", phone=I.PHONE2, campaign="post_op")],
            }
        ]
    }
    r = client.put(I.URL, json=body)
    assert r.status_code == 200, r.text
    camp = {
        i["external_id"]: run(api.store.get("call_list", i["entry_id"]))["campaign_id"]
        for i in r.json()["items"]
    }
    assert camp == {"a": "pre_op", "b": "post_op"}
    bad = {"tranches": [{**body["tranches"][0], "campaign": "nope"}]}
    assert client.put(I.URL, json=bad).status_code == 422


def test_ingest_fingerprint_is_unchanged_for_batches_without_a_use_case():
    import hashlib
    import json

    import test_ingest as I
    from voa_api.routes_ingest import IngestBatch, _fingerprint

    body = IngestBatch.model_validate(I.batch(I.call("e1")))
    legacy = body.model_dump()
    for t in legacy["tranches"]:
        t.pop("campaign")
        for c in t["calls"]:
            c.pop("campaign")
    want = hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()
    assert _fingerprint(body) == want


def test_test_call_stages_the_requested_use_case():
    cfg = load_config(
        environ={
            "VOA_ENV": "test",
            "VOA__GCP__PROJECT_ID": "test-project",
            "VOA__SERVICES__GATEWAY_URL": "https://gw.example.org",
            "VOA__SERVICES__UI_SA": "ui@example.iam.gserviceaccount.com",
            "VOA__SERVICES__TASKS_INVOKER_SA": "invoker@example.iam.gserviceaccount.com",
            "VOA__SOFTPHONE__ENABLED": "true",
            "VOA__SOFTPHONE__TOKEN_SECRET": "test-softphone-secret",
        }
    )
    api = Api(cfg=cfg, store=MemoryStore(), tasks=MemoryTaskQueue(), voice=FakeVoice())
    client = TestClient(create_app(cfg, api))
    client.__enter__()
    r = client.post("/api/test-call", json={"campaign_id": "pre_op"})
    assert r.status_code == 200, r.text
    pending, call = staged(api, r.json()["call_sid"])
    assert pending["campaign_id"] == call["campaign_id"] == "pre_op"
    r = client.post("/api/test-call", json={})
    assert staged(api, r.json()["call_sid"])[1]["campaign_id"] == "post_op"
    assert client.post("/api/test-call", json={"campaign_id": "nope"}).status_code == 422


# --------------------------------------------------------------------------- SMS


PRE_OP_REPLIES = ["yes", "yes", "yes", "yes", "no", "no"]


def test_a_missed_pre_op_call_is_followed_up_by_text_as_pre_op():
    client, api, _ = S.make()
    S.seed_call(api)
    run(api.store.merge("calls", S.CALL, {"campaign_id": "pre_op", "spec_version": 1}))
    S.consent(client)
    assert S.invite(client, api)[0].json() == {"sent": True}
    s = S.session(api)
    assert s["campaign_id"] == "pre_op" and s["patient_context"]["campaign_id"] == "pre_op"
    S.text(client, "3")
    S.text(client, "03/15/1950")
    assert api.sms.bodies()[-1].startswith("(1/6) Do you know what time to arrive")
    for a in PRE_OP_REPLIES:
        S.text(client, a)
    closing = api.sms.bodies()[-1]
    assert "Your care team has your answers" in closing
    assert messages.MEDICATION_LIST_REMINDER not in closing
    (rep,) = [d for _, d in run(api.store.query("reports"))]
    assert rep["campaign_id"] == "pre_op" and rep["spec_version"] == 1
    assert rep["outcome"] == "questionnaire_completed" and rep["follow_up"] == "none"
    assert rep["survey_responses"]["ride_home"] == "yes"


def test_a_post_op_follow_up_reports_post_op():
    client, api, _ = S.make()
    S.seed_call(api)
    run(api.store.merge("calls", S.CALL, {"campaign_id": "post_op", "spec_version": 1}))
    S.consent(client)
    S.invite(client, api)
    S.answer_survey(client, S.CLEAN_SURVEY)
    rep = run(api.store.query("reports"))[0][1]
    assert rep["campaign_id"] == "post_op" and rep["follow_up"] == "none"
    assert messages.MEDICATION_LIST_REMINDER in api.sms.bodies()[-1]


def test_an_entry_whose_use_case_was_removed_is_blocked_not_retried():
    client, api = A.make(max_concurrent_calls=5)
    e = client.post("/api/call-list", json={**A.ENTRY, "campaign_id": "pre_op"}).json()
    # The spec file disappears after the entry was saved (simulated on the row).
    run(api.store.merge("call_list", e["entry_id"], {"campaign_id": "retired_case"}))
    client.post("/api/call-list/run", json={})
    results = A.drain(client, api)
    assert all(r.status_code < 500 for r in results), [r.text for r in results]
    row = run(api.store.get("call_list", e["entry_id"]))
    assert row["list_status"] == "blocked"
    assert "not dialable" in row["last_error"]
