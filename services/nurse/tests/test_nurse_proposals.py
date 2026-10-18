"""Proposals: single-use, 15-minute TTL, bound to the conversation, purged when
stale, and shared by every instance through the DocStore."""

from voa_core.store import MemoryStore
from voa_nurse.proposals import COLLECTION, ProposalStore


class Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


async def test_claim_is_single_use():
    store = MemoryStore()
    ps = ProposalStore(store, "conv-1")
    p = await ps.create("place_call", {"body": {"x": 1}}, "Call?")
    assert len(p.id) == 8
    got = await ps.claim(p.id)
    assert got and got.kind == "place_call" and got.params == {"body": {"x": 1}}
    assert await ps.claim(p.id) is None
    assert await store.query(COLLECTION) == []  # removed once used


async def test_expires_after_ttl():
    clock = Clock()
    ps = ProposalStore(MemoryStore(), "conv-1", ttl_s=900, clock=clock)
    p = await ps.create("place_call", {}, "Call?")
    clock.t += 899
    assert [x.id for x in await ps.pending()] == [p.id]
    clock.t += 2
    assert await ps.pending() == []
    assert await ps.claim(p.id) is None


async def test_bound_to_the_conversation():
    store = MemoryStore()
    mine, theirs = ProposalStore(store, "conv-1"), ProposalStore(store, "conv-2")
    p = await mine.create("place_call", {}, "Call?")
    assert await theirs.claim(p.id) is None
    assert await theirs.discard(p.id) is False
    assert await mine.claim(p.id) is not None


async def test_forged_and_malformed_ids_do_nothing():
    ps = ProposalStore(MemoryStore(), "conv-1")
    await ps.create("place_call", {}, "Call?")
    for bad in ("forged", "", "../../x", "ABCDEFGH1", "00000000"):
        assert await ps.claim(bad) is None
    assert len(await ps.pending()) == 1


async def test_survives_another_instance():
    """Two ProposalStores over one store stand for two Cloud Run instances."""
    store = MemoryStore()
    p = await ProposalStore(store, "conv-1").create("cooldown", {"enabled": True}, "Turn on?")
    got = await ProposalStore(store, "conv-1").claim(p.id)
    assert got and got.params == {"enabled": True}


async def test_purge_removes_expired_and_used_only():
    clock = Clock()
    store = MemoryStore()
    ps = ProposalStore(store, "conv-1", ttl_s=900, clock=clock)
    old = await ps.create("a", {}, "A?")
    clock.t += 600
    fresh = await ps.create("b", {}, "B?")
    other = await ProposalStore(store, "conv-2", clock=clock).create("c", {}, "C?")
    clock.t += 400  # old is 1000 s old (expired), fresh is 400 s
    assert await ps.purge_stale() == 1
    ids = {i.rsplit("_", 1)[-1] for i, _ in await store.query(COLLECTION)}
    assert ids == {fresh.id, other.id} and old.id not in ids


async def test_discard_cancels():
    ps = ProposalStore(MemoryStore(), "conv-1")
    p = await ps.create("a", {}, "A?")
    assert await ps.discard(p.id) is True
    assert await ps.claim(p.id) is None


async def test_expire_at_is_set_for_a_firestore_ttl_policy():
    store = MemoryStore()
    await ProposalStore(store, "conv-1").create("a", {}, "A?")
    ((_, doc),) = await store.query(COLLECTION)
    assert doc["expire_at"].tzinfo is not None
