"""Document store: one small interface, a Firestore implementation and an in-memory one.

Services depend on :class:`DocStore`, never on the Firestore SDK directly, so the
API and gateway can be tested end to end with :class:`MemoryStore`.

Documents are plain dicts. Timestamps are ISO-8601 UTC strings (what the UI
consumes); ``now_iso()`` is the one way to make one.
"""

from __future__ import annotations

import asyncio
import copy
import threading
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

Doc = dict[str, Any]
Op = Literal["==", "!=", "<", "<=", ">", ">=", "in", "array_contains"]
Mutator = Callable[[Doc | None], Doc | None]


def now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="milliseconds")


def new_id() -> str:
    return uuid.uuid4().hex  # 32 hex chars; the UI validates this shape


@dataclass(frozen=True)
class Where:
    field: str
    op: Op
    value: Any


class DocStore(Protocol):
    async def get(self, coll: str, doc_id: str) -> Doc | None: ...
    async def set(self, coll: str, doc_id: str, data: Doc) -> None: ...
    async def merge(self, coll: str, doc_id: str, data: Doc) -> None: ...
    async def delete(self, coll: str, doc_id: str) -> bool: ...
    async def create(self, coll: str, doc_id: str, data: Doc) -> bool:
        """Create only if absent. Returns False if the doc already existed."""
        ...

    async def transact(self, coll: str, doc_id: str, fn: Mutator) -> Doc | None:
        """Atomic read-modify-write. ``fn`` gets the current doc (or None) and returns
        the new doc, or None to leave it untouched. Returns what was written (or the
        unchanged current doc). ``fn`` may run more than once; keep it pure."""
        ...

    async def query(
        self,
        coll: str,
        where: list[Where] | None = None,
        order_by: str | None = None,
        descending: bool = False,
        limit: int | None = None,
    ) -> list[tuple[str, Doc]]: ...

    def watch(self, coll: str, doc_id: str) -> AsyncIterator[Doc | None]:
        """Yield the doc now and after every change, until the consumer stops."""
        ...


# --------------------------------------------------------------------------- memory


def _match(doc: Doc, w: Where) -> bool:
    v = doc.get(w.field)
    if w.op == "==":
        return v == w.value
    if w.op == "!=":
        return v != w.value
    if w.op == "in":
        return v in w.value
    if w.op == "array_contains":
        return isinstance(v, list) and w.value in v
    if v is None:
        return False
    return {"<": v < w.value, "<=": v <= w.value, ">": v > w.value, ">=": v >= w.value}[w.op]


