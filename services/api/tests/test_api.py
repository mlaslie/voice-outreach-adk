import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from voa_api.app import create_app
from voa_api.context import Api
from voa_core.config import load_config
from voa_core.store import MemoryStore
from voa_core.tasks import MemoryTaskQueue
from voa_providers.fake import FakeVoice

NOW = datetime(2026, 3, 3, 15, 0, tzinfo=UTC)  # Tuesday 10:00 in New York: inside calling hours
PHONE = "+12025550101"
ENTRY = {
    "phone": PHONE,
    "patient_name": "Jordan Rivera",
    "patient_id": "P-001",
    "patient_dob": "1950-03-15",
    "is_diabetic": "false",
}


def make(env="test", allowlist=PHONE, **dialing):
    cfg = load_config(
        environ={
            "VOA_ENV": env,
            "VOA__GCP__PROJECT_ID": "test-project",
            "VOA__SERVICES__GATEWAY_URL": "https://gw.example.org",
            "VOA__SERVICES__TASKS_INVOKER_SA": "invoker@example.iam.gserviceaccount.com",
            "VOA__SERVICES__UI_SA": "ui@example.iam.gserviceaccount.com",
            "VOA__DIALING__ALLOWLIST": allowlist,
            **{f"VOA__DIALING__{k.upper()}": str(v) for k, v in dialing.items()},
        }
    )
    api = Api(
        cfg=cfg, store=MemoryStore(), tasks=MemoryTaskQueue(), voice=FakeVoice(), clock=lambda: NOW
    )
    client = TestClient(create_app(cfg, api))
    client.__enter__()
    return client, api


def run(coro):
    return (
        asyncio.get_event_loop_policy().get_event_loop().run_until_complete(coro)
        if False
        else asyncio.run(coro)
    )


def drain(client, api, path_prefix="/tasks/dial"):
    """Execute queued tasks of one kind, like Cloud Tasks would (once each)."""
    out = []
    for t in [t for t in api.tasks.tasks if t["path"].startswith(path_prefix)]:
        api.tasks.tasks.remove(t)
        out.append(client.post(t["path"], json=t["payload"]))
    return out


# --------------------------------------------------------------------------- dialing


def test_place_call_stages_dob_only_in_pending_and_fails_closed():
    client, api = make(allowlist="")
    r = client.post("/api/place-call", json=ENTRY)
    assert r.status_code == 503  # no allowlist configured: refuse

    client, api = make()
    r = client.post("/api/place-call", json={**ENTRY, "phone": "+12025550199"})
    assert r.status_code == 403  # not on the allowlist
    r = client.post("/api/place-call", json=ENTRY)
    assert r.status_code == 200
    call_id = r.json()["call_sid"]
    call = run(api.store.get("calls", call_id))
    pending = run(api.store.get("pending_calls", call_id))
    assert pending["dob"] == "1950-03-15" and pending["is_diabetic"] is False
    assert "1950-03-15" not in json.dumps(call)  # review C-2
    assert call["amd_requested"] is True
    assert api.voice.placed[0][1].answer_url == "https://gw.example.org/twilio/answer"


def test_budget_and_provider_rejection():
    client, api = make(max_dials_per_hour=1)
    assert client.post("/api/place-call", json=ENTRY).status_code == 200
    assert client.post("/api/place-call", json=ENTRY).status_code == 429
    client, api = make()
    api.voice.reject_code = 21211
    assert client.post("/api/place-call", json=ENTRY).status_code == 400
    assert run(api.store.get("rate_limits", "outbound_dials"))["count"] == 0  # released


def test_input_validation():
    client, _ = make()
    assert (
        client.post("/api/call-list", json={**ENTRY, "patient_dob": "1950-13-45"}).status_code
        == 422
    )
    assert client.post("/api/call-list", json={**ENTRY, "phone": "555"}).status_code == 422
    dup = client.post("/api/call-list/bulk", json=[ENTRY, ENTRY])
    assert dup.status_code == 400


# --------------------------------------------------------------------------- run lifecycle


