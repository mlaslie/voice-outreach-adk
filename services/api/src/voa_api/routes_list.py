"""/api/call-list*, /api/call-runs*, /api/call-settings*, /api/allowlist*."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from voa_core import budget, compliance, timefmt
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

log = logging.getLogger(__name__)
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


async def nurse_time_format(api: Api) -> timefmt.TimeFormat:
    """The clinic's nurse-facing time format: Settings override, else site config."""
    doc = await api.store.get(SETTINGS, "display") or {}
    return timefmt.coerce(doc.get("time_format"), api.cfg.site.nurse_time_format)


async def hours_text(api: Api, cfg: compliance.RecallSettings) -> str:
    """Calling hours for a nurse-facing message, in the clinic's time format."""
    return compliance.window_text(cfg, _tz(api), await nurse_time_format(api))


async def _settings_body(api: Api, recall: compliance.RecallSettings | None = None) -> dict:
    from voa_core import organization

    default = api.cfg.site.org_name
    name = await organization.org_name(api.store, default)
    return {
        "recall": (recall or await recall_settings(api)).model_dump(),
        "timezone": _tz(api),
        "display": {"time_format": await nurse_time_format(api)},
        # The company name patients hear and read; "default" is the configured one.
        "organization": {"org_name": name, "default": default, "is_default": name == default},
    }


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
    """Cancel queued calls with a reason.

    A member of a run already being dialled can be cancelled too: its dial task
    re-checks the entry inside the claim transaction before ringing anyone, and a
    late result never turns the cancel back into "pending" (old proxy B-2). Read
    and write are one transaction: a claim that slips in between (status ->
    "calling") makes this report in_progress instead of overwriting a live call's
    row."""
    api = _api(request)
    by = body.canceled_by or acting_user(request)
    now = now_iso(api.now())
    all_runs = await api.store.query(CALL_RUNS)
    # Prefer the scheduled run a patient is waiting in, else one being dialled now.
    runs = [(i, d) for i, d in all_runs if d.get("status") == "scheduled"] + [
        (i, d)
        for i, d in all_runs
        if d.get("status") in ("dispatching", "dispatched") and not d.get("finished_at")
    ]
    results = []
    for entry_id in body.entry_ids:
        if not HEX32_RE.match(entry_id):
            results.append({"entry_id": entry_id, "status": "not_found"})
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
        verdict: dict[str, str] = {}

        def mutate(e, event=event, verdict=verdict):
            verdict.clear()
            if e is None:
                verdict["status"] = "not_found"
                return None
            if e.get("list_status") == "calling":
                verdict["status"] = "in_progress"
                return None
            if e.get("list_status") == "completed":
                verdict["status"] = "completed"
                return None
            verdict["status"] = "canceled"
            return {
                **e,
                "list_status": "canceled",
                "canceled": event,
                "cancel_history": [*(e.get("cancel_history") or []), event],
                "next_recall_at": None,
                "updated_at": now,
            }

        await api.store.transact(CALL_LIST, entry_id, mutate)
        status = verdict.get("status", "error")
        if status == "canceled" and from_run and from_run[1].get("status") == "scheduled":
            await _drop_from_runs(api, entry_id)  # a dispatched run's record is left as it was
        results.append({"entry_id": entry_id, "status": status})
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
                400,
                f"That run is outside calling hours ({await hours_text(api, cfg)}).",
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
                400,
                f"Outside calling hours ({await hours_text(api, cfg)}).",
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
    # A nurse naming rows ("Call now", "Call again") may dial a finished,
    # blocked or cancelled patient; the bulk button dials pending ones only.
    explicit = body.entry_ids is not None
    if explicit:
        wanted = set(body.entry_ids or [])
        entries = [(i, e) for i, e in rows if i in wanted and e.get("list_status") != "calling"]
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
    run = await enqueue_dials(
        api,
        [i for i, _ in entries],
        label="manual",
        explicit=explicit,
        campaign_id=body.campaign_id,
    )
    return _run_public(run)


@router.post("/call-list/run/cancel")
async def cancel_run(request: Request):
    """Stop a run: every dial task not yet started skips (it re-checks the run)."""
    from .routes_tasks import run_finished

    api = _api(request)
    stopped: dict[str, Any] = {}

    def mutate(run):
        stopped.clear()
        if not run or run.get("status") != "running":
            return None
        new = {
            **run,
            "cancel_requested": True,
            "status": "canceled",
            "finished_at": now_iso(api.now()),
        }
        stopped["run"] = new
        return new

    out = await api.store.transact(LIST_RUNS, CURRENT_RUN, mutate)
    if stopped.get("run"):
        await run_finished(api, stopped["run"])
    return _run_public(out)


# --------------------------------------------------------------------------- scheduled runs


@router.get("/call-runs")
async def list_runs(request: Request):
    rows = await _api(request).store.query(CALL_RUNS, order_by="scheduled_for")
    runs = [{"run_id": i, **d} for i, d in rows]
    return {"runs": runs, "count": len(runs)}


async def _require_hours(api: Api, when: datetime) -> None:
    cfg = await recall_settings(api)
    if not compliance.in_calling_window(when, cfg, _tz(api)):
        raise HTTPException(
            400,
            f"Outside calling hours ({await hours_text(api, cfg)}).",
        )


def _require_future(api: Api, raw: str) -> datetime:
    when = _parse_when(raw)
    if when <= api.now():
        raise HTTPException(400, "scheduled_for must be in the future.")
    return when


