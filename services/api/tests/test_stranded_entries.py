"""Call-list rows stranded in "calling": the per-minute cron puts them back.

A row stays "calling" when its call never gets a final status on the row (no
Twilio terminal callback, a gateway crash, a lost task). ``/cron/dispatch-due``
runs ``recover_stranded_entries`` every tick. Fictional data only."""

import asyncio
from unittest.mock import patch

from test_call_concurrency import NOW, add_patients, drain, live_calls, make
from voa_api import routes_tasks
from voa_api.routes_tasks import (
    CURRENT_RUN,
    TERMINAL_APPLY_GRACE_S,
    _call_timeout_s,
    enqueue_dials,
    recover_stranded_entries,
)
from voa_core.compliance import STRANDED_REQUEUED
from voa_core.store import now_iso


def run(coro):
    return asyncio.run(coro)


def entry(api, eid) -> dict:
    return run(api.store.get("call_list", eid))


def list_run(api) -> dict:
    return run(api.store.get("list_runs", CURRENT_RUN))


def tick(client) -> None:
    assert client.post("/cron/dispatch-due").status_code == 200


def placed(client, api, n: int = 1):
    """n patients in a run, each dialled: their call docs exist, no status yet."""
    ids = add_patients(client, n)
    run(enqueue_dials(api, ids, label="test"))
    drain(client, api)
    calls = live_calls(api)
    assert len(calls) == n
    return ids, calls


def stuck_claim(api, clock) -> tuple[str, str]:
    """A row claimed by a run whose dial task died before the call doc was written."""
    eid = "entry-stuck"
    stamp = now_iso(clock())
    run(
        api.store.set(
            "call_list",
            eid,
            {
                "phone": "+12025550101",
                "patient_name": "Test Patient Stuck",
                "patient_id": "P-900",
                "patient_dob": "1950-03-15",
                "list_status": "calling",
                "attempts": 1,
                "last_attempt_at": stamp,
                "updated_at": stamp,
            },
        )
    )
    r = run(enqueue_dials(api, [eid], label="test"))
    api.tasks.tasks.clear()  # the dial task is "lost"
    run(api.store.merge("call_list", eid, {"claimed_by_run": r["run_id"]}))
    return eid, r["run_id"]


# --------------------------------------------------------------------------- no call doc


def test_a_stuck_row_with_no_call_doc_is_requeued_after_the_ttl():
    client, api, clock = make()
    eid, run_id = stuck_claim(api, clock)
    clock.advance(_call_timeout_s(api) - 30)
    tick(client)
    assert entry(api, eid)["list_status"] == "calling"  # the dial may still be placing it

    clock.advance(60)
    with patch.object(routes_tasks.log, "warning") as warn:
        tick(client)
    e = entry(api, eid)
    assert e["list_status"] == "pending"
    assert e["last_error"] == STRANDED_REQUEUED
    assert e["last_call_status"] == "timeout"
    assert e["attempts"] == 1  # the next run claims attempt 2
    r = list_run(api)
    assert r["run_id"] == run_id and eid in r["deferred"]
    assert r["status"] == "completed"  # its only member is accounted for
    events = [
        c.kwargs["extra"]
        for c in warn.call_args_list
        if c.kwargs.get("extra", {}).get("event") == "entry.stranded_recovered"
    ]
    assert len(events) == 1 and events[0]["reason"] == "no_call"
    assert events[0]["entry_id"] == eid and "phone" not in events[0]


def test_a_requeued_row_dials_again_on_the_next_run():
    client, api, clock = make()
    eid, _ = stuck_claim(api, clock)
    clock.advance(_call_timeout_s(api) + 60)
    tick(client)
    run(enqueue_dials(api, [eid], label="again"))
    out = drain(client, api)
    assert [b for _, b in out if "call_id" in b]
    assert entry(api, eid)["attempts"] == 2


# --------------------------------------------------------------------------- terminal not applied


def test_a_terminal_call_that_never_reached_its_row_is_applied():
    client, api, clock = make()
    (eid,), [(cid, _)] = placed(client, api)
    run(api.store.merge("calls", cid, {"call_status": "rna", "ended_at": now_iso(clock())}))
    # /tasks/call-terminal is never delivered.
    clock.advance(60)
    tick(client)
    assert entry(api, eid)["list_status"] == "calling"  # within the grace: the task may come

    clock.advance(TERMINAL_APPLY_GRACE_S)
    tick(client)
    e = entry(api, eid)
    assert e["list_status"] == "pending" and e["last_call_status"] == "rna"
    assert "last_error" not in e or e["last_error"] != STRANDED_REQUEUED
    r = list_run(api)
    assert r["in_flight"] == [] and r["failed"] == 1 and r["status"] == "completed"


def test_a_successful_call_that_never_reached_its_row_completes_it():
    client, api, clock = make()
    (eid,), [(cid, _)] = placed(client, api)
    run(api.store.merge("calls", cid, {"call_status": "success", "ended_at": now_iso(clock())}))
    clock.advance(TERMINAL_APPLY_GRACE_S + 60)
    tick(client)
    e = entry(api, eid)
    assert e["list_status"] == "completed" and e["last_call_status"] == "success"
    assert list_run(api)["succeeded"] == 1


