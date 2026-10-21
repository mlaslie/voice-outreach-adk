"""The knowledge agent's admin API, shadow drafts and the nurse rating route.

docs/KNOWLEDGE-PLAN.md (v2) and docs/KNOWLEDGE-API.md. Firestore:

* ``knowledge_items/{item_id}``: example Q&A {item_id, question, alt_questions,
  answer, category, status (draft|approved|retired), version, created_*,
  updated_*, approved_*}.
* ``knowledge_documents/{doc_id}``: guidelines {doc_id, title, body, procedures,
  care_settings, status, version, ...}, the same lifecycle.
* ``settings/knowledge``: {mode off|shadow|live, channels {voice, sms},
  allowed_categories, min_support, ask_satisfied}. Off by default; a v1 document
  (``enabled``) migrates on read. Medication, symptom and other can never be
  allowed.

Only approved content is ever used. Editing approved content makes a new draft
version. Admin writes are audited by the route dependency (``audit.py``); "Try
it" is not (it changes nothing) and runs the real agent in any mode.

Shadow mode: :func:`run_shadow` (the ``/tasks/knowledge-shadow`` task) stores what
the agent would have said on each of a call's questions; nurses rate it with
``POST /api/calls/{call_id}/questions/{index}/shadow-review``.
"""

from __future__ import annotations

import logging
from datetime import UTC
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from voa_core import knowledge as kb
from voa_core import patient_questions as pq
from voa_core.store import new_id, now_iso

from .auth import acting_user
from .context import CALL_ID_RE, CALLS, Api
from .routes_calls import api_of

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/knowledge", dependencies=[Depends(api_of)])
calls_router = APIRouter(prefix="/api", dependencies=[Depends(api_of)])

_BODY_MAX = 4000  # generous: the real limits are checked by kb.clean_fields
_DOC_BODY_MAX = kb.MAX_BODY * 2  # generous: kb.clean_document_fields checks the limits


def _by(request: Request) -> str:
    """The admin who clicked (X-Acting-User), else "admin"."""
    return (request.headers.get("x-acting-user") or "admin")[:200]


def _check(fn, fields: dict[str, Any]) -> dict[str, Any]:
    try:
        return fn(fields)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


def _changed(api: Api) -> None:
    kb.forget(api.store)  # this instance's caches; others pick it up within 60 s


def _sent(body: BaseModel) -> dict[str, Any]:
    sent = {k: getattr(body, k) for k in body.model_fields_set}
    if any(v is None for v in sent.values()):
        raise HTTPException(422, "A field sent must have a value.")
    return sent


async def _get(api: Api, collection: str, key: str, what: str) -> dict:
    if not key or len(key) > 64:
        raise HTTPException(404, f"No such {what}.")
    doc = await api.store.get(collection, key)
    if not doc:
        raise HTTPException(404, f"No such {what}.")
    return doc


async def _transition(api: Api, request: Request, collection: str, key: str, fn) -> dict:
    by, at = _by(request), now_iso(api.now())

    def apply(cur: dict | None) -> dict | None:
        return fn(cur, by=by, at=at) if cur else None

    doc = await api.store.transact(collection, key, apply)
    _changed(api)
    return doc or await api.store.get(collection, key) or {}


# --------------------------------------------------------------------------- examples


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


def _warm(api: Api) -> None:
    """Opening the editor warms the agent, so the first "Try it" isn't a cold
    sign-in that overruns the budget."""
    if api.cfg.env not in ("dev", "test"):
        from voa_agents.voice.knowledge import warm_up

        warm_up(api.get_knowledge_agent())


@router.get("/items")
async def list_items(request: Request):
    api: Api = request.app.state.api
    _warm(api)
    rows = await api.store.query(kb.ITEMS, order_by="created_at", descending=True)
    return {"items": [kb.public_item({**d, "item_id": d.get("item_id") or i}) for i, d in rows]}


