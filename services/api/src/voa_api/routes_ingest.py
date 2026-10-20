"""/api/v1/call-batches: the EHR feed's way to schedule calls (docs/ADMIN-PLAN.md §3).

A service that polls the EHR (FHIR, HL7 ADT through an integration engine, a
scheduled extract) builds the day's list and PUTs it here as one batch of
tranches. Each tranche becomes an ordinary call run; each patient an ordinary
call-list entry tagged with its source, so the console, recall, calling hours and
dialing all work exactly as for a list typed in by hand.

* **Idempotent.** The key is ``(source, batch_id)``; each patient upserts on
  ``(source, external_id)``. Re-sending the same batch changes nothing; sending it
  without an item cancels that item if it has not been called yet.
* **Per-item results.** One bad record never fails the batch: every item comes
  back created / updated / unchanged / cancelled / rejected (with a reason).
* **No PHI in responses.** Results carry external ids and statuses only.

Only the ``voa-ingest`` identity may write (auth.ROUTE_POLICY); every write is in
the audit log.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from voa_core.store import new_id, now_iso

from .auth import acting_user
from .context import CALL_LIST, CALL_RUNS, Api
from .dialer import DialError, check_allowed
from .models import CallListEntryInput, CancelEntries
from .routes_calls import api_of
from .routes_list import (
    _check_capacity,
    _new_entry,
    _parse_when,
    _write_budget,
    cancel_entries,
    hours_text,
    recall_settings,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", dependencies=[Depends(api_of)])

BATCHES = "call_batches"
KEYS = "ingest_keys"
SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
MAX_CALLS = 500
#: Entry fields the feed owns (a change to any of them is an update).
FEED_FIELDS = (
    "phone",
    "patient_name",
    "patient_id",
    "patient_dob",
    "operation_name",
    "operation_date",
    "is_diabetic",
)


class IngestCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(pattern=ID_RE.pattern)
    mrn: str
    name: str
    dob: str
    phone: str
    procedure: str | None = None
    procedure_date: str | None = None
    is_diabetic: bool = False
    language: str | None = Field(default=None, max_length=16)


class IngestTranche(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=60)
    scheduled_for: str
    calls: list[IngestCall] = Field(min_length=1)


class IngestBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tranches: list[IngestTranche] = Field(min_length=1, max_length=20)


def _ids(source: str, batch_id: str) -> str:
    if not SOURCE_RE.match(source):
        raise HTTPException(400, "source must be lower-case letters, digits and dashes (≤32).")
    if not ID_RE.match(batch_id):
        raise HTTPException(400, "batch_id must be letters, digits and _ . : - (≤64).")
    return f"{source}__{batch_id}"


def _run_id(source: str, batch_id: str, label: str) -> str:
    """Stable per (batch, tranche label), so a re-send updates the same run."""
    return hashlib.sha256(f"{source}|{batch_id}|{label}".encode()).hexdigest()[:32]


def _fingerprint(body: IngestBatch) -> str:
    return hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True).encode()).hexdigest()


def _entry_input(c: IngestCall) -> CallListEntryInput:
    return CallListEntryInput(
        phone=c.phone,
        patient_name=c.name,
        patient_id=c.mrn,
        patient_dob=c.dob,
        operation_name=c.procedure,
        operation_date=c.procedure_date,
        is_diabetic=c.is_diabetic,
    )


def _why(exc: ValidationError) -> str:
    e = exc.errors()[0]
    field = ".".join(str(p) for p in e.get("loc", ()) if p != "body") or "record"
    return f"{field}: {e.get('msg', 'invalid')}"


async def _tranche_problem(api: Api, raw: str) -> tuple[datetime | None, str | None]:
    from voa_core import compliance

    try:
        when = _parse_when(raw)
    except HTTPException as exc:
        return None, str(exc.detail)
    if when <= api.now():
        return None, "scheduled_for is in the past"
    cfg = await recall_settings(api)
    if not compliance.in_calling_window(when, cfg, api.cfg.site.timezone):
        return None, f"outside calling hours ({await hours_text(api, cfg)})"
    return when, None


@router.put("/call-batches/{source}/{batch_id}")
async def put_batch(source: str, batch_id: str, body: IngestBatch, request: Request):
    api: Api = request.app.state.api
    key = _ids(source, batch_id)
    total = sum(len(t.calls) for t in body.tranches)
    if total > MAX_CALLS:
        raise HTTPException(400, f"At most {MAX_CALLS} calls per batch.")
    labels = [t.label for t in body.tranches]
    if len(set(labels)) != len(labels):
        raise HTTPException(400, "Tranche labels must be unique within a batch.")
    ext = [c.external_id for t in body.tranches for c in t.calls]
    if len(set(ext)) != len(ext):
        raise HTTPException(400, "external_id appears more than once in the batch.")

    fp = _fingerprint(body)
    prior = await api.store.get(BATCHES, key)
    if prior and prior.get("fingerprint") == fp and prior.get("status") == "active":
        return _public(prior, replay=True)  # same batch again: nothing changes

    now = now_iso(api.now())
    by = f"ehr:{source}"
    items: list[dict[str, Any]] = []
    runs: list[str] = []
    new_entries = 0

    for t in body.tranches:
        when, problem = await _tranche_problem(api, t.scheduled_for)
        accepted: list[str] = []
        for c in t.calls:
            item = {"external_id": c.external_id, "tranche": t.label}
            if problem:
                items.append({**item, "status": "rejected", "reason": problem})
                continue
            try:
                entry_in = _entry_input(c)
            except ValidationError as exc:
                items.append({**item, "status": "rejected", "reason": _why(exc)})
                continue
            try:
                await check_allowed(api, entry_in.phone)
            except DialError as exc:
                items.append({**item, "status": "rejected", "reason": str(exc.detail)})
                continue
            fields = {
                **{k: v for k, v in entry_in.model_dump().items() if k in FEED_FIELDS},
                "language": c.language,
                "source": source,
                "external_id": c.external_id,
                "batch_id": batch_id,
            }
            kref = await api.store.get(KEYS, f"{source}__{c.external_id}")
            entry_id = (kref or {}).get("entry_id")
            existing = await api.store.get(CALL_LIST, entry_id) if entry_id else None
            if existing is None:
                if new_entries == 0:
                    await _check_capacity(api, total)
                    await _write_budget(api, total)
                entry_id = new_id()
                doc = {**_new_entry(entry_in, now), **fields}
                await api.store.set(CALL_LIST, entry_id, doc)
                await api.store.set(
                    KEYS, f"{source}__{c.external_id}", {"entry_id": entry_id, "at": now}
                )
                new_entries += 1
                items.append({**item, "status": "created", "entry_id": entry_id})
            else:
                changed = any(existing.get(k) != v for k, v in fields.items())
                if existing.get("list_status") == "calling":
                    items.append(
                        {
                            **item,
                            "status": "unchanged",
                            "entry_id": entry_id,
                            "reason": "on a call now",
                        }
                    )
                elif changed:
                    patch = {**fields, "updated_at": now}
                    if existing.get("list_status") == "canceled":
                        patch.update(list_status="pending", canceled=None)
                    await api.store.merge(CALL_LIST, entry_id, patch)
                    items.append({**item, "status": "updated", "entry_id": entry_id})
                else:
                    items.append({**item, "status": "unchanged", "entry_id": entry_id})
            if existing is None or existing.get("list_status") != "calling":
                accepted.append(entry_id)

        rid = _run_id(source, batch_id, t.label)
        run = await api.store.get(CALL_RUNS, rid)
        if accepted and when is not None and (run is None or run.get("status") == "scheduled"):
            await api.store.set(
                CALL_RUNS,
                rid,
                {
                    **(run or {"created_at": now, "status": "scheduled"}),
                    "scheduled_for": when.isoformat(),
                    "entry_ids": accepted,
                    "label": f"{t.label} ({source})",
                    "source": source,
                    "batch_id": batch_id,
                    "updated_at": now,
                },
            )
            runs.append(rid)
        elif run is not None and run.get("status") == "scheduled" and not accepted:
            await api.store.delete(CALL_RUNS, rid)

    # Items in the previous version of this batch but not in this one: cancel them
    # (only if not called yet) and drop runs whose tranche disappeared.
    current = {i["external_id"] for i in items}
    dropped = [
        i
        for i in (prior or {}).get("items", [])
        if i["external_id"] not in current and i.get("entry_id")
    ]
    if dropped:
        res = await cancel_entries(
            CancelEntries(
                entry_ids=[i["entry_id"] for i in dropped],
                reason="Removed from the EHR batch",
                canceled_by=by,
            ),
            request,
        )
        verdict = {r["entry_id"]: r["status"] for r in res.get("results", [])}
        for i in dropped:
            st = verdict.get(i["entry_id"])
            items.append(
                {
                    "external_id": i["external_id"],
                    "tranche": i.get("tranche"),
                    "entry_id": i["entry_id"],
                    "status": "cancelled" if st == "canceled" else "unchanged",
                    "reason": None if st == "canceled" else f"not cancelled: {st}",
                }
            )
    for rid in (prior or {}).get("runs", []):
        if rid not in runs:
            old = await api.store.get(CALL_RUNS, rid)
            if old and old.get("status") == "scheduled":
                await api.store.delete(CALL_RUNS, rid)

    doc = {
        "source": source,
        "batch_id": batch_id,
        "status": "active",
        "fingerprint": fp,
        "received_at": (prior or {}).get("received_at") or now,
        "updated_at": now,
        "updated_by": acting_user(request),
        "runs": runs,
        "items": items,
    }
    await api.store.set(BATCHES, key, doc)
    log.info(
        "call batch received",
        extra={"event": "ingest.batch", "source": source, "counts": _counts(items)},
    )
    return _public(doc)


def _counts(items: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i["status"]] = out.get(i["status"], 0) + 1
    return out


def _public(doc: dict, *, replay: bool = False) -> dict:
    return {
        "source": doc["source"],
        "batch_id": doc["batch_id"],
        "status": doc.get("status"),
        "replay": replay,
        "received_at": doc.get("received_at"),
        "updated_at": doc.get("updated_at"),
        "counts": _counts(doc.get("items", [])),
        "runs": doc.get("runs", []),
        "items": [
            {k: i.get(k) for k in ("external_id", "tranche", "entry_id", "status", "reason")}
            for i in doc.get("items", [])
        ],
    }


@router.get("/call-batches/{source}/{batch_id}")
async def get_batch(source: str, batch_id: str, request: Request):
    """The batch with each item's current call-list status, for reconciliation."""
    api: Api = request.app.state.api
    doc = await api.store.get(BATCHES, _ids(source, batch_id))
    if not doc:
        raise HTTPException(404, "Batch not found.")
    out = _public(doc)
    for i in out["items"]:
        e = await api.store.get(CALL_LIST, i["entry_id"]) if i.get("entry_id") else None
        i["list_status"] = (e or {}).get("list_status")
        i["attempts"] = (e or {}).get("attempts")
    return out


