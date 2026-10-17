"""/api/call-list*, /api/call-runs*, /api/call-settings*, /api/allowlist*."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request
from voa_core import budget, compliance
from voa_core.store import new_id, now_iso

from .auth import acting_user
from .context import ALLOWLIST, CALL_LIST, CALL_RUNS, HEX32_RE, LIST_RUNS, SETTINGS, Api
from .models import (
    AllowlistAdd,
    CallListEntryInput,
    CallRunInput,
    CallRunPatch,
    CancelEntries,
    Reschedule,
    RunStart,
)
from .routes_calls import api_of

router = APIRouter(prefix="/api", dependencies=[Depends(api_of)])
CURRENT_RUN = "current"


def _api(request: Request) -> Api:
    return request.app.state.api


def _check_hex(value: str, what: str) -> None:
    if not HEX32_RE.match(value):
        raise HTTPException(400, f"invalid {what}")


async def entry_or_404(api: Api, entry_id: str) -> dict:
    _check_hex(entry_id, "entry id")
    e = await api.store.get(CALL_LIST, entry_id)
    if not e:
        raise HTTPException(404, "Entry not found.")
    return {"entry_id": entry_id, **e}


async def _write_budget(api: Api, n: int = 1) -> None:
    for _ in range(n):
        try:
            ok = await budget.consume(
                api.store, "call_list_writes", api.cfg.dialing.max_list_writes_per_hour
            )
        except budget.BudgetUnavailable:
            raise HTTPException(503, "Write budget unavailable.") from None
        if not ok:
            raise HTTPException(429, "Too many call-list changes this hour.")


async def recall_settings(api: Api) -> compliance.RecallSettings:
    doc = await api.store.get(SETTINGS, "recall")
    return compliance.RecallSettings(**(doc or {}))


def _tz(api: Api) -> str:
    return api.cfg.site.timezone


def _parse_when(raw: str) -> datetime:
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "scheduled_for must be ISO 8601 with a timezone") from None
    if dt.tzinfo is None:
        raise HTTPException(400, "scheduled_for must include a timezone")
    return dt


# --------------------------------------------------------------------------- entries


@router.get("/call-list")
async def list_entries(request: Request, limit: int = 500):
    api = _api(request)
    rows = await api.store.query(CALL_LIST, order_by="created_at", limit=max(1, min(limit, 1000)))
    entries = [{"entry_id": i, **d} for i, d in rows]
    return {"entries": entries, "count": len(entries)}


def _new_entry(body: CallListEntryInput, now: str) -> dict[str, Any]:
    return {
        **body.model_dump(),
        "list_status": "pending",
        "attempts": 0,
        "created_at": now,
        "updated_at": now,
        "auto_recalls": 0,
    }


async def _check_capacity(api: Api, adding: int) -> None:
    existing = len(await api.store.query(CALL_LIST))
    if existing + adding > api.cfg.dialing.max_list_size:
        raise HTTPException(409, f"Call list is full ({api.cfg.dialing.max_list_size}).")


@router.post("/call-list", status_code=201)
async def add_entry(body: CallListEntryInput, request: Request):
    api = _api(request)
    await _write_budget(api)
    await _check_capacity(api, 1)
    entry_id, now = new_id(), now_iso(api.now())
    doc = _new_entry(body, now)
    await api.store.set(CALL_LIST, entry_id, doc)
    return {"entry_id": entry_id, **doc}


@router.post("/call-list/bulk", status_code=201)
async def add_bulk(body: list[CallListEntryInput], request: Request):
    api = _api(request)
    if not 1 <= len(body) <= 200:
        raise HTTPException(400, "Send between 1 and 200 entries per request.")
    ids = [e.patient_id for e in body]
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "Duplicate patient_id in upload.")  # review U-12
    await _write_budget(api, len(body))
    await _check_capacity(api, len(body))
    now = now_iso(api.now())
    out = []
    for e in body:
        entry_id, doc = new_id(), _new_entry(e, now)
        await api.store.set(CALL_LIST, entry_id, doc)
        out.append({"entry_id": entry_id, **doc})
    return {"entries": out, "count": len(out)}


@router.patch("/call-list/{entry_id}")
async def edit_entry(entry_id: str, body: CallListEntryInput, request: Request):
    api = _api(request)
    cur = await entry_or_404(api, entry_id)
    if cur.get("list_status") == "calling":
        raise HTTPException(409, "This patient is on a call right now.")
    await _write_budget(api)
    now = now_iso(api.now())
    patch = {**body.model_dump(), "updated_at": now}
    if cur.get("list_status") == "blocked":
        patch |= {"list_status": "pending", "last_error": ""}  # editing a blocked number retries it
    await api.store.merge(CALL_LIST, entry_id, patch)
    return {**cur, **patch}


@router.delete("/call-list/{entry_id}")
async def delete_entry(entry_id: str, request: Request):
    api = _api(request)
    cur = await entry_or_404(api, entry_id)
    if cur.get("list_status") == "calling":
        raise HTTPException(409, "This patient is on a call right now.")
    await api.store.delete(CALL_LIST, entry_id)
    await _drop_from_runs(api, entry_id)
    return {"deleted": entry_id}


@router.post("/call-list/reset")
async def reset_entries(request: Request):
    """Completed and blocked entries back to pending (blocked = usually a fixed config issue)."""
    api = _api(request)
    n = 0
    for entry_id, e in await api.store.query(CALL_LIST):
        if e.get("list_status") in ("completed", "blocked"):
            await api.store.merge(
                CALL_LIST,
                entry_id,
                {"list_status": "pending", "last_error": "", "updated_at": now_iso(api.now())},
            )
            n += 1
    return {"reset": n}


async def _scheduled_runs(api: Api) -> list[tuple[str, dict]]:
    return [(i, d) for i, d in await api.store.query(CALL_RUNS) if d.get("status") == "scheduled"]


async def _drop_from_runs(api: Api, entry_id: str) -> None:
    for run_id, run in await _scheduled_runs(api):
        ids = run.get("entry_ids") or []
        if entry_id in ids:
            rest = [x for x in ids if x != entry_id]
            if rest:
                await api.store.merge(
                    CALL_RUNS, run_id, {"entry_ids": rest, "updated_at": now_iso(api.now())}
                )
            else:
                await api.store.delete(CALL_RUNS, run_id)


@router.post("/call-list/cancel")
async def cancel_entries(body: CancelEntries, request: Request):
    api = _api(request)
    by = body.canceled_by or acting_user(request)
    now = now_iso(api.now())
    runs = await _scheduled_runs(api)
    results = []
    for entry_id in body.entry_ids:
        if not HEX32_RE.match(entry_id):
            results.append({"entry_id": entry_id, "status": "not_found"})
            continue
        e = await api.store.get(CALL_LIST, entry_id)
        if not e:
            results.append({"entry_id": entry_id, "status": "not_found"})
            continue
        if e.get("list_status") in ("calling", "completed"):
            results.append(
                {
                    "entry_id": entry_id,
                    "status": "in_progress" if e["list_status"] == "calling" else "completed",
                }
            )
            continue
        from_run = next(
            ((rid, d) for rid, d in runs if entry_id in (d.get("entry_ids") or [])), None
        )
        event = {
            "at": now,
            "reason": body.reason,
            "by": by,
            "run_id": from_run[0] if from_run else None,
            "run_scheduled_for": from_run[1].get("scheduled_for") if from_run else None,
        }
        await api.store.merge(
            CALL_LIST,
            entry_id,
            {
                "list_status": "canceled",
                "canceled": event,
                "cancel_history": [*(e.get("cancel_history") or []), event],
                "next_recall_at": None,
                "updated_at": now,
            },
        )
        if from_run:
            await _drop_from_runs(api, entry_id)
        results.append({"entry_id": entry_id, "status": "canceled"})
    return {"results": results}


@router.post("/call-list/{entry_id}/reschedule")
async def reschedule(entry_id: str, body: Reschedule, request: Request):
    api = _api(request)
    e = await entry_or_404(api, entry_id)
    if e.get("list_status") not in ("canceled", "pending"):
        raise HTTPException(
            409,
            "Only a cancelled or pending patient can be rescheduled "
            f"(this one is {e.get('list_status')}).",
        )
    runs = await _scheduled_runs(api)
    if any(entry_id in (d.get("entry_ids") or []) for _, d in runs):
        raise HTTPException(409, "Already in a scheduled run.")
    cfg, now = await recall_settings(api), now_iso(api.now())
    if body.run_id:
        _check_hex(body.run_id, "run id")
        target = next((d for rid, d in runs if rid == body.run_id), None)
        if target is None:
            raise HTTPException(404, "Run not found or already dialled.")
        when = _parse_when(target["scheduled_for"])
        if not compliance.in_calling_window(when, cfg, _tz(api)):
            raise HTTPException(
                400, f"That run is outside calling hours ({compliance.window_text(cfg, _tz(api))})."
            )
        ids = [*target.get("entry_ids", []), entry_id]
        if len(ids) > api.cfg.dialing.max_dials_per_run:
            raise HTTPException(400, f"That run is full ({api.cfg.dialing.max_dials_per_run}).")
        await api.store.merge(CALL_RUNS, body.run_id, {"entry_ids": ids, "updated_at": now})
        run_id, scheduled = body.run_id, target["scheduled_for"]
    else:
        if not body.scheduled_for:
            raise HTTPException(400, "Give run_id or scheduled_for.")
        when = _parse_when(body.scheduled_for)
        if when <= api.now():
            raise HTTPException(400, "scheduled_for must be in the future.")
        if not compliance.in_calling_window(when, cfg, _tz(api)):
            raise HTTPException(
                400, f"Outside calling hours ({compliance.window_text(cfg, _tz(api))})."
            )
        run_id, scheduled = new_id(), when.isoformat()
        await api.store.set(
            CALL_RUNS,
            run_id,
            {
                "scheduled_for": scheduled,
                "entry_ids": [entry_id],
                "label": (body.label or "").strip() or None,
                "status": "scheduled",
                "created_at": now,
                "updated_at": now,
            },
        )
    await api.store.merge(
        CALL_LIST,
        entry_id,
        {
            "list_status": "pending",
            "canceled": None,
            "rescheduled_at": now,
            "auto_recalls": 0,
            "updated_at": now,
        },
    )
    return {"entry_id": entry_id, "run_id": run_id, "scheduled_for": scheduled}


# --------------------------------------------------------------------------- manual run


def _run_public(run: dict | None) -> dict:
    if not run:
        return {"active": False, "run": None}
    return {"active": run.get("status") == "running", "run": run}


@router.get("/call-list/run")
async def get_run(request: Request):
    return _run_public(await _api(request).store.get(LIST_RUNS, CURRENT_RUN))


@router.post("/call-list/run")
async def start_run(body: RunStart, request: Request):
    """Start dialling now. Each patient becomes one named Cloud Task; the dial task
    itself waits for a free call slot (max_concurrent_calls), so runs survive
    restarts and never run in a detached in-memory loop (reviews K1/K2/C-8)."""
    from .routes_tasks import enqueue_dials

    api = _api(request)
    current = await api.store.get(LIST_RUNS, CURRENT_RUN)
    if current and current.get("status") == "running":
        raise HTTPException(409, "A run is already in progress.")
    rows = await api.store.query(CALL_LIST)
    now = api.now()
    if body.entry_ids is not None:
        wanted = set(body.entry_ids)
        entries = [
            (i, e)
            for i, e in rows
            if i in wanted and e.get("list_status") in ("pending", "blocked", "completed")
        ]
    else:
        entries = [(i, e) for i, e in rows if e.get("list_status") == "pending"]
    if body.due_only:
        entries = [(i, e) for i, e in entries if compliance.is_due(e, now)]
    if not entries:
        raise HTTPException(400, "Nothing to dial.")
    if len(entries) > api.cfg.dialing.max_dials_per_run:
        raise HTTPException(
            400, f"Too many patients for one run ({api.cfg.dialing.max_dials_per_run})."
        )
    run = await enqueue_dials(api, [i for i, _ in entries], label="manual")
    return _run_public(run)


@router.post("/call-list/run/cancel")
async def cancel_run(request: Request):
    api = _api(request)

    def mutate(run):
        if not run or run.get("status") != "running":
            return None
        return {
            **run,
            "cancel_requested": True,
            "status": "canceled",
            "finished_at": now_iso(api.now()),
        }

    return _run_public(await api.store.transact(LIST_RUNS, CURRENT_RUN, mutate))


# --------------------------------------------------------------------------- scheduled runs


@router.get("/call-runs")
async def list_runs(request: Request):
    rows = await _api(request).store.query(CALL_RUNS, order_by="scheduled_for")
    runs = [{"run_id": i, **d} for i, d in rows]
    return {"runs": runs, "count": len(runs)}


async def _validate_run(api: Api, scheduled_for: str, entry_ids: list[str]) -> str:
    when = _parse_when(scheduled_for)
    if when <= api.now():
        raise HTTPException(400, "scheduled_for must be in the future.")
    cfg = await recall_settings(api)
    if not compliance.in_calling_window(when, cfg, _tz(api)):
        raise HTTPException(
            400, f"Outside calling hours ({compliance.window_text(cfg, _tz(api))})."
        )
    if len(entry_ids) > api.cfg.dialing.max_dials_per_run:
        raise HTTPException(
            400, f"Too many patients for one run ({api.cfg.dialing.max_dials_per_run})."
        )
    for e in entry_ids:
        _check_hex(e, "entry id")
    return when.isoformat()


@router.post("/call-runs", status_code=201)
async def create_run(body: CallRunInput, request: Request):
    api = _api(request)
    when = await _validate_run(api, body.scheduled_for, body.entry_ids)
    run_id, now = new_id(), now_iso(api.now())
    doc = {
        "scheduled_for": when,
        "entry_ids": body.entry_ids,
        "label": (body.label or "").strip() or None,
        "status": "scheduled",
        "created_at": now,
        "updated_at": now,
    }
    await api.store.set(CALL_RUNS, run_id, doc)
    return {"run_id": run_id, **doc}


@router.patch("/call-runs/{run_id}")
async def patch_run(run_id: str, body: CallRunPatch, request: Request):
    api = _api(request)
    _check_hex(run_id, "run id")
    run = await api.store.get(CALL_RUNS, run_id)
    if not run:
        raise HTTPException(404, "Run not found.")
    if run.get("status") != "scheduled":
        raise HTTPException(409, "That run has already been dialled.")
    ids = body.entry_ids if body.entry_ids is not None else run.get("entry_ids", [])
    if body.entry_ids is not None and not ids:
        await api.store.delete(CALL_RUNS, run_id)  # an empty patient list deletes the run
        return {"deleted": run_id}
    when = await _validate_run(api, body.scheduled_for or run["scheduled_for"], ids)
    patch = {"scheduled_for": when, "entry_ids": ids, "updated_at": now_iso(api.now())}
    if body.label is not None:
        patch["label"] = body.label.strip() or None
    await api.store.merge(CALL_RUNS, run_id, patch)
    return {"run_id": run_id, **run, **patch}


@router.delete("/call-runs/{run_id}")
async def delete_run(run_id: str, request: Request):
    api = _api(request)
    _check_hex(run_id, "run id")
    if not await api.store.delete(CALL_RUNS, run_id):
        raise HTTPException(404, "Run not found.")
    return {"deleted": run_id}


# --------------------------------------------------------------------------- settings


@router.get("/call-settings")
async def get_settings(request: Request):
    api = _api(request)
    return {"recall": (await recall_settings(api)).model_dump(), "timezone": _tz(api)}


@router.put("/call-settings/recall")
async def put_recall(body: compliance.RecallSettings, request: Request):
    api = _api(request)
    await api.store.set(
        SETTINGS, "recall", {**body.model_dump(), "updated_by": acting_user(request)}
    )
    return {"recall": body.model_dump(), "timezone": _tz(api)}


# --------------------------------------------------------------------------- allowlist


@router.get("/allowlist")
async def get_allowlist(request: Request):
    api = _api(request)
    static = compliance.parse_allowlist(api.cfg.dialing.allowlist)
    entries = [
        {"phone": p, "label": "configured", "source": "env", "editable": False}
        for p in sorted(static.exact)
    ]
    entries += [
        {"phone": p + "*", "label": "configured prefix", "source": "env", "editable": False}
        for p in sorted(static.prefixes)
    ]
    for phone, d in await api.store.query(ALLOWLIST):
        entries.append(
            {
                "phone": phone,
                "label": d.get("label", ""),
                "created_at": d.get("created_at"),
                "source": "firestore",
                "editable": True,
            }
        )
    unrestricted = static.mode is compliance.AllowlistMode.OPEN
    mode = "open" if unrestricted else ("unset" if not entries else "list")
    return {"entries": entries, "count": len(entries), "mode": mode, "unrestricted": unrestricted}


@router.post("/allowlist", status_code=201)
async def add_allowed(body: AllowlistAdd, request: Request):
    api = _api(request)
    doc = {"label": body.label, "created_at": now_iso(api.now()), "added_by": acting_user(request)}
    await api.store.set(ALLOWLIST, body.phone, doc)
    return {"phone": body.phone, "source": "firestore", "editable": True, **doc}


@router.delete("/allowlist/{phone}")
async def remove_allowed(phone: str, request: Request):
    api = _api(request)
    phone = unquote(phone)
    if not await api.store.delete(ALLOWLIST, phone):
        raise HTTPException(404, "Not on the editable allowlist.")
    return {"deleted": phone}