@router.post("/items")
async def create_item(body: ItemCreate, request: Request):
    api: Api = request.app.state.api
    fields = _check(kb.clean_fields, body.model_dump(exclude_none=True))
    fields.setdefault("alt_questions", [])
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
    fields = _check(kb.clean_fields, _sent(body))
    await _get(api, kb.ITEMS, item_id, "item")
    by, at = _by(request), now_iso(api.now())
    doc = await _transition(
        api, request, kb.ITEMS, item_id, lambda cur, **_: kb.edit_item(cur, fields, by=by, at=at)
    )
    return kb.public_item(doc)


@router.post("/items/{item_id}/approve")
async def approve_item(item_id: str, request: Request):
    api: Api = request.app.state.api
    await _get(api, kb.ITEMS, item_id, "item")
    item = kb.public_item(await _transition(api, request, kb.ITEMS, item_id, kb.approve_item))
    log.info(
        "knowledge item approved",
        extra={"event": "knowledge.item_approved", "id": item_id, "version": item["version"]},
    )
    return item


@router.post("/items/{item_id}/retire")
async def retire_item(item_id: str, request: Request):
    api: Api = request.app.state.api
    await _get(api, kb.ITEMS, item_id, "item")
    return kb.public_item(await _transition(api, request, kb.ITEMS, item_id, kb.retire_item))


@router.delete("/items/{item_id}")
async def delete_item(item_id: str, request: Request):
    api: Api = request.app.state.api
    doc = await _get(api, kb.ITEMS, item_id, "item")
    if doc.get("status") == kb.APPROVED:
        raise HTTPException(409, "Retire it first.")
    await api.store.delete(kb.ITEMS, item_id)
    _changed(api)
    return {"deleted": True}


# --------------------------------------------------------------------------- guidelines


class DocCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(max_length=_BODY_MAX)
    body: str = Field(max_length=_DOC_BODY_MAX)
    procedures: list[str] | None = Field(default=None, max_length=50)
    care_settings: list[str] | None = Field(default=None, max_length=5)


class DocPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=_BODY_MAX)
    body: str | None = Field(default=None, max_length=_DOC_BODY_MAX)
    procedures: list[str] | None = Field(default=None, max_length=50)
    care_settings: list[str] | None = Field(default=None, max_length=5)


def _pub_doc(doc_id: str, d: dict) -> dict:
    return kb.public_document({**d, "doc_id": d.get("doc_id") or doc_id})


@router.get("/documents")
async def list_documents(request: Request):
    api: Api = request.app.state.api
    _warm(api)
    rows = await api.store.query(kb.DOCUMENTS, order_by="created_at", descending=True)
    return {"documents": [_pub_doc(i, d) for i, d in rows]}


@router.post("/documents")
async def create_document(body: DocCreate, request: Request):
    api: Api = request.app.state.api
    fields = _check(kb.clean_document_fields, body.model_dump(exclude_none=True))
    existing = await api.store.query(kb.DOCUMENTS, limit=kb.MAX_DOCUMENTS)
    if len(existing) >= kb.MAX_DOCUMENTS:
        raise HTTPException(409, f"The knowledge base holds at most {kb.MAX_DOCUMENTS} guidelines.")
    doc_id = new_id()
    doc = kb.new_document(doc_id, fields, by=_by(request), at=now_iso(api.now()))
    await api.store.set(kb.DOCUMENTS, doc_id, doc)
    log.info("knowledge guideline created", extra={"event": "knowledge.doc_created", "id": doc_id})
    return kb.public_document(doc)


@router.patch("/documents/{doc_id}")
async def patch_document(doc_id: str, body: DocPatch, request: Request):
    api: Api = request.app.state.api
    fields = _check(kb.clean_document_fields, _sent(body))
    await _get(api, kb.DOCUMENTS, doc_id, "guideline")
    by, at = _by(request), now_iso(api.now())
    doc = await _transition(
        api,
        request,
        kb.DOCUMENTS,
        doc_id,
        lambda cur, **_: kb.edit_document(cur, fields, by=by, at=at),
    )
    return _pub_doc(doc_id, doc)