@router.delete("/call-batches/{source}/{batch_id}")
async def delete_batch(source: str, batch_id: str, request: Request):
    """Cancel everything in the batch that has not been called yet."""
    api: Api = request.app.state.api
    key = _ids(source, batch_id)
    doc = await api.store.get(BATCHES, key)
    if not doc:
        raise HTTPException(404, "Batch not found.")
    ids = [i["entry_id"] for i in doc.get("items", []) if i.get("entry_id")]
    res = (
        await cancel_entries(
            CancelEntries(entry_ids=ids, reason="EHR batch withdrawn", canceled_by=f"ehr:{source}"),
            request,
        )
        if ids
        else {"results": []}
    )
    for rid in doc.get("runs", []):
        run = await api.store.get(CALL_RUNS, rid)
        if run and run.get("status") == "scheduled":
            await api.store.delete(CALL_RUNS, rid)
    await api.store.merge(BATCHES, key, {"status": "withdrawn", "updated_at": now_iso(api.now())})
    return {
        "source": source,
        "batch_id": batch_id,
        "status": "withdrawn",
        "results": res.get("results", []),
    }


@router.get("/call-batches")
async def list_batches(request: Request, limit: int = 100):
    """Admin view: recent batches with their counts (no patient data)."""
    api: Api = request.app.state.api
    rows = await api.store.query(BATCHES)
    rows.sort(key=lambda r: r[1].get("updated_at", ""), reverse=True)
    return {
        "batches": [
            {k: v for k, v in _public(d).items() if k != "items"}
            for _, d in rows[: max(1, min(limit, 500))]
        ]
    }
