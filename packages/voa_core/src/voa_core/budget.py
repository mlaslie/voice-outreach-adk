"""Windowed budgets (dials per hour, list writes per hour, SMS per hour).

Reserve inside a transaction BEFORE the external call; release only when the
external call never happened. Fail closed: if the store is unreachable the
caller must refuse (an abuse budget that fails open is not a budget).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .store import DocStore

COLLECTION = "rate_limits"


class BudgetUnavailable(RuntimeError):
    """The budget store could not be read: refuse the action."""


async def consume(
    store: DocStore,
    name: str,
    limit: int,
    *,
    window_s: int = 3600,
    now: datetime | None = None,
    units: int = 1,
) -> bool:
    """Reserve ``units`` (one by default). False when the window's budget cannot
    hold them. ``limit <= 0`` = unlimited."""
    if limit <= 0:
        return True
    now = now or datetime.now(UTC)
    result = {"ok": False}

    def mutate(doc):
        doc = dict(doc or {})
        start = doc.get("window_start")
        start_dt = datetime.fromisoformat(start) if start else None
        if start_dt is None or now - start_dt >= timedelta(seconds=window_s):
            doc = {"window_start": now.isoformat(), "count": 0}
        n = max(1, int(units))
        count = int(doc.get("count", 0))
        if count >= limit or (n > 1 and count + n > limit):
            result["ok"] = False
            return None
        doc["count"] = count + n
        result["ok"] = True
        return doc

    try:
        await store.transact(COLLECTION, name, mutate)
    except Exception as exc:
        raise BudgetUnavailable(name) from exc
    return result["ok"]


async def release(store: DocStore, name: str, limit: int, *, units: int = 1) -> None:
    """Give units back after a failure that never reached the outside world. Best effort."""
    if limit <= 0:
        return
    n = max(1, int(units))

    def mutate(doc):
        if not doc or int(doc.get("count", 0)) <= 0:
            return None
        return {**doc, "count": max(0, int(doc["count"]) - n)}

    try:
        await store.transact(COLLECTION, name, mutate)
    except Exception:  # noqa: S110 - over-counting is the safe direction
        pass