# --------------------------------------------------------------------------- live calls


def test_a_live_call_within_the_ttl_is_left_alone():
    client, api, clock = make(max_concurrent_calls=3)
    ids, calls = placed(client, api, 3)
    before = {eid: entry(api, eid) for eid in ids}
    for _ in range(5):
        clock.advance((_call_timeout_s(api) - 60) / 5)
        tick(client)
    assert {eid: entry(api, eid) for eid in ids} == before
    r = list_run(api)
    assert len(r["in_flight"]) == 3 and r["status"] == "running"


def test_a_call_answered_but_not_ended_is_left_alone_within_the_ttl():
    client, api, clock = make()
    (eid,), [(cid, _)] = placed(client, api)
    run(api.store.merge("calls", cid, {"call_status": "in_progress"}))
    clock.advance(_call_timeout_s(api) - 10)
    assert run(recover_stranded_entries(api)) == []
    assert entry(api, eid)["list_status"] == "calling"


# --------------------------------------------------------------------------- old call, no status


def test_an_old_call_with_no_status_is_reset_to_pending():
    client, api, clock = make()
    (eid,), [(cid, _)] = placed(client, api)
    clock.advance(_call_timeout_s(api) + 30)
    fixed = run(recover_stranded_entries(api))
    assert [(f["entry_id"], f["reason"], f["call_id"]) for f in fixed] == [
        (eid, "call_never_finished", cid)
    ]
    e = entry(api, eid)
    assert e["list_status"] == "pending" and e["last_error"] == STRANDED_REQUEUED
    r = list_run(api)
    assert r["in_flight"] == [] and r["failed"] == 1 and r["status"] == "completed"
    # The call doc is the gateway's: left as it is.
    assert not run(api.store.get("calls", cid)).get("call_status")


def test_a_late_terminal_result_after_a_reset_still_settles_the_row():
    client, api, clock = make()
    (eid,), [(cid, _)] = placed(client, api)
    clock.advance(_call_timeout_s(api) + 30)
    run(recover_stranded_entries(api))
    run(api.store.merge("calls", cid, {"call_status": "success"}))
    assert client.post("/tasks/call-terminal", json={"call_id": cid}).status_code == 200
    assert entry(api, eid)["list_status"] == "completed"


# --------------------------------------------------------------------------- idempotency


def test_recovery_is_idempotent():
    client, api, clock = make(max_concurrent_calls=3)
    (e1, e2), calls = placed(client, api, 2)
    sids = {c["entry_id"]: cid for cid, c in calls}
    run(api.store.merge("calls", sids[e1], {"call_status": "busy", "ended_at": now_iso(clock())}))
    stuck, _ = stuck_claim(api, clock)  # replaces the current run
    clock.advance(_call_timeout_s(api) + 60)
    first = run(recover_stranded_entries(api))
    assert sorted(f["entry_id"] for f in first) == sorted([e1, e2, stuck])
    snap_entries = {e: entry(api, e) for e in (e1, e2, stuck)}
    snap_run = list_run(api)
    clock.advance(60)
    assert run(recover_stranded_entries(api)) == []
    tick(client)
    tick(client)
    assert {e: entry(api, e) for e in (e1, e2, stuck)} == snap_entries
    assert list_run(api)["deferred"] == snap_run["deferred"]
    assert list_run(api)["failed"] == snap_run["failed"]


def test_a_row_claimed_again_meanwhile_is_not_touched():
    """The transaction re-checks the attempt: a newer claim wins."""
    client, api, clock = make()
    (eid,), _ = placed(client, api)
    clock.advance(_call_timeout_s(api) + 30)
    real = api.store.transact

    async def racing(coll, doc_id, fn):
        if coll == "call_list" and doc_id == eid:
            await api.store.merge("call_list", eid, {"attempts": 2})  # another claim landed
        return await real(coll, doc_id, fn)

    api.store.transact = racing
    assert run(recover_stranded_entries(api)) == []
    api.store.transact = real
    assert entry(api, eid)["list_status"] == "calling"


def test_an_unreadable_call_counts_as_live():
    client, api, clock = make()
    (eid,), _ = placed(client, api)
    clock.advance(_call_timeout_s(api) + 30)
    real = api.store.query

    async def failing(coll, *a, **k):
        if coll == "calls":
            raise RuntimeError("datastore blip")
        return await real(coll, *a, **k)

    api.store.query = failing
    assert run(recover_stranded_entries(api)) == []
    api.store.query = real
    assert entry(api, eid)["list_status"] == "calling"


def test_never_more_than_the_batch_per_tick(monkeypatch):
    client, api, clock = make()
    for i in range(4):
        run(
            api.store.set(
                "call_list",
                f"e{i}",
                {"list_status": "calling", "attempts": 1, "last_attempt_at": NOW.isoformat()},
            )
        )
    clock.advance(_call_timeout_s(api) + 30)
    monkeypatch.setattr(routes_tasks, "STRANDED_BATCH", 3)
    assert len(run(recover_stranded_entries(api))) == 3
    assert len(run(recover_stranded_entries(api))) == 1