def test_run_dials_through_tasks_and_settles():
    client, api = make()
    e1 = client.post("/api/call-list", json=ENTRY).json()["entry_id"]
    e2 = client.post("/api/call-list", json={**ENTRY, "patient_id": "P-002"}).json()["entry_id"]
    r = client.post("/api/call-list/run", json={"entry_ids": None})
    assert r.status_code == 200 and r.json()["active"] is True
    assert client.post("/api/call-list/run", json={}).status_code == 409

    results = drain(client, api)
    assert results[0].status_code == 200 and "call_id" in results[0].json()
    assert results[1].status_code == 503  # one call slot (max_concurrent_calls=1): retried later
    call_id = results[0].json()["call_id"]
    assert run(api.store.get("call_list", e1))["list_status"] == "calling"

    run(api.store.merge("calls", call_id, {"call_status": "success"}))
    assert client.post("/tasks/call-terminal", json={"call_id": call_id}).status_code == 200
    assert run(api.store.get("call_list", e1))["list_status"] == "completed"

    # Cloud Tasks retries the busy one: now the slot is free.
    retry = client.post(
        "/tasks/dial", json={"entry_id": e2, "run_id": r.json()["run"]["run_id"], "attempt": 1}
    )
    call2 = retry.json()["call_id"]
    run(api.store.merge("calls", call2, {"call_status": "rna"}))
    client.post("/tasks/call-terminal", json={"call_id": call2})
    status = client.get("/api/call-list/run").json()
    assert status["run"]["status"] == "completed" and status["run"]["succeeded"] == 1
    assert status["run"]["failed"] == 1
    # Missed call: SMS follow-up queued and an auto-recall run scheduled in calling hours.
    assert any(t["path"] == "/tasks/sms-missed-call" for t in api.tasks.tasks)
    runs = client.get("/api/call-runs").json()["runs"]
    assert runs and runs[0]["auto_recall"] and e2 in runs[0]["entry_ids"]
    assert run(api.store.get("call_list", e2))["auto_recalls"] == 1


def test_dial_task_is_idempotent():
    client, api = make()
    client.post("/api/call-list", json=ENTRY).json()["entry_id"]
    client.post("/api/call-list/run", json={})
    task = api.tasks.tasks[0]
    first = client.post(task["path"], json=task["payload"]).json()
    again = client.post(task["path"], json=task["payload"]).json()  # duplicate delivery
    assert "call_id" in first and again == {"skipped": "already claimed"}
    assert len(api.voice.placed) == 1


def test_blocked_entry_is_parked_not_retried():
    client, api = make()
    e1 = client.post("/api/call-list", json={**ENTRY, "phone": "+12025550198"}).json()["entry_id"]
    client.post("/api/call-list/run", json={})
    drain(client, api)
    assert run(api.store.get("call_list", e1))["list_status"] == "blocked"
    assert client.post("/api/call-list/reset").json() == {"reset": 1}


def test_cancel_reschedule_and_dispatch_due():
    client, api = make()
    e1 = client.post("/api/call-list", json=ENTRY).json()["entry_id"]
    r = client.post(
        "/api/call-list/cancel",
        json={"entry_ids": [e1], "reason": "patient asked"},
        headers={"X-Acting-User": "nurse@example.org"},
    )
    assert r.json()["results"][0]["status"] == "canceled"
    assert run(api.store.get("call_list", e1))["canceled"]["by"] == "nurse@example.org"
    when = (NOW + timedelta(hours=1)).isoformat()
    r = client.post(f"/api/call-list/{e1}/reschedule", json={"scheduled_for": when})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    late = (NOW + timedelta(hours=14)).isoformat()  # midnight-ish: outside the window
    assert (
        client.post("/api/call-runs", json={"scheduled_for": late, "entry_ids": [e1]}).status_code
        == 400
    )

    api.clock = lambda: NOW + timedelta(hours=1, minutes=1)
    r = client.post("/cron/dispatch-due")
    assert r.json()["call_run_id"] == run_id and r.json()["dialled"] == 1
    assert run(api.store.get("call_runs", run_id))["status"] == "dispatched"


# --------------------------------------------------------------------------- calls views


def test_follow_up_override_keeps_agent_tier_and_history():
    client, api = make()
    cid = "CA" + "a" * 32
    run(api.store.set("calls", cid, {"follow_up": "none", "initiated_at": NOW.isoformat()}))
    r = client.post(
        f"/api/calls/{cid}/follow-up",
        json={"follow_up": "urgent_same_day", "reason": "called back"},
        headers={"X-Acting-User": "nurse@example.org"},
    )
    body = r.json()
    assert body["agent_follow_up"] == "none" and body["follow_up"] == "urgent_same_day"
    assert body["follow_up_history"][0]["set_by"] == "nurse@example.org"
    assert (
        client.post(
            f"/api/calls/{cid}/follow-up", json={"follow_up": "bogus", "reason": "x" * 5}
        ).status_code
        == 400
    )


