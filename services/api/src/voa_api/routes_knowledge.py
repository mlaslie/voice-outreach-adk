"""/api/admin/knowledge/*: the clinic's approved Q&A and its settings (admin only).

Knowledge base step 1 (docs/KNOWLEDGE-PLAN.md, docs/KNOWLEDGE-API.md). Firestore:

* ``knowledge_items/{item_id}``: {item_id, question, alt_questions, answer,
  category, status (draft|approved|retired), version, created_*, updated_*,
  approved_*}. Only approved items are ever said to a patient. Changing the
  content of an approved item makes a new version and puts it back to draft.
* ``settings/knowledge``: {enabled, channels {voice, sms}, allowed_categories,
  min_match, ask_satisfied}. Off by default. Medication, symptom and other can
  never be allowed.

Writes are audited by the route dependency (``audit.py``). "Try it" runs the real
answer path (``voa_core.knowledge.answer``) but ignores ``enabled`` and
``channels``, so an admin can test before switching it on.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from voa_core import knowledge as kb
from voa_core import patient_questions as pq
from voa_core.store import new_id, now_iso

from .auth import acting_user
from .context import Api
from .routes_calls import api_of

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/knowledge", dependencies=[Depends(api_of)])

_BODY_MAX = 4000  # generous: the real limits are checked by kb.clean_fields


def _by(request: Request) -> str:
    """The admin who clicked (X-Acting-User), else "admin"."""
    return (request.headers.get("x-acting-user") or "admin")[:200]


def _matcher(request: Request, api: Api):
    if api.knowledge_matcher is not None:
        return api.knowledge_matcher
    m = getattr(request.app.state, "knowledge_matcher", None)
    if m is None:
        from voa_agents.knowledge_matcher import GeminiMatcher

        m = request.app.state.knowledge_matcher = GeminiMatcher(api.cfg)
    return m


def _clean(fields: dict[str, Any]) -> dict[str, Any]:
    try:
        return kb.clean_fields(fields)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


def _topic(question: str, category: str) -> None:
    try:
        kb.check_topic(question, category)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


async def _get(api: Api, item_id: str) -> dict:
    if not item_id or len(item_id) > 64:
        raise HTTPException(404, "No such item.")
    doc = await api.store.get(kb.ITEMS, item_id)
    if not doc:
        raise HTTPException(404, "No such item.")
    return doc


def _changed(api: Api) -> None:
    kb.forget(api.store)  # this instance's caches; others pick it up within 60 s


# --------------------------------------------------------------------------- items


class ItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(max_length=_BODY_MAX)
    answer: str = Field(max_length=_BODY_MAX)
    category: str = Field(max_length=64)
    alt_questions: list[str] | None = Field(default=None, max_length=50)


class ItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str | None = Field(default=None, max_length=_BODY_MAX)
    answer: str | None = Field(default=None, max_length=_BODY_MAX)
    category: str | None = Field(default=None, max_length=64)
    alt_questions: list[str] | None = Field(default=None, max_length=50)


@router.get("/items")
async def list_items(request: Request):
    api: Api = request.app.state.api
    rows = await api.store.query(kb.ITEMS, order_by="created_at", descending=True)
    return {"items": [kb.public_item({**d, "item_id": d.get("item_id") or i}) for i, d in rows]}


@router.post("/items")
async def create_item(body: ItemCreate, request: Request):
    api: Api = request.app.state.api
    fields = _clean(body.model_dump(exclude_none=True))
    fields.setdefault("alt_questions", [])
    _topic(fields["question"], fields["category"])
    existing = await api.store.query(kb.ITEMS, limit=kb.MAX_ITEMS)
    if len(existing) >= kb.MAX_ITEMS:
        raise HTTPException(409, f"The knowledge base holds at most {kb.MAX_ITEMS} items.")
    item_id = new_id()
    doc = kb.new_item(item_id, fields, by=_by(request), at=now_iso(api.now()))
    await api.store.set(kb.ITEMS, item_id, doc)
    log.info("knowledge item created", extra={"event": "knowledge.item_created", "id": item_id})
    return kb.public_item(doc)


@router.patch("/items/{item_id}")
async def patch_item(item_id: str, body: ItemPatch, request: Request):
    api: Api = request.app.state.api
    sent = {k: getattr(body, k) for k in body.model_fields_set}
    if any(v is None for v in sent.values()):
        raise HTTPException(422, "A field sent must have a value.")
    fields = _clean(sent)
    cur = await _get(api, item_id)
    if "question" in fields or "category" in fields:
        _topic(
            fields.get("question", cur.get("question") or ""),
            fields.get("category", cur.get("category") or ""),
        )
    by, at = _by(request), now_iso(api.now())

    def apply(cur: dict | None) -> dict | None:
        return kb.edit_item(cur, fields, by=by, at=at) if cur else None

    doc = await api.store.transact(kb.ITEMS, item_id, apply)
    if not doc:
        raise HTTPException(404, "No such item.")
    _changed(api)
    return kb.public_item(doc)


async def _transition(request: Request, item_id: str, fn) -> dict:
    api: Api = request.app.state.api
    await _get(api, item_id)
    by, at = _by(request), now_iso(api.now())

    def apply(cur: dict | None) -> dict | None:
        return fn(cur, by=by, at=at) if cur else None

    doc = await api.store.transact(kb.ITEMS, item_id, apply)
    if not doc:
        raise HTTPException(404, "No such item.")
    _changed(api)
    return kb.public_item(doc)


@router.post("/items/{item_id}/approve")
async def approve_item(item_id: str, request: Request):
    item = await _transition(request, item_id, kb.approve_item)
    log.info(
        "knowledge item approved",
        extra={"event": "knowledge.item_approved", "id": item_id, "version": item["version"]},
    )
    return item


@router.post("/items/{item_id}/retire")
async def retire_item(item_id: str, request: Request):
    return await _transition(request, item_id, kb.retire_item)


@router.delete("/items/{item_id}")
async def delete_item(item_id: str, request: Request):
    api: Api = request.app.state.api
    doc = await _get(api, item_id)
    if doc.get("status") == kb.APPROVED:
        raise HTTPException(409, "Retire it first.")
    await api.store.delete(kb.ITEMS, item_id)
    _changed(api)
    return {"deleted": True}


# --------------------------------------------------------------------------- settings


def _settings_out(doc: dict | None) -> dict:
    s = kb.normalize_settings(doc)
    return {
        **s,
        "updated_at": (doc or {}).get("updated_at"),
        "updated_by": (doc or {}).get("updated_by"),
        "locked_categories": list(kb.LOCKED_CATEGORIES),
        "available_categories": [
            {"key": c, "label": pq.CATEGORY_LABELS[c]} for c in kb.ALLOWABLE_CATEGORIES
        ],
    }


class Channels(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voice: bool | None = None
    sms: bool | None = None


class SettingsPatch(BaseModel):
    """Partial update: a field left out is unchanged. Unknown fields are refused."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    channels: Channels | None = None
    allowed_categories: list[str] | None = Field(default=None, max_length=20)
    min_match: float | None = Field(default=None, ge=0.5, le=1.0)
    ask_satisfied: bool | None = None


