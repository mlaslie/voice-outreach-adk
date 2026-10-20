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
