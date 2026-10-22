"""Call concurrency: N call slots, waiting for a slot by re-enqueueing (never by a
503 into Cloud Tasks backoff), and the stale-run check that keeps a lost dial task
from dropping a patient or blocking the scheduler. Fictional data only."""

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from voa_api.app import create_app
from voa_api.context import Api
from voa_api.routes_tasks import _run_stale_after, enqueue_dials
from voa_core.config import load_config
from voa_core.store import MemoryStore
from voa_core.tasks import MemoryTaskQueue
from voa_providers.fake import FakeVoice

NOW = datetime(2026, 3, 3, 15, 0, tzinfo=UTC)  # Tuesday 10:00 in New York: inside calling hours
PHONE = "+12025550101"


class Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


def make(**dialing):
    cfg = load_config(
        environ={
            "VOA_ENV": "test",
            "VOA__GCP__PROJECT_ID": "test-project",
            "VOA__SERVICES__GATEWAY_URL": "https://gw.example.org",
            "VOA__SERVICES__TASKS_INVOKER_SA": "invoker@example.iam.gserviceaccount.com",
            "VOA__SERVICES__UI_SA": "ui@example.iam.gserviceaccount.com",
            "VOA__DIALING__ALLOWLIST": PHONE,
            **{f"VOA__DIALING__{k.upper()}": str(v) for k, v in dialing.items()},
        }
    )
    clock = Clock(NOW)
    api = Api(cfg=cfg, store=MemoryStore(), tasks=MemoryTaskQueue(), voice=FakeVoice(), clock=clock)
    client = TestClient(create_app(cfg, api))
    client.__enter__()
    return client, api, clock


def run(coro):
    return asyncio.run(coro)


def add_patients(client, n: int) -> list[str]:
    ids = []
    for i in range(n):
        r = client.post(
            "/api/call-list",
            json={
                "phone": PHONE,
                "patient_name": f"Test Patient {i:02d}",
                "patient_id": f"P-{i:03d}",
                "patient_dob": "1950-03-15",
            },
        )
        ids.append(r.json()["entry_id"])
    return ids


def drain(client, api) -> list:
    """Deliver the dial tasks queued right now, once each (like Cloud Tasks)."""
    out = []
    for t in [t for t in api.tasks.tasks if t["path"] == "/tasks/dial"]:
        api.tasks.tasks.remove(t)
        r = client.post(t["path"], json=t["payload"])
        assert r.status_code == 200, r.text  # never a 503 for a busy slot
        out.append((t, r.json()))
    return out


def live_calls(api) -> list[tuple[str, dict]]:
    return [
        (i, c)
        for i, c in run(api.store.query("calls"))
        if c.get("placed_via") == "call_list" and not c.get("call_status")
    ]


def end_call(client, api, call_id: str, status: str = "success") -> None:
    run(api.store.merge("calls", call_id, {"call_status": status}))
    assert client.post("/tasks/call-terminal", json={"call_id": call_id}).status_code == 200


def current_run(client) -> dict:
    return client.get("/api/call-list/run").json()["run"]


# --------------------------------------------------------------------------- N slots


def test_ten_patients_three_slots_never_more_than_three_and_all_dial():
    client, api, clock = make(max_concurrent_calls=3)
    ids = add_patients(client, 10)
    assert client.post("/api/call-list/run", json={"entry_ids": None}).status_code == 200

    dialled: list[str] = []
    for _round in range(200):
        for t, body in drain(client, api):
            if "call_id" in body:
                dialled.append(t["payload"]["entry_id"])
            else:
                assert body.get("waiting"), body
        live = live_calls(api)
        assert len(live) <= 3
        if len(dialled) == 10 and not live:
            break
        # Each slot wait is a new named task with the configured delay.
        for t in api.tasks.tasks:
            if t["path"] == "/tasks/dial" and t["payload"].get("wait"):
                assert t["delay_s"] == api.cfg.dialing.slot_wait_s
                assert t["name"].endswith(f"-w{t['payload']['wait']}")
        clock.advance(api.cfg.dialing.slot_wait_s)
        # The oldest live call ends; the others are still talking.
        if live:
            oldest = min(live, key=lambda x: x[1]["initiated_at"])
            end_call(client, api, oldest[0])

    assert sorted(dialled) == sorted(ids)  # everyone dialled, once each
    assert dialled[:3] == ids[:3]  # the first three go first
    r = current_run(client)
    assert r["status"] == "completed" and r["succeeded"] == 10 and r["failed"] == 0
    assert sorted(r["dialled_ids"]) == sorted(ids)


