"""End-of-call writes never bring back a call that was deleted while it ended."""

import asyncio

from voa_core.store import MemoryStore
from voa_gateway.app import CALL_ID_RE
from voa_gateway.persist import StoreCallSink


def _sink(store):
    sink = StoreCallSink(
        store, recordings_bucket="bucket", project_id="p", call_id_pattern=CALL_ID_RE
    )
    sink._upload = lambda call_id, wav: f"gs://bucket/recordings/{call_id}.wav"
    return sink


def test_finish_saves_for_a_live_call():
    store = MemoryStore()
    asyncio.run(store.set("calls", "browser_" + "a" * 16, {"call_sid": "x", "to": "browser"}))
    asyncio.run(
        _sink(store).finish(
            "browser_" + "a" * 16,
            transcript=[{"role": "agent"}],
            timeline=[],
            recording_wav=b"RIFF",
        )
    )
    doc = asyncio.run(store.get("calls", "browser_" + "a" * 16))
    assert doc["recording_uri"].endswith(".wav")
    assert asyncio.run(store.get("transcripts", "browser_" + "a" * 16))


def test_finish_after_delete_writes_nothing():
    store = MemoryStore()
    cid = "browser_" + "b" * 16
    asyncio.run(
        _sink(store).finish(cid, transcript=[{"role": "agent"}], timeline=[], recording_wav=b"RIFF")
    )
    for coll in ("calls", "transcripts", "call_events"):
        assert asyncio.run(store.get(coll, cid)) is None


def _report():
    from voa_core import patient_questions as pq

    return {
        "outcome": "reschedule_requested",
        "follow_up": "routine_next_business_day",
        "follow_up_reason": "question asked (activity)",
        "survey_responses": {
            "has_questions": "yes",
            "patient_questions": [pq.record("When can I shower?")],
        },
    }


def test_a_report_filed_in_shadow_mode_queues_the_knowledge_shadow_task():
    from voa_core import knowledge as kb
    from voa_core.tasks import MemoryTaskQueue

    async def go(mode):
        store, tasks = MemoryStore(), MemoryTaskQueue()
        cid = "browser_" + "c" * 16
        await store.set("calls", cid, {"call_sid": cid})
        await store.set("settings", "knowledge", {"mode": mode})
        kb.forget(store)
        sink = StoreCallSink(
            store,
            recordings_bucket=None,
            project_id="p",
            call_id_pattern=CALL_ID_RE,
            tasks=tasks,
            events_queue="voa-events",
        )
        await sink.save_report(cid, _report())
        return cid, tasks.tasks

    cid, queued = asyncio.run(go("shadow"))
    assert [(t["queue"], t["path"], t["payload"]) for t in queued] == [
        ("voa-events", "/tasks/knowledge-shadow", {"call_id": cid})
    ]
    assert queued[0]["name"].startswith(f"kshadow-{cid}-")
    for mode in ("off", "live"):
        assert asyncio.run(go(mode))[1] == []