def test_a_nurse_sets_several_reasons_and_the_agents_are_kept():
    client, api = make()
    cid = "CA" + "c" * 32
    agent = [{"tier": "urgent_same_day", "text": "question asked (medication)", "source": "triage"}]
    run(
        api.store.set(
            "calls",
            cid,
            {
                "follow_up": "urgent_same_day",
                "follow_up_reason": "Patient reports question asked (medication).",
                "follow_up_reasons": agent,
                "initiated_at": NOW.isoformat(),
            },
        )
    )
    r = client.post(
        f"/api/calls/{cid}/follow-up",
        json={
            "follow_up": "routine_next_business_day",
            "reasons": ["Medication refill request", "Not sleeping well", "Not sleeping well"],
        },
        headers={"X-Acting-User": "nurse@example.org"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [x["text"] for x in body["follow_up_reasons"]] == [
        "Medication refill request",
        "Not sleeping well",
    ]
    assert {x["source"] for x in body["follow_up_reasons"]} == {"nurse"}
    assert body["agent_follow_up_reasons"] == agent
    assert body["follow_up_reason"] == "Nurse: Medication refill request; Not sleeping well."
    assert body["follow_up_history"][-1]["reasons"] == [
        "Medication refill request",
        "Not sleeping well",
    ]
    # A second change keeps the agent's originals, not the first nurse's list.
    client.post(
        f"/api/calls/{cid}/follow-up",
        json={"follow_up": "informational", "reasons": ["Discussed by phone"]},
    )
    assert run(api.store.get("calls", cid))["agent_follow_up_reasons"] == agent
    # Neither a reason nor reasons: refused.
    assert client.post(f"/api/calls/{cid}/follow-up", json={"follow_up": "none"}).status_code == 422


def test_transcript_list_and_delete():
    client, api = make()
    cid = "CA" + "b" * 32
    run(
        api.store.set(
            "calls", cid, {"initiated_at": NOW.isoformat(), "answered_at": NOW.isoformat()}
        )
    )
    run(
        api.store.set(
            "transcripts",
            cid,
            {
                "turns": [
                    {"role": "agent", "text": "Hello", "at_ms": 1200},
                    {"role": "patient", "text": "Hi", "at_ms": 2500},
                ]
            },
        )
    )
    t = client.get(f"/api/calls/{cid}/transcript").json()
    assert t["available"] and [x["role"] for x in t["turns"]] == ["agent", "patient"]
    assert client.get("/api/calls").json()["count"] == 1
    assert client.get("/api/calls/not-an-id").status_code == 400
    r = client.post("/api/calls/batch-delete", json={"call_sids": [cid, "bad"]}).json()["results"]
    assert [x["status"] for x in r] == ["deleted", "invalid_sid"]


def test_allowlist_endpoints():
    client, api = make()
    assert (
        client.post("/api/allowlist", json={"phone": "+12025550150", "label": "tester"}).status_code
        == 201
    )
    entries = client.get("/api/allowlist").json()
    assert {e["source"] for e in entries["entries"]} == {"env", "firestore"} and entries[
        "mode"
    ] == "list"
    assert client.delete("/api/allowlist/%2B12025550150").status_code == 200


# --------------------------------------------------------------------------- auth


def _token(email):
    payload = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return {"Authorization": f"Bearer x.{payload}.y"}


def test_route_groups_are_limited_to_their_service_accounts():
    client, api = make(env="demo")
    assert client.get("/api/calls").status_code == 403
    assert (
        client.get("/api/calls", headers=_token("ui@example.iam.gserviceaccount.com")).status_code
        == 200
    )
    assert (
        client.post(
            "/cron/dispatch-due", headers=_token("ui@example.iam.gserviceaccount.com")
        ).status_code
        == 403
    )
    ok = client.post(
        "/cron/dispatch-due", headers=_token("invoker@example.iam.gserviceaccount.com")
    )
    assert ok.status_code == 200
    assert client.get("/health").status_code == 200


@pytest.fixture(autouse=True)
def _no_live(monkeypatch):
    monkeypatch.delenv("VOA_SECRET_SOFTPHONE_TOKEN_KEY", raising=False)