def test_busy_slot_reenqueues_with_a_delay_not_503():
    client, api, _ = make(max_concurrent_calls=1, slot_wait_s=20)
    e1, e2 = add_patients(client, 2)
    run_id = client.post("/api/call-list/run", json={"entry_ids": None}).json()["run"]["run_id"]
    results = drain(client, api)
    assert "call_id" in results[0][1]
    assert results[1][1]["waiting"] and results[1][1]["retry_in_s"] == 20
    (task,) = api.tasks.tasks
    assert task["delay_s"] == 20
    assert task["name"] == f"dial-{run_id}-{e2}-1-w1"
    assert task["payload"]["waiting_since"]
    # A redelivery of the same wait step does not fork the chain (named task).
    client.post("/tasks/dial", json=results[1][0]["payload"])
    assert len(api.tasks.tasks) == 1


def test_a_cancelled_run_stops_the_waiting():
    client, api, clock = make(max_concurrent_calls=1)
    add_patients(client, 2)
    client.post("/api/call-list/run", json={"entry_ids": None})
    drain(client, api)
    assert len(api.tasks.tasks) == 1  # the second patient is waiting
    assert client.post("/api/call-list/run/cancel").status_code == 200
    clock.advance(15)
    ((_, body),) = drain(client, api)
    assert body == {"skipped": "run canceled"}
    assert not api.tasks.tasks  # the wait ended


def test_a_cancelled_patient_stops_its_own_wait_and_is_recorded():
    client, api, clock = make(max_concurrent_calls=1)
    e1, e2 = add_patients(client, 2)
    client.post("/api/call-list/run", json={"entry_ids": None})
    drain(client, api)
    run(api.store.merge("call_list", e2, {"list_status": "canceled"}))
    clock.advance(15)
    ((_, body),) = drain(client, api)
    assert body["skipped"] == "entry canceled"
    assert not api.tasks.tasks
    assert current_run(client)["skipped"] == [e2]


def test_closing_time_defers_a_waiting_patient():
    client, api, clock = make(max_concurrent_calls=1)
    e1, e2 = add_patients(client, 2)
    run(enqueue_dials(api, [e1, e2], label="scheduled", enforce_hours=True))
    drain(client, api)
    assert len(api.tasks.tasks) == 1
    clock.t = datetime(2026, 3, 3, 22, 5, tzinfo=UTC)  # 17:05 in New York: closed
    ((_, body),) = drain(client, api)
    assert body == {"skipped": "outside calling hours"}
    assert not api.tasks.tasks
    assert current_run(client)["deferred"] == [e2]
    assert run(api.store.get("call_list", e2))["list_status"] == "pending"


# --------------------------------------------------------------------------- nobody lost


def test_a_long_wait_still_dials_once_a_stranded_call_times_out():
    """A call whose terminal status never arrives stops holding its slot after the
    call timeout (at least the longest call), and the waiting patient then dials."""
    client, api, clock = make(max_concurrent_calls=1)
    e1, e2 = add_patients(client, 2)
    client.post("/api/call-list/run", json={"entry_ids": None})
    drain(client, api)  # e1 dials and its call is never settled
    timeout = max(api.cfg.dialing.call_timeout_s, api.cfg.voice.max_call_seconds + 120)
    waited = 0
    while True:
        clock.advance(api.cfg.dialing.slot_wait_s)
        waited += api.cfg.dialing.slot_wait_s
        ((t, body),) = drain(client, api)
        if "call_id" in body:
            break
        assert waited <= timeout + api.cfg.dialing.slot_wait_s
    assert t["payload"]["entry_id"] == e2
    assert waited > api.cfg.voice.max_call_seconds  # never counted out during a long call
    assert run(api.store.get("call_list", e2))["list_status"] == "calling"