async def _validate_members(
    api: Api, entry_ids: list[str], *, exclude_run: str | None = None
) -> None:
    """Every patient exists, is not on the phone right now, is not already in
    another scheduled run, and the run fits the per-run dial cap."""
    if len(entry_ids) > api.cfg.dialing.max_dials_per_run:
        raise HTTPException(
            400, f"Too many patients for one run ({api.cfg.dialing.max_dials_per_run})."
        )
    for e in entry_ids:
        _check_hex(e, "entry id")
    missing, on_phone = [], []
    for eid in entry_ids:
        doc = await api.store.get(CALL_LIST, eid)
        if not doc:
            missing.append(eid)
        elif doc.get("list_status") == "calling":
            on_phone.append(doc.get("patient_name") or eid)
    if missing:
        raise HTTPException(400, f"Unknown patients: {', '.join(missing)}")
    if on_phone:
        raise HTTPException(409, "On the phone right now: " + ", ".join(on_phone))
    taken = {
        eid
        for rid, run in await _scheduled_runs(api)
        if rid != exclude_run
        for eid in run.get("entry_ids") or []
    }
    clash = [eid for eid in entry_ids if eid in taken]
    if clash:
        raise HTTPException(409, "Already in another scheduled run: " + ", ".join(clash))


async def _requeue_members(api: Api, entry_ids: list[str]) -> None:
    """Any patient can be put in a run: one cancelled, reached or blocked goes
    back to "pending" so the run dials them, exactly as a Reschedule would.
    Without this the dispatcher, which dials pending patients only, would skip
    them silently."""
    now = now_iso(api.now())
    for eid in entry_ids:

        def mutate(e):
            if not e or e.get("list_status") not in ("canceled", "completed", "blocked"):
                return None
            return {
                **e,
                "list_status": "pending",
                "canceled": None,
                "last_error": "",
                "rescheduled_at": now,
                "auto_recalls": 0,
                "updated_at": now,
            }

        await api.store.transact(CALL_LIST, eid, mutate)


@router.post("/call-runs", status_code=201)
async def create_run(body: CallRunInput, request: Request):
    api = _api(request)
    when = _require_future(api, body.scheduled_for)
    await _require_hours(api, when)
    await _validate_members(api, body.entry_ids)
    await _requeue_members(api, body.entry_ids)
    run_id, now = new_id(), now_iso(api.now())
    doc = {
        "scheduled_for": when.isoformat(),
        "entry_ids": body.entry_ids,
        "label": (body.label or "").strip() or None,
        "status": "scheduled",
        "created_at": now,
        "updated_at": now,
    }
    if body.campaign_id:
        doc["campaign_id"] = body.campaign_id
    await api.store.set(CALL_RUNS, run_id, doc)
    return {"run_id": run_id, **doc}


@router.patch("/call-runs/{run_id}")
async def patch_run(run_id: str, body: CallRunPatch, request: Request):
    """Change a scheduled run's time, patients or label. Removing every patient
    deletes the run. A new time, or an added patient, must land inside calling
    hours; only removing patients from a run is allowed as-is."""
    api = _api(request)
    _check_hex(run_id, "run id")
    run = await api.store.get(CALL_RUNS, run_id)
    if not run:
        raise HTTPException(404, "Run not found.")
    if run.get("status") != "scheduled":
        raise HTTPException(409, "That run has already been dialled.")
    if body.entry_ids is not None and not body.entry_ids:
        await api.store.delete(CALL_RUNS, run_id)  # an empty patient list deletes the run
        return {"deleted": run_id}
    patch: dict[str, Any] = {"updated_at": now_iso(api.now())}
    if body.scheduled_for is not None:
        patch["scheduled_for"] = _require_future(api, body.scheduled_for).isoformat()
    added: list[str] = []
    if body.entry_ids is not None:
        await _validate_members(api, body.entry_ids, exclude_run=run_id)
        patch["entry_ids"] = body.entry_ids
        added = sorted(set(body.entry_ids) - set(run.get("entry_ids") or []))
    if body.scheduled_for is not None or added:
        await _require_hours(api, _parse_when(patch.get("scheduled_for") or run["scheduled_for"]))
    if body.label is not None:
        patch["label"] = body.label.strip() or None
    if body.campaign_id is not None:
        patch["campaign_id"] = body.campaign_id
    if added:
        await _requeue_members(api, added)
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
    return await _settings_body(_api(request))


@router.put("/call-settings/recall")
async def put_recall(body: compliance.RecallSettings, request: Request):
    api = _api(request)
    await api.store.set(
        SETTINGS, "recall", {**body.model_dump(), "updated_by": acting_user(request)}
    )
    return await _settings_body(api, body)


class OrganizationSettings(BaseModel):
    """The company name in the voice script, voicemail and texts. Empty resets it to
    the configured default (site.org_name)."""

    org_name: str | None = None


@router.put("/call-settings/organization")
async def put_organization(body: OrganizationSettings, request: Request):
    from voa_core import organization

    api = _api(request)
    if body.org_name and body.org_name.strip():
        try:
            name = organization.validate(body.org_name)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        await api.store.set(
            organization.COLLECTION,
            organization.DOC_ID,
            {
                "org_name": name,
                "updated_by": acting_user(request),
                "updated_at": now_iso(api.now()),
            },
        )
    else:
        await api.store.delete(organization.COLLECTION, organization.DOC_ID)
    organization.forget(api.store)
    log.info(
        "company name changed",
        extra={
            "event": "organization.changed",
            "by": acting_user(request),
            "reset": not body.org_name,
        },
    )
    return await _settings_body(api)


class DisplaySettings(BaseModel):
    """How times show to nurses. Patients always get 12-hour times."""

    time_format: timefmt.TimeFormat


@router.put("/call-settings/display")
async def put_display(body: DisplaySettings, request: Request):
    api = _api(request)
    await api.store.set(
        SETTINGS, "display", {**body.model_dump(), "updated_by": acting_user(request)}
    )
    return await _settings_body(api)


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