@router.get("/settings")
async def get_settings(request: Request):
    api: Api = request.app.state.api
    return _settings_out(await api.store.get(kb.SETTINGS_COLLECTION, kb.SETTINGS_DOC))


@router.put("/settings")
async def put_settings(body: SettingsPatch, request: Request):
    api: Api = request.app.state.api
    sent = body.model_fields_set
    if any(getattr(body, k) is None for k in sent) or (
        body.channels is not None
        and any(getattr(body.channels, k) is None for k in body.channels.model_fields_set)
    ):
        raise HTTPException(422, "A field sent must have a value.")
    cats = body.allowed_categories
    if cats is not None:
        bad = [c for c in cats if c not in kb.ALLOWABLE_CATEGORIES]
        if bad:
            locked = [c for c in bad if c in kb.NEVER_ANSWERED]
            raise HTTPException(
                422,
                (
                    f"{', '.join(locked)} can never be answered by the agent."
                    if locked
                    else f"Unknown categories: {', '.join(bad)}."
                ),
            )
    by, at = _by(request), now_iso(api.now())

    def apply(cur: dict | None) -> dict:
        doc = kb.normalize_settings(cur)
        if "enabled" in sent:
            doc["enabled"] = body.enabled
        if "channels" in sent and body.channels is not None:
            for ch in body.channels.model_fields_set:
                doc["channels"][ch] = getattr(body.channels, ch)
        if "allowed_categories" in sent:
            doc["allowed_categories"] = [c for c in kb.ALLOWABLE_CATEGORIES if c in (cats or [])]
        if "min_match" in sent:
            doc["min_match"] = float(body.min_match)  # type: ignore[arg-type]
        if "ask_satisfied" in sent:
            doc["ask_satisfied"] = body.ask_satisfied
        return {**doc, "updated_at": at, "updated_by": by}

    doc = await api.store.transact(kb.SETTINGS_COLLECTION, kb.SETTINGS_DOC, apply)
    _changed(api)
    log.info(
        "knowledge settings changed",
        extra={"event": "knowledge.settings_updated", "fields": sorted(sent), "by": by},
    )
    return _settings_out(doc)


# --------------------------------------------------------------------------- try it


class TryIt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=pq.MAX_QUESTION_CHARS)


@router.post("/try")
async def try_question(body: TryIt, request: Request):
    """The real answer path, with ``enabled`` and ``channels`` ignored."""
    api: Api = request.app.state.api
    settings = await kb.load_settings(api.store, fresh=True)
    items = await kb.load_approved(api.store, fresh=True)
    result = await kb.answer(
        body.question, items=items, settings=settings, matcher=_matcher(request, api)
    )
    out = result.to_dict()
    if not settings["enabled"]:
        out["note"] = "test only: grounding is off"
    log.info(
        "knowledge try",
        extra={
            "event": "knowledge.try",
            "outcome": result.outcome,
            "reason": result.reason,
            "by": acting_user(request),
        },
    )
    return out