@router.post("/documents/{doc_id}/approve")
async def approve_document(doc_id: str, request: Request):
    api: Api = request.app.state.api
    cur = await _get(api, kb.DOCUMENTS, doc_id, "guideline")
    if cur.get("status") != kb.APPROVED:
        approved = await kb.load_approved_documents(api.store, fresh=True)
        total = sum(kb.document_chars(d) for d in approved if d.get("doc_id") != doc_id)
        if total + kb.document_chars(cur) > kb.MAX_APPROVED_CHARS:
            raise HTTPException(
                409,
                f"Approved guidelines can hold at most {kb.MAX_APPROVED_CHARS:,} characters "
                f"in total ({total:,} in use). Retire one first.",
            )
    doc = await _transition(api, request, kb.DOCUMENTS, doc_id, kb.approve_item)
    log.info(
        "knowledge guideline approved",
        extra={"event": "knowledge.doc_approved", "id": doc_id, "version": doc.get("version")},
    )
    return _pub_doc(doc_id, doc)


@router.post("/documents/{doc_id}/retire")
async def retire_document(doc_id: str, request: Request):
    api: Api = request.app.state.api
    await _get(api, kb.DOCUMENTS, doc_id, "guideline")
    return _pub_doc(doc_id, await _transition(api, request, kb.DOCUMENTS, doc_id, kb.retire_item))


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, request: Request):
    api: Api = request.app.state.api
    doc = await _get(api, kb.DOCUMENTS, doc_id, "guideline")
    if doc.get("status") == kb.APPROVED:
        raise HTTPException(409, "Retire it first.")
    await api.store.delete(kb.DOCUMENTS, doc_id)
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
    """Partial update: a field left out is unchanged. Unknown fields (including
    the v1 ``enabled`` and ``min_match``) are refused."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["off", "shadow", "live"] | None = None
    channels: Channels | None = None
    allowed_categories: list[str] | None = Field(default=None, max_length=20)
    min_support: float | None = Field(
        default=None, ge=kb.MIN_SUPPORT_RANGE[0], le=kb.MIN_SUPPORT_RANGE[1]
    )
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
        doc = kb.normalize_settings(cur)  # migrates a v1 document; drops enabled/min_match
        if "mode" in sent:
            doc["mode"] = body.mode
        if "channels" in sent and body.channels is not None:
            for ch in body.channels.model_fields_set:
                doc["channels"][ch] = getattr(body.channels, ch)
        if "allowed_categories" in sent:
            doc["allowed_categories"] = [c for c in kb.ALLOWABLE_CATEGORIES if c in (cats or [])]
        if "min_support" in sent:
            doc["min_support"] = float(body.min_support)  # type: ignore[arg-type]
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


class TryContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    procedure: str | None = Field(default=None, max_length=kb.MAX_PROCEDURE)
    care_setting: Literal["inpatient", "outpatient"] | None = None
    days_since_surgery: int | None = Field(default=None, ge=0, le=3650)
    is_diabetic: bool | None = None


class TryIt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=pq.MAX_QUESTION_CHARS)
    context: TryContext | None = None


@router.post("/try")
async def try_question(body: TryIt, request: Request):
    """The real agent, in any mode (the ``note`` says when it is not live)."""
    api: Api = request.app.state.api
    settings = await kb.load_settings(api.store, fresh=True)
    snapshot = await kb.load_snapshot(api.store, fresh=True)
    ctx = body.context.model_dump(exclude_none=True) if body.context else {}
    if procedure := " ".join((ctx.get("procedure") or "").split()):
        ctx["procedure"] = procedure
    else:
        ctx.pop("procedure", None)
    result = await api.get_knowledge_agent().answer(
        body.question, ctx, settings=settings, snapshot=snapshot
    )
    out = result.to_dict()
    mode = settings["mode"]
    out["note"] = None if mode == kb.MODE_LIVE else f"test only: mode is {mode}"
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


# --------------------------------------------------------------------------- shadow mode


async def run_shadow(api: Api, call_id: str) -> dict[str, Any]:
    """The ``/tasks/knowledge-shadow`` task: run the agent on each of the call's
    questions with the call's patient context and store ``shadow`` {outcome,
    reason, answer, sources, support_score, at} on each.

    Idempotent: a question that already has ``shadow`` is skipped (also inside
    the transaction, so a duplicate delivery never overwrites one). It never
    changes ``follow_up``, the triage or anything else on the call."""
    call = await api.store.get(CALLS, call_id) if CALL_ID_RE.match(call_id or "") else None
    if not call:
        return {"skipped": "no call"}
    qs = (call.get("survey_responses") or {}).get("patient_questions")
    if not isinstance(qs, list):
        return {"skipped": "no questions"}
    todo = [
        (i, q)
        for i, q in enumerate(qs)
        if isinstance(q, dict) and str(q.get("text") or "").strip() and not q.get("shadow")
    ]
    if not todo:
        return {"skipped": "nothing to draft"}
    settings = await kb.load_settings(api.store, fresh=True)
    snapshot = await kb.load_snapshot(api.store)
    ctx = kb.patient_context(call.get("patient_context"), today=api.now().astimezone(UTC).date())
    agent = api.get_knowledge_agent()
    drafts: dict[int, tuple[str, dict]] = {}
    for i, q in todo:
        r = await agent.answer(q["text"], ctx, settings=settings, snapshot=snapshot)
        drafts[i] = (
            q["text"],
            {
                "outcome": r.outcome,
                "reason": r.reason,
                "answer": r.answer,
                "sources": [dict(s) for s in r.sources],
                "support_score": r.support_score,
                "at": now_iso(api.now()),
            },
        )
    stored: list[int] = []

    def apply(cur: dict | None) -> dict | None:
        stored.clear()
        if cur is None:
            return None
        survey = dict(cur.get("survey_responses") or {})
        cur_qs = [
            dict(q) if isinstance(q, dict) else q for q in survey.get("patient_questions") or []
        ]
        for i, (text, shadow) in drafts.items():
            q = cur_qs[i] if i < len(cur_qs) else None
            if isinstance(q, dict) and q.get("text") == text and not q.get("shadow"):
                q["shadow"] = shadow
                stored.append(i)
        if not stored:
            return None
        survey["patient_questions"] = cur_qs
        return {**cur, "survey_responses": survey, "updated_at": now_iso(api.now())}

    await api.store.transact(CALLS, call_id, apply)
    log.info(
        "knowledge shadow drafts stored",
        extra={"event": "knowledge.shadow", "stored": len(stored), "drafted": len(drafts)},
    )
    return {"stored": len(stored)}


class ShadowReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["good", "not_right"]
    note: str | None = Field(default=None, max_length=pq.MAX_REVIEW_NOTE)


@calls_router.post("/calls/{call_id}/questions/{index}/shadow-review")
async def shadow_review(call_id: str, index: int, body: ShadowReview, request: Request):
    """A nurse rates the agent's shadow draft for one question: Good or Not right."""
    if not CALL_ID_RE.match(call_id):
        raise HTTPException(400, "invalid call id")
    note = " ".join((body.note or "").split()) or None
    if note and (found := kb.phi_problems(note)):
        raise HTTPException(
            422,
            f"The note looks like it holds patient details ({', '.join(found)}). Leave them out.",
        )
    api: Api = request.app.state.api
    review = {
        "verdict": body.verdict,
        "note": note,
        "by": acting_user(request),
        "at": now_iso(api.now()),
    }
    found_q: dict[str, Any] = {}

    def apply(cur: dict | None) -> dict | None:
        found_q.clear()
        if cur is None:
            return None
        survey = dict(cur.get("survey_responses") or {})
        qs = survey.get("patient_questions")
        if not isinstance(qs, list) or not 0 <= index < len(qs):
            return None
        q = qs[index]
        if not isinstance(q, dict) or not q.get("shadow"):
            return None
        qs = list(qs)
        qs[index] = found_q["q"] = {**q, "shadow_review": review}
        survey["patient_questions"] = qs
        return {**cur, "survey_responses": survey, "updated_at": now_iso(api.now())}

    await api.store.transact(CALLS, call_id, apply)
    if not found_q:
        raise HTTPException(404, "No agent draft for that question.")
    log.info(
        "knowledge shadow reviewed",
        extra={"event": "knowledge.shadow_review", "verdict": body.verdict},
    )
    return found_q["q"]