def test_a_patient_who_never_gets_a_slot_is_deferred_and_the_run_reconciled():
    client, api, clock = make(max_concurrent_calls=1, max_slot_wait_s=60)
    e1, e2 = add_patients(client, 2)
    client.post("/api/call-list/run", json={"entry_ids": None})
    drain(client, api)
    for _ in range(10):
        clock.advance(15)
        out = drain(client, api)
        if not out or "skipped" in out[0][1]:
            break
    assert out[0][1]["skipped"] == "no free call slot"
    assert not api.tasks.tasks
    r = current_run(client)
    assert r["deferred"] == [e2] and r["status"] == "running"  # e1's call never settled
    # Not stale yet: the cron leaves the run alone.
    assert client.post("/cron/dispatch-due").json()["reason"] == "a run is in progress"
    clock.advance(_run_stale_after(api).total_seconds() + 1)
    client.post("/cron/dispatch-due")
    r = current_run(client)
    assert r["status"] == "completed" and r["reconciled_at"] and r["in_flight"] == []
    assert run(api.store.get("call_list", e2))["list_status"] == "pending"


def test_a_lost_dial_task_is_reconciled_and_the_patient_carried_over():
    """Cloud Tasks gave up on one patient's task (error retries exhausted). The run
    must still close, record the patient, and carry them to the next opening."""
    client, api, clock = make(max_concurrent_calls=3)
    ids = add_patients(client, 3)
    now = NOW.isoformat()
    run(
        api.store.set(
            "call_runs",
            "sched-1",
            {"scheduled_for": now, "entry_ids": ids, "status": "dispatched", "label": "test"},
        )
    )
    run(enqueue_dials(api, ids, label="test", scheduled_run_id="sched-1", enforce_hours=True))
    lost = [t for t in api.tasks.tasks if t["payload"]["entry_id"] == ids[2]][0]
    api.tasks.tasks.remove(lost)  # the task vanished
    drain(client, api)
    for cid, _ in live_calls(api):
        end_call(client, api, cid)
    assert current_run(client)["status"] == "running"
    clock.advance(_run_stale_after(api).total_seconds() - 60)
    assert client.post("/cron/dispatch-due").json()["reason"] == "a run is in progress"
    clock.advance(120)
    client.post("/cron/dispatch-due")
    r = current_run(client)
    assert r["status"] == "completed" and r["deferred"] == [ids[2]] and r["succeeded"] == 2
    carried = [
        d
        for i, d in run(api.store.query("call_runs"))
        if i != "sched-1" and d.get("carried_from") == "sched-1"
    ]
    assert len(carried) == 1 and carried[0]["entry_ids"] == [ids[2]]
    # The lost task turning up late after all does not dial anyone.
    assert client.post("/tasks/dial", json=lost["payload"]).json()["skipped"] in (
        "run canceled",
        "run superseded",
    )


def test_a_task_from_a_superseded_run_does_not_dial():
    client, api, _ = make(max_concurrent_calls=3)
    (e1,) = add_patients(client, 1)
    old = run(enqueue_dials(api, [e1], label="old"))
    stale = api.tasks.tasks.pop()
    run(enqueue_dials(api, [e1], label="new"))
    body = client.post("/tasks/dial", json=stale["payload"]).json()
    assert body == {"skipped": "run superseded"} and old["run_id"] != current_run(client)["run_id"]
    assert run(api.store.get("call_list", e1))["list_status"] == "pending"
