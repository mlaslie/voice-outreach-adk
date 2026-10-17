"""Store contract: the memory and Firestore implementations must behave the same.

Firestore runs only with VOA_RUN_LIVE=1 (uses the configured project, collections
prefixed with a random test id, cleaned up afterwards).
"""

import asyncio
import os
import uuid

import pytest
from voa_core.store import MemoryStore, Where


def _firestore():
    from voa_core.config import load_config
    from voa_core.store import FirestoreStore

    return FirestoreStore(load_config().gcp.project_id, prefix=f"test_{uuid.uuid4().hex[:8]}_")


@pytest.fixture(params=["memory", pytest.param("firestore", marks=pytest.mark.live)])
async def store(request):
    s = MemoryStore() if request.param == "memory" else _firestore()
    yield s
    if request.param == "firestore":
        for coll in ("calls", "list"):
            for doc_id, _ in await s.query(coll):
                await s.delete(coll, doc_id)


async def test_crud_and_merge(store):
    assert await store.get("calls", "a") is None
    await store.set("calls", "a", {"x": 1, "y": {"z": 2}})
    await store.merge("calls", "a", {"x": 3, "w": True})
    assert await store.get("calls", "a") == {"x": 3, "y": {"z": 2}, "w": True}
    assert await store.delete("calls", "a") is True
    assert await store.delete("calls", "a") is False


async def test_create_is_set_if_absent(store):
    assert await store.create("calls", "b", {"n": 1}) is True
    assert await store.create("calls", "b", {"n": 2}) is False
    assert (await store.get("calls", "b"))["n"] == 1
    await store.delete("calls", "b")


async def test_transact_serializes_increments(store):
    def inc(doc):
        doc = doc or {"count": 0}
        doc["count"] += 1
        return doc

    await asyncio.gather(*(store.transact("calls", "c", inc) for _ in range(5)))
    assert (await store.get("calls", "c"))["count"] == 5
    unchanged = await store.transact("calls", "c", lambda d: None)
    assert unchanged["count"] == 5
    await store.delete("calls", "c")


async def test_query_filter_order_limit(store):
    for i, status in enumerate(["pending", "completed", "pending", "pending"]):
        await store.set("list", f"e{i}", {"list_status": status, "created_at": f"2026-01-0{i + 1}"})
    rows = await store.query(
        "list",
        [Where("list_status", "==", "pending")],
        order_by="created_at",
        descending=True,
        limit=2,
    )
    assert [r[0] for r in rows] == ["e3", "e2"]
    for i in range(4):
        await store.delete("list", f"e{i}")


async def test_watch_sees_changes(store):
    seen = []

    async def consume():
        async for doc in store.watch("calls", "w"):
            seen.append(doc)
            if doc and doc.get("answered_by"):
                return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.5 if os.environ.get("VOA_RUN_LIVE") else 0.01)
    await store.set("calls", "w", {"answered_by": "human"})
    await asyncio.wait_for(task, 15)
    assert seen[-1] == {"answered_by": "human"}
    await store.delete("calls", "w")
