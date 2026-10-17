"""Cloud Tasks and Cloud Scheduler targets (OIDC-authenticated, tasks-invoker only).

/tasks/dial            dial one call-list entry (waits for a free call slot by returning 503)
/tasks/call-terminal   a call reached a terminal status: settle its entry and run, auto-recall,
                       and hand missed calls to the SMS follow-up
/cron/dispatch-due     every minute: dial scheduled runs and entries whose time has come
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from voa_core import compliance, lifecycle
from voa_core.store import Where, new_id, now_iso

from .auth import require_caller, task_callers
from .context import CALL_LIST, CALL_RUNS, CALLS, LIST_RUNS, Api
from .dialer import DialError, dial
from .models import PlaceCallRequest

log = logging.getLogger(__name__)
CURRENT_RUN = "current"
RECALLABLE = {"rna", "left_vmail", "busy", "interrupted"}
SMS_FOLLOW_UP = {"rna", "busy", "left_vmail"}


def _tasks_api(request: Request) -> Api:
    require_caller(request, task_callers(request))
    return request.app.state.api


router = APIRouter(dependencies=[Depends(_tasks_api)])


async def enqueue_dials(
    api: Api, entry_ids: list[str], *, label: str, scheduled_run_id: str | None = None
) -> dict[str, Any]:
    run_id, now = new_id(), now_iso(api.now())
    run = {
        "run_id": run_id,
        "status": "running",
        "label": label,
        "started_at": now,
        "finished_at": None,
        "total": len(entry_ids),
        "dispatched": 0,
        "succeeded": 0,
        "failed": 0,
        "cancel_requested": False,
        "errors": [],
        "in_flight": [],
        "entry_ids": entry_ids,
        "scheduled_run_id": scheduled_run_id,
    }
    await api.store.set(LIST_RUNS, CURRENT_RUN, run)
    for entry_id in entry_ids:
        e = await api.store.get(CALL_LIST, entry_id) or {}
        attempt = int(e.get("attempts") or 0) + 1
        await api.tasks.enqueue(
            api.cfg.services.queue_dial,
            "/tasks/dial",
            {"entry_id": entry_id, "run_id": run_id, "attempt": attempt},
            name=f"dial-{run_id}-{entry_id}-{attempt}",
        )
    log.info(
        "run started", extra={"event": "run.started", "run_id": run_id, "total": len(entry_ids)}
    )
    return run


async def _calls_in_flight(api: Api) -> int:
    since = (api.now() - timedelta(seconds=api.cfg.dialing.call_timeout_s)).isoformat()
    rows = await api.store.query(CALLS, [Where("initiated_at", ">=", since)])
    return sum(
        1
        for _, d in rows
        if d.get("placed_via") in ("call_list", "dashboard")
        and d.get("call_status") not in lifecycle.TERMINAL_STATUSES
    )


async def _run_update(api: Api, run_id: str, fn) -> None:
    def mutate(run):
        if not run or run.get("run_id") != run_id:
            return None
        new = fn(dict(run))
        done = new["succeeded"] + new["failed"]
        if new["status"] == "running" and done >= new["total"]:
            new["status"] = "completed"
            new["finished_at"] = now_iso(api.now())
        return new

    await api.store.transact(LIST_RUNS, CURRENT_RUN, mutate)


@router.post("/tasks/dial")
async def task_dial(payload: dict, request: Request):
    api: Api = request.app.state.api
    entry_id, run_id, attempt = payload["entry_id"], payload["run_id"], int(payload["attempt"])
    run = await api.store.get(LIST_RUNS, CURRENT_RUN) or {}
    if run.get("run_id") == run_id and (
        run.get("cancel_requested") or run.get("status") != "running"
    ):
        return {"skipped": "run canceled"}
    entry = await api.store.get(CALL_LIST, entry_id)
    if not entry or int(entry.get("attempts") or 0) >= attempt:
        return {"skipped": "already claimed"}  # duplicate delivery: never wait on a slot for it
    if await _calls_in_flight(api) >= api.cfg.dialing.max_concurrent_calls:
        # No free call slot: Cloud Tasks retries with backoff (this replaces the old
        # in-memory semaphore + wait loop).
        return Response(status_code=503, content="call slot busy")

    claimed = await api.store.transact(
        CALL_LIST, entry_id, lambda cur: compliance.claim_entry(cur, attempt, now=api.now())
    )
    if not claimed or claimed.get("attempts") != attempt or claimed.get("list_status") != "calling":
        return {"skipped": "already claimed"}
    req = PlaceCallRequest.model_validate(claimed)
    try:
        call_id = await dial(api, req, placed_via="call_list", entry_id=entry_id, run_id=run_id)
    except DialError as exc:
        err = exc  # bind: the closure below outlives the except clause
        blocked = exc.permanent
        await api.store.merge(
            CALL_LIST,
            entry_id,
            {
                "list_status": "blocked" if blocked else "pending",
                "last_call_status": "blocked" if blocked else "dispatch_failed",
                "last_error": exc.detail,
                "updated_at": now_iso(api.now()),
            },
        )

        def failed(r):
            r["failed"] += 1
            r["errors"] = [*r["errors"], {"entry_id": entry_id, "error": err.detail}][-50:]
            if err.status == 429:
                r["status"], r["finished_at"] = "budget_exhausted", now_iso(api.now())
            return r

        await _run_update(api, run_id, failed)
        return {"error": exc.detail}
    await api.store.merge(CALL_LIST, entry_id, {"last_call_sid": call_id, "last_run_id": run_id})

    def dispatched(r):
        r["dispatched"] += 1
        r["in_flight"] = [
            *r["in_flight"],
            {
                "entry_id": entry_id,
                "patient_name": claimed.get("patient_name", ""),
                "call_sid": call_id,
            },
        ]
        return r

    await _run_update(api, run_id, dispatched)
    return {"call_id": call_id}


@router.post("/tasks/call-terminal")
async def task_call_terminal(payload: dict, request: Request):
    api: Api = request.app.state.api
    call_id = payload["call_id"]
    call = await api.store.get(CALLS, call_id)
    if not call:
        return {"skipped": "no call"}
    status = call.get("call_status")
    entry_id, run_id = call.get("entry_id"), call.get("run_id")
    result: dict[str, Any] = {"status": status}
    if entry_id:

        def settle(entry):
            if entry is None:
                return None
            patch = compliance.reconcile_entry_patch(entry, status)
            return {**entry, **patch, "updated_at": now_iso(api.now())} if patch else None

        entry = await api.store.transact(CALL_LIST, entry_id, settle)
        if run_id:
            ok = status in compliance.CLEARING_STATUSES

            def finished(r):
                r["in_flight"] = [x for x in r["in_flight"] if x.get("call_sid") != call_id]
                r["succeeded" if ok else "failed"] += 1
                return r

            await _run_update(api, run_id, finished)
        if entry and status in RECALLABLE:
            result["recall"] = await _maybe_recall(api, entry_id, entry)
    if status in SMS_FOLLOW_UP and call.get("placed_via") != "test_call":
        await api.tasks.enqueue(
            api.cfg.services.queue_sms,
            "/tasks/sms-missed-call",
            {"call_id": call_id},
            name=f"sms-missed-{call_id}",
        )
    return result


async def _maybe_recall(api: Api, entry_id: str, entry: dict) -> str:
    from .routes_list import recall_settings

    cfg = await recall_settings(api)
    if not cfg.enabled:
        return "disabled"
    if int(entry.get("auto_recalls") or 0) >= cfg.max_recalls:
        await api.store.merge(CALL_LIST, entry_id, {"recall_skipped": "max recalls reached"})
        return "max"
    # A patient who engaged with the follow-up text is not re-called (old F4 rule).
    sms = await api.store.get("sms_sessions", entry.get("phone", ""))
    if sms and (
        sms.get("last_inbound_at") or sms.get("state") in ("scheduled", "survey", "awaiting_time")
    ):
        await api.store.merge(CALL_LIST, entry_id, {"recall_skipped": "patient replied to text"})
        return "sms_engaged"
    slot = compliance.next_recall_slot(
        api.now() + timedelta(minutes=cfg.interval_minutes), cfg, api.cfg.site.timezone
    )
    if slot is None:
        return "no_slot"
    when = slot.isoformat()
    runs = [
        (i, d)
        for i, d in await api.store.query(CALL_RUNS, [Where("scheduled_for", "==", when)])
        if d.get("status") == "scheduled" and d.get("auto_recall")
    ]
    now = now_iso(api.now())
    if runs:
        run_id, run = runs[0]
        ids = list(dict.fromkeys([*run.get("entry_ids", []), entry_id]))
        await api.store.merge(CALL_RUNS, run_id, {"entry_ids": ids, "updated_at": now})
    else:
        run_id = new_id()
        await api.store.set(
            CALL_RUNS,
            run_id,
            {
                "scheduled_for": when,
                "entry_ids": [entry_id],
                "label": "Auto-recall",
                "status": "scheduled",
                "auto_recall": True,
                "created_at": now,
                "updated_at": now,
            },
        )
    await api.store.merge(
        CALL_LIST,
        entry_id,
        {
            "auto_recalls": int(entry.get("auto_recalls") or 0) + 1,
            "next_recall_at": when,
            "recall_skipped": None,
        },
    )
    return f"scheduled {when}"


@router.post("/cron/dispatch-due")
async def cron_dispatch_due(request: Request):
    """Every minute (Cloud Scheduler). Claims due scheduled runs; always 200."""
    api: Api = request.app.state.api
    current = await api.store.get(LIST_RUNS, CURRENT_RUN)
    if current and current.get("status") == "running":
        return {"started": False, "reason": "a run is in progress"}
    now = api.now()
    for run_id, run in await api.store.query(CALL_RUNS, [Where("status", "==", "scheduled")]):
        when = run.get("scheduled_for")
        if not when or when > now.isoformat():
            continue
        claimed = await api.store.transact(
            CALL_RUNS,
            run_id,
            lambda cur: (
                {**cur, "status": "dispatching", "updated_at": now_iso(now)}
                if cur and cur.get("status") == "scheduled"
                else None
            ),
        )
        if not claimed or claimed.get("status") != "dispatching":
            continue
        pending = []
        for entry_id in run.get("entry_ids", []):
            e = await api.store.get(CALL_LIST, entry_id)
            if e and e.get("list_status") == "pending":
                pending.append(entry_id)
        if pending:
            await enqueue_dials(
                api,
                pending[: api.cfg.dialing.max_dials_per_run],
                label=run.get("label") or "scheduled",
                scheduled_run_id=run_id,
            )
        await api.store.merge(
            CALL_RUNS,
            run_id,
            {
                "status": "dispatched",
                "dispatched_at": now_iso(now),
                "dialled": len(pending),
                "updated_at": now_iso(now),
            },
        )
        return {"started": bool(pending), "call_run_id": run_id, "dialled": len(pending)}
    # Entries with their own planned time and no run.
    due = [
        i
        for i, e in await api.store.query(CALL_LIST, [Where("list_status", "==", "pending")])
        if compliance.is_due(e, now)
    ]
    if due:
        await enqueue_dials(api, due[: api.cfg.dialing.max_dials_per_run], label="due")
        return {"started": True, "dialled": len(due)}
    return {"started": False, "reason": "nothing due"}
