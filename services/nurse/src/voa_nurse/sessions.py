"""An ADK session service on ``voa_core.store.DocStore`` (collection ``nurse_sessions``).

Cloud Run scales to zero, runs several instances and replaces them on every
deploy, so the conversation (the model's history and the assistant's per-
conversation state such as text mode) lives in Firestore, not in memory. Tests
use ``MemoryStore`` with the same semantics.

One document per conversation. Events and state are stored as JSON strings:
Firestore rejects arrays nested in arrays, which tool arguments can contain.

History is bounded (``max_events`` and a byte budget, under Firestore's 1 MiB
document limit) and is only ever cut at a user TEXT event. Gemini rejects a
history that starts with a model turn or a function response, and then every
later turn fails too (review A-3): a cut anywhere else could leave an orphaned
function call or response. :meth:`rollback` removes a turn that failed or ran
out of time half way, for the same reason.

Documents hold PHI (tool results include patient details), so each carries
``expire_at`` for a Firestore TTL policy.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from google.adk.events import Event
from google.adk.sessions import BaseSessionService, Session
from google.adk.sessions.base_session_service import GetSessionConfig, ListSessionsResponse
from google.adk.sessions.state import State
from voa_core.store import DocStore, Where

log = logging.getLogger(__name__)

COLLECTION = "nurse_sessions"
MAX_BYTES = 800_000


def _doc_id(app_name: str, user_id: str, session_id: str) -> str:
    return hashlib.sha256(f"{app_name}|{user_id}|{session_id}".encode()).hexdigest()[:40]


def is_user_text(event_json: dict) -> bool:
    """A real user message: role user, at least one text part, no function response."""
    content = event_json.get("content") or {}
    if event_json.get("author") != "user" or content.get("role", "user") != "user":
        return False
    parts = content.get("parts") or []
    return any(p.get("text") for p in parts) and not any(p.get("function_response") for p in parts)


def trim(events: list[str], max_events: int, max_bytes: int = MAX_BYTES) -> list[str]:
    """Keep at most ``max_events`` events and ``max_bytes`` bytes, starting at a user
    text event. The earliest user text event inside the window; failing that, the
    latest one anywhere; failing that, nothing."""

    def fits(window: list[str]) -> bool:
        return len(window) <= max_events and sum(len(e) for e in window) <= max_bytes

    starts = [i for i, e in enumerate(events) if is_user_text(json.loads(e))]
    if not starts:
        return []  # never a history that opens on a model turn or a function response
    for i in starts:
        if fits(events[i:]):
            return events[i:]
    # One huge exchange: keep the latest one even over budget (dropping it would
    # orphan the function calls of the turn in progress).
    return events[starts[-1] :]


class DocStoreSessionService(BaseSessionService):
    def __init__(self, store: DocStore, *, max_events: int = 60, ttl_hours: int = 24):
        self.store = store
        self.max_events = max_events
        self.ttl = timedelta(hours=ttl_hours)

    # --- helpers -------------------------------------------------------------------

    def _expire_at(self) -> datetime:
        return datetime.now(UTC) + self.ttl

    @staticmethod
    def _to_session(doc: dict, config: GetSessionConfig | None = None) -> Session:
        events = [Event.model_validate_json(e) for e in doc.get("events_json") or []]
        if config and config.after_timestamp is not None:
            events = [e for e in events if e.timestamp >= config.after_timestamp]
        if config and config.num_recent_events is not None:
            events = events[-config.num_recent_events :] if config.num_recent_events else []
        return Session(
            id=doc["session_id"],
            app_name=doc["app_name"],
            user_id=doc["user_id"],
            state=json.loads(doc.get("state_json") or "{}"),
            events=events,
            last_update_time=float(doc.get("last_update_time") or 0.0),
        )

    # --- BaseSessionService ----------------------------------------------------------

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> Session:
        session_id = session_id or uuid.uuid4().hex
        clean = {k: v for k, v in (state or {}).items() if not k.startswith(State.TEMP_PREFIX)}
        now = time.time()
        doc = {
            "app_name": app_name,
            "user_id": user_id,
            "session_id": session_id,
            "state_json": json.dumps(clean),
            "events_json": [],
            "last_update_time": now,
            "expire_at": self._expire_at(),
        }
        if not await self.store.create(COLLECTION, _doc_id(app_name, user_id, session_id), doc):
            raise ValueError(f"session {session_id} already exists")
        return self._to_session(doc)

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: GetSessionConfig | None = None,
    ) -> Session | None:
        doc = await self.store.get(COLLECTION, _doc_id(app_name, user_id, session_id))
        return self._to_session(doc, config) if doc else None

    async def list_sessions(
        self, *, app_name: str, user_id: str | None = None
    ) -> ListSessionsResponse:
        where = [Where("app_name", "==", app_name)]
        if user_id is not None:
            where.append(Where("user_id", "==", user_id))
        rows = await self.store.query(COLLECTION, where)
        sessions = [
            Session(
                id=d["session_id"],
                app_name=d["app_name"],
                user_id=d["user_id"],
                last_update_time=float(d.get("last_update_time") or 0.0),
            )
            for _, d in rows
        ]
        return ListSessionsResponse(sessions=sorted(sessions, key=lambda s: s.last_update_time))

    async def delete_session(self, *, app_name: str, user_id: str, session_id: str) -> None:
        await self.store.delete(COLLECTION, _doc_id(app_name, user_id, session_id))

    async def append_event(self, session: Session, event: Event) -> Event:
        if event.partial:
            return event
        event = await super().append_event(session, event)
        delta = {
            k: v
            for k, v in ((event.actions.state_delta if event.actions else None) or {}).items()
            if not k.startswith(State.TEMP_PREFIX)
        }
        encoded = event.model_dump_json(exclude_none=True)
        max_events = self.max_events

        def mutate(doc):
            if doc is None:
                return None
            state = json.loads(doc.get("state_json") or "{}")
            state.update(delta)
            events = trim([*(doc.get("events_json") or []), encoded], max_events)
            return {
                **doc,
                "state_json": json.dumps(state, default=str),
                "events_json": events,
                "last_update_time": event.timestamp,
                "expire_at": self._expire_at(),
            }

        written = await self.store.transact(
            COLLECTION, _doc_id(session.app_name, session.user_id, session.id), mutate
        )
        if written is None:
            log.warning("append to a missing session", extra={"event": "nurse.session.missing"})
        session.last_update_time = event.timestamp
        return event

    # --- the assistant's own operations ------------------------------------------------

    async def update_state(
        self, *, app_name: str, user_id: str, session_id: str, delta: dict[str, Any]
    ) -> None:
        def mutate(doc):
            if doc is None:
                return None
            state = json.loads(doc.get("state_json") or "{}")
            state.update(delta)
            return {**doc, "state_json": json.dumps(state, default=str)}

        await self.store.transact(COLLECTION, _doc_id(app_name, user_id, session_id), mutate)

    async def rollback(
        self, *, app_name: str, user_id: str, session_id: str, keep_ids: set[str]
    ) -> int:
        """Drop every event not in ``keep_ids`` (the events that existed before a turn
        that failed or timed out). Returns how many were dropped."""
        dropped = {"n": 0}

        def mutate(doc):
            if doc is None:
                return None
            kept = [e for e in doc.get("events_json") or [] if json.loads(e).get("id") in keep_ids]
            dropped["n"] = len(doc.get("events_json") or []) - len(kept)
            return {**doc, "events_json": kept} if dropped["n"] else None

        await self.store.transact(COLLECTION, _doc_id(app_name, user_id, session_id), mutate)
        return dropped["n"]