class MemoryStore:
    """In-process store with the same semantics, for tests and local runs."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Doc]] = defaultdict(dict)
        self._lock = asyncio.Lock()
        self._watchers: dict[tuple[str, str], list[asyncio.Queue]] = defaultdict(list)

    def _notify(self, coll: str, doc_id: str) -> None:
        doc = copy.deepcopy(self._data[coll].get(doc_id))
        for q in self._watchers.get((coll, doc_id), []):
            q.put_nowait(doc)

    async def get(self, coll, doc_id):
        return copy.deepcopy(self._data[coll].get(doc_id))

    async def set(self, coll, doc_id, data):
        self._data[coll][doc_id] = copy.deepcopy(data)
        self._notify(coll, doc_id)

    async def merge(self, coll, doc_id, data):
        cur = self._data[coll].setdefault(doc_id, {})
        cur.update(copy.deepcopy(data))
        self._notify(coll, doc_id)

    async def delete(self, coll, doc_id):
        existed = self._data[coll].pop(doc_id, None) is not None
        self._notify(coll, doc_id)
        return existed

    async def create(self, coll, doc_id, data):
        async with self._lock:
            if doc_id in self._data[coll]:
                return False
            self._data[coll][doc_id] = copy.deepcopy(data)
        self._notify(coll, doc_id)
        return True

    async def transact(self, coll, doc_id, fn):
        async with self._lock:
            cur = copy.deepcopy(self._data[coll].get(doc_id))
            new = fn(cur)
            if new is None:
                return cur
            self._data[coll][doc_id] = copy.deepcopy(new)
        self._notify(coll, doc_id)
        return copy.deepcopy(new)

    async def query(self, coll, where=None, order_by=None, descending=False, limit=None):
        rows = [(i, copy.deepcopy(d)) for i, d in self._data[coll].items()]
        for w in where or []:
            rows = [r for r in rows if _match(r[1], w)]
        if order_by:
            present = [r for r in rows if r[1].get(order_by) is not None]
            rows = sorted(present, key=lambda r: r[1][order_by], reverse=descending)
        return rows[:limit] if limit else rows

    async def watch(self, coll, doc_id):
        q: asyncio.Queue = asyncio.Queue()
        self._watchers[(coll, doc_id)].append(q)
        try:
            yield copy.deepcopy(self._data[coll].get(doc_id))
            while True:
                yield await q.get()
        finally:
            self._watchers[(coll, doc_id)].remove(q)


# --------------------------------------------------------------------------- firestore


class FirestoreStore:
    """Firestore (Native) implementation using the async client.

    ``watch`` uses the sync client's ``on_snapshot`` (the async client has no
    listeners), bridged onto the event loop with ``call_soon_threadsafe``.
    """

    def __init__(self, project_id: str, database: str = "(default)", prefix: str = ""):
        from google.cloud import firestore

        self._fs = firestore
        self._client = firestore.AsyncClient(project=project_id, database=database)
        self._project, self._database = project_id, database
        self._prefix = prefix  # e.g. "test_" for integration tests
        self._sync_client = None
        self._sync_lock = threading.Lock()

    def _ref(self, coll: str, doc_id: str):
        return self._client.collection(self._prefix + coll).document(doc_id)

    async def get(self, coll, doc_id):
        snap = await self._ref(coll, doc_id).get()
        return snap.to_dict() if snap.exists else None

    async def set(self, coll, doc_id, data):
        await self._ref(coll, doc_id).set(data)

    async def merge(self, coll, doc_id, data):
        await self._ref(coll, doc_id).set(data, merge=True)

    async def delete(self, coll, doc_id):
        ref = self._ref(coll, doc_id)
        existed = (await ref.get()).exists
        await ref.delete()
        return existed

    async def create(self, coll, doc_id, data):
        from google.api_core.exceptions import AlreadyExists

        try:
            await self._ref(coll, doc_id).create(data)
            return True
        except AlreadyExists:
            return False

    async def transact(self, coll, doc_id, fn):
        ref = self._ref(coll, doc_id)
        transaction = self._client.transaction()
        result: dict[str, Doc | None] = {}

        @self._fs.async_transactional
        async def run(tx):
            snap = await ref.get(transaction=tx)
            cur = snap.to_dict() if snap.exists else None
            new = fn(copy.deepcopy(cur))
            if new is None:
                result["doc"] = cur
                return
            tx.set(ref, new)
            result["doc"] = new

        await run(transaction)
        return result.get("doc")

    async def query(self, coll, where=None, order_by=None, descending=False, limit=None):
        from google.cloud.firestore_v1.base_query import FieldFilter

        # No composite indexes, by design (they fail at runtime when missing, and the
        # old system lost time to exactly that). Filters use single-field indexes;
        # when the sort field differs from every filter field we sort client-side.
        # Fine at demo scale; revisit with declared indexes if collections grow large.
        q = self._client.collection(self._prefix + coll)
        where = where or []
        for w in where:
            q = q.where(filter=FieldFilter(w.field, w.op, w.value))
        server_sort = order_by and all(w.field == order_by for w in where)
        if server_sort:
            q = q.order_by(
                order_by,
                direction=self._fs.Query.DESCENDING if descending else self._fs.Query.ASCENDING,
            )
            if limit:
                q = q.limit(limit)
        rows = [(s.id, s.to_dict()) async for s in q.stream()]
        if order_by and not server_sort:
            rows = sorted(
                (r for r in rows if r[1].get(order_by) is not None),
                key=lambda r: r[1][order_by],
                reverse=descending,
            )
            rows = rows[:limit] if limit else rows
        return rows

    async def watch(self, coll, doc_id):
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        with self._sync_lock:
            if self._sync_client is None:
                self._sync_client = self._fs.Client(project=self._project, database=self._database)
        ref = self._sync_client.collection(self._prefix + coll).document(doc_id)

        def on_snapshot(snaps, _changes, _read_time):
            for s in snaps:
                loop.call_soon_threadsafe(q.put_nowait, s.to_dict() if s.exists else None)

        watch = ref.on_snapshot(on_snapshot)
        try:
            while True:
                yield await q.get()
        finally:
            watch.unsubscribe()
