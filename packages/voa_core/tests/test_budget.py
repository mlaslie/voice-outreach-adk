import asyncio
from datetime import UTC, datetime, timedelta

from voa_core import budget
from voa_core.store import MemoryStore


async def test_budget_window_limit_and_release():
    s = MemoryStore()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    results = await asyncio.gather(*(budget.consume(s, "dials", 3, now=t0) for _ in range(5)))
    assert sorted(results) == [False, False, True, True, True]
    await budget.release(s, "dials", 3)
    assert await budget.consume(s, "dials", 3, now=t0) is True
    assert await budget.consume(s, "dials", 3, now=t0 + timedelta(hours=1)) is True  # new window
    assert await budget.consume(s, "dials", 0) is True  # unlimited


async def test_budget_fails_closed():
    class Broken(MemoryStore):
        async def transact(self, *a, **k):
            raise RuntimeError("down")

    try:
        await budget.consume(Broken(), "dials", 3)
    except budget.BudgetUnavailable:
        return
    raise AssertionError("expected BudgetUnavailable")
