"""Pending confirmations, stored in ``nurse_proposals`` (shared by every instance).

Anything that dials a phone, cancels, reschedules, changes a tier or a setting is
never executed directly. A ``propose_*`` action stores the request here and shows
a confirmation card; only an explicit confirmation runs it:

* a Confirm button click, which carries just the proposal id, or
* a typed ``confirm <id>``.

A bare "ok" / "yes" never executes anything (review C-5).

Each proposal is:

* bound to its conversation: the document id is derived from the conversation
  AND the proposal id, so an id from another conversation finds nothing;
* single-use: claiming it is a transaction that marks it used;
* short-lived: ``ttl_s`` (15 min by default). Expired and used proposals are
  purged at the top of every turn, and ``expire_at`` lets a Firestore TTL policy
  remove anything a purge missed.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from voa_core.store import DocStore, Where

COLLECTION = "nurse_proposals"
ID_RE = re.compile(r"^[0-9a-f]{8}$")


def conversation_key(conversation: str) -> str:
    return hashlib.sha256(conversation.encode()).hexdigest()[:32]


@dataclass
class Proposal:
    id: str
    kind: str
    params: dict[str, Any]
    title: str
    created: float = field(default_factory=time.time)


class ProposalStore:
    def __init__(self, store: DocStore, conversation: str, *, ttl_s: int = 900, clock=time.time):
        self.store = store
        self.key = conversation_key(conversation)
        self.ttl_s = ttl_s
        self.clock = clock

    def _doc_id(self, pid: str) -> str:
        return f"{self.key}_{pid}"

    async def create(self, kind: str, params: dict[str, Any], title: str) -> Proposal:
        now = self.clock()
        for _ in range(5):
            pid = secrets.token_hex(4)
            doc = {
                "conversation": self.key,
                "kind": kind,
                "params": params,
                "title": title,
                "created": now,
                "expires": now + self.ttl_s,
                "used": False,
                # Firestore TTL policy field (a Timestamp).
                "expire_at": datetime.fromtimestamp(now, UTC) + timedelta(seconds=self.ttl_s),
            }
            if await self.store.create(COLLECTION, self._doc_id(pid), doc):
                return Proposal(pid, kind, params, title, now)
        raise RuntimeError("could not allocate a proposal id")

    async def claim(self, pid: str) -> Proposal | None:
        """The proposal, exactly once, if it belongs to this conversation and has not
        expired. Every other case (unknown, forged, other conversation, used,
        expired) returns None and changes nothing."""
        pid = (pid or "").strip().lower()
        if not ID_RE.match(pid):
            return None
        now = self.clock()
        claimed: dict[str, Any] = {}

        def mutate(doc):
            claimed.clear()  # a Firestore transaction may retry this function
            if (
                not doc
                or doc.get("conversation") != self.key
                or doc.get("used")
                or now >= float(doc.get("expires") or 0)
            ):
                return None
            claimed["doc"] = doc
            return {**doc, "used": True, "used_at": now}

        await self.store.transact(COLLECTION, self._doc_id(pid), mutate)
        doc = claimed.get("doc")
        if not doc:
            return None
        await self.store.delete(COLLECTION, self._doc_id(pid))
        return Proposal(pid, doc["kind"], doc["params"], doc["title"], doc["created"])

    async def discard(self, pid: str) -> bool:
        pid = (pid or "").strip().lower()
        if not ID_RE.match(pid):
            return False
        doc = await self.store.get(COLLECTION, self._doc_id(pid))
        if not doc or doc.get("conversation") != self.key:
            return False
        return await self.store.delete(COLLECTION, self._doc_id(pid))

    async def pending(self) -> list[Proposal]:
        now = self.clock()
        rows = await self.store.query(COLLECTION, [Where("conversation", "==", self.key)])
        return [
            Proposal(i.rsplit("_", 1)[-1], d["kind"], d["params"], d["title"], d["created"])
            for i, d in rows
            if not d.get("used") and now < float(d.get("expires") or 0)
        ]

    async def purge_stale(self) -> int:
        """Delete this conversation's expired or used proposals (top of every turn)."""
        now = self.clock()
        rows = await self.store.query(COLLECTION, [Where("conversation", "==", self.key)])
        n = 0
        for doc_id, d in rows:
            if d.get("used") or now >= float(d.get("expires") or 0):
                n += bool(await self.store.delete(COLLECTION, doc_id))
        return n
