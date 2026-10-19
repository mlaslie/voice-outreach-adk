"""Cloud Tasks and Cloud Scheduler targets (OIDC-authenticated, tasks-invoker only).

/tasks/dial            dial one call-list entry (waits for a free call slot by returning 503)
/tasks/call-terminal   a call reached a terminal status: settle its entry and run, auto-recall,
                       and hand missed calls to the SMS follow-up
/cron/dispatch-due     every minute: dial scheduled runs and entries whose time has come;
                       close browser test calls whose link was never opened

Two rules from the old proxy's backlog are enforced here, at dial time:

* B-2: a nurse's cancel stops the dial. A dial task re-checks its entry inside
  the claim transaction and skips a cancelled (or completed, or blocked) row
  unless a nurse named it; a cancelled run skips its remaining tasks.
* B-8: a scheduled or automatic run only dials inside calling hours (site
  timezone). Outside them due runs move to the next opening, and the members a
  run had not reached by closing time are carried into a new run there. A
  nurse's own run or call is not limited.
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
from .dialer import DialError, OutsideCallingHours, dial
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
    api: Api,
    entry_ids: list[str],
    *,
    label: str,
    scheduled_run_id: str | None = None,
    explicit: bool = False,
    enforce_hours: bool = False,
) -> dict[str, Any]:
    """Start a run: one named dial task per entry.

    ``explicit``: a nurse named these rows, so a completed or cancelled one may be
    dialled again. ``enforce_hours``: a scheduled or automatic run, which stops
    at closing time (B-8)."""
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
        "explicit": explicit,
        "enforce_hours": enforce_hours,
        # Members not dialled: cancelled/finished since the run started (B-2), or
        # left at closing time (B-8). Ids, so a redelivered task counts once.
        "skipped": [],
        "deferred": [],
    }
    await api.store.set(LIST_RUNS, CURRENT_RUN, run)
    for entry_id in entry_ids:
        e = await api.store.get(CALL_LIST, entry_id) or {}
        attempt = int(e.get("attempts") or 0) + 1
        await api.tasks.enqueue(
            api.cfg.services.queue_dial,
            "/tasks/dial",
            {"entry_id": entry_id, "run_id": run_id, "attempt": attempt, "explicit": explicit},
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


def _done(run: dict) -> int:
    return (
        int(run.get("succeeded") or 0)
        + int(run.get("failed") or 0)
        + len(run.get("skipped") or [])
        + len(run.get("deferred") or [])
    )


async def _run_update(api: Api, run_id: str, fn) -> None:
    finished: dict[str, Any] = {}

    def mutate(run):
        finished.clear()
        if not run or run.get("run_id") != run_id:
            return None
        new = fn(dict(run))
        if new["status"] == "running" and _done(new) >= new["total"]:
            new["status"] = "completed"
            new["finished_at"] = now_iso(api.now())
            finished["run"] = new
        return new

    await api.store.transact(LIST_RUNS, CURRENT_RUN, mutate)
    if finished.get("run"):
        await run_finished(api, finished["run"])


async def run_finished(api: Api, run: dict) -> None:
    """A run's dialling ended (finished, cancelled, or stopped by the budget).

    Its scheduled run records when, so nobody in it shows as "waiting its turn"
    afterwards; members left at closing time go to a new run at the next opening.
    Only a call run's members are carried: per-entry planned rows are simply
    still due at the next opening."""
    rid = run.get("scheduled_run_id")
    if not rid:
        return
    try:
        if run.get("deferred"):
            await carry_over(api, list(run["deferred"]), rid)
        await api.store.merge(
            CALL_RUNS, rid, {"finished_at": run.get("finished_at") or now_iso(api.now())}
        )
    except Exception:
        log.exception("could not close scheduled run", extra={"event": "run.close_failed"})


async def carry_over(
    api: Api,
    entry_ids: list[str],
    from_run: str | None,
    label: str = "Continued: calling hours ended",
) -> str | None:
    """Patients a scheduled run had not reached by closing time: into a new run at
    the next opening, and out of the run they were in (B-8). Only patients still
    waiting: one cancelled or reached since must not be put in tomorrow's run. A
    continued recall run stays a recall. Returns the new run id, if any."""
    from .routes_list import recall_settings

    cfg = await recall_settings(api)
    slot = compliance.next_recall_slot(api.now(), cfg, api.cfg.site.timezone)
    if slot is None:
        return None
    now = now_iso(api.now())
    still = []
    for eid in entry_ids:
        e = await api.store.get(CALL_LIST, eid)
        if e and e.get("list_status") == "pending":
            still.append(eid)
    parent: dict[str, Any] = {}
    if from_run:
        parent = await api.store.get(CALL_RUNS, from_run) or {}
        if parent:
            await api.store.merge(
                CALL_RUNS,
                from_run,
                {
                    "entry_ids": [e for e in parent.get("entry_ids") or [] if e not in entry_ids],
                    "carried_over": still,
                    "updated_at": now,
                },
            )
    if not still:
        return None
    rid = new_id()
    await api.store.set(
        CALL_RUNS,
        rid,
        {
            "scheduled_for": slot.isoformat(),
            "entry_ids": still,
            "label": label,
            "status": "scheduled",
            "auto_recall": bool(parent.get("auto_recall")),
            "carried_from": from_run,
            "created_at": now,
            "updated_at": now,
        },
    )
    log.info(
        "patients carried over",
        extra={"event": "run.carried_over", "count": len(still), "run_id": rid},
    )
    return rid


async def _member_not_dialled(api: Api, run_id: str, entry_id: str, kind: str) -> None:
    """Count a run member that will not be dialled (``skipped`` or ``deferred``)."""

    def mark(r):
        if entry_id not in (r.get("skipped") or []) and entry_id not in (r.get("deferred") or []):
            r[kind] = [*(r.get(kind) or []), entry_id]
        return r

    await _run_update(api, run_id, mark)


@router.post("/tasks/dial")
async def task_dial(payload: dict, request: Request):
    from .routes_list import recall_settings

    api: Api = request.app.state.api
    entry_id, run_id, attempt = payload["entry_id"], payload["run_id"], int(payload["attempt"])
    explicit = bool(payload.get("explicit"))
    run = await api.store.get(LIST_RUNS, CURRENT_RUN) or {}
    ours = run.get("run_id") == run_id
    if ours and (run.get("cancel_requested") or run.get("status") != "running"):
        return {"skipped": "run canceled"}
    entry = await api.store.get(CALL_LIST, entry_id)
    if not entry:
        await _member_not_dialled(api, run_id, entry_id, "skipped")
        return {"skipped": "entry deleted"}
    if int(entry.get("attempts") or 0) >= attempt:
        return {"skipped": "already claimed"}  # duplicate delivery: never wait on a slot for it
    # B-2: re-checked here, right before dialling, and again inside the claim.
    if entry.get("list_status") in compliance.NOT_AUTO_DIALLED and not explicit:
        await _member_not_dialled(api, run_id, entry_id, "skipped")
        return {"skipped": f"entry {entry.get('list_status')}"}
    hours = await recall_settings(api) if ours and run.get("enforce_hours") else None
    if hours is not None and not compliance.in_calling_window(
        api.now(), hours, api.cfg.site.timezone
    ):
        await _member_not_dialled(api, run_id, entry_id, "deferred")
        return {"skipped": "outside calling hours"}
    if await _calls_in_flight(api) >= api.cfg.dialing.max_concurrent_calls:
        # No free call slot: Cloud Tasks retries with backoff (this replaces the old
        # in-memory semaphore + wait loop).
        return Response(status_code=503, content="call slot busy")

    claimed = await api.store.transact(
        CALL_LIST,
        entry_id,
        lambda cur: compliance.claim_entry(cur, attempt, now=api.now(), explicit=explicit),
    )
    if not claimed or claimed.get("attempts") != attempt or claimed.get("list_status") != "calling":
        if claimed and claimed.get("list_status") in compliance.NOT_AUTO_DIALLED:
            # Cancelled between the check above and the claim.
            await _member_not_dialled(api, run_id, entry_id, "skipped")
            return {"skipped": f"entry {claimed.get('list_status')}"}
        return {"skipped": "already claimed"}
    req = PlaceCallRequest.model_validate(claimed)
    try:
        call_id = await dial(
            api,
            req,
            placed_via="call_list",
            entry_id=entry_id,
            run_id=run_id,
            attempt=attempt,
            hours=hours,
        )
    except OutsideCallingHours as exc:
        # Closing time passed between the check and the dial: back on the list.
        await api.store.merge(
            CALL_LIST,
            entry_id,
            {"list_status": "pending", "last_error": exc.detail, "updated_at": now_iso(api.now())},
        )
        await _member_not_dialled(api, run_id, entry_id, "deferred")
        return {"skipped": "outside calling hours"}
    except DialError as exc:
        err = exc  # bind: the closure below outlives the except clause
        blocked = exc.permanent
        result = {
            "list_status": "blocked" if blocked else "pending",
            "last_call_status": "blocked" if blocked else "dispatch_failed",
            "last_error": exc.detail,
        }

        def finish(cur):
            # Only our own "calling" row takes the status: a nurse who cancelled
            # meanwhile wins (B-2). The outcome is always recorded.
            if cur is None:
                return None
            mine = cur.get("list_status") == "calling" and int(cur.get("attempts") or 0) == attempt
            patch = result if mine else {k: v for k, v in result.items() if k != "list_status"}
            return {**cur, **patch, "updated_at": now_iso(api.now())}

        await api.store.transact(CALL_LIST, entry_id, finish)

        def failed(r):
            r["failed"] += 1
            r["errors"] = [*r["errors"], {"entry_id": entry_id, "error": err.detail}][-50:]
            if err.status == 429:
                r["status"], r["finished_at"] = "budget_exhausted", now_iso(api.now())
            return r

        await _run_update(api, run_id, failed)
        if err.status == 429:
            stopped = await api.store.get(LIST_RUNS, CURRENT_RUN) or {}
            if stopped.get("run_id") == run_id:
                await run_finished(api, stopped)
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
        attempt = call.get("entry_attempt")

        def settle(entry):
            if entry is None:
                return None
            # A cancel made during the call stands; an older attempt's result
            # never resets a newer one (B-2).
            patch = compliance.reconcile_entry_patch(
                entry, status, attempt=int(attempt) if attempt is not None else None
            )
            return {**entry, **patch, "updated_at": now_iso(api.now())} if patch else None

        entry = await api.store.transact(CALL_LIST, entry_id, settle)
        if entry and entry.get("list_status") == "canceled":
            entry = None  # a cancelled patient is not auto-recalled
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


#: SMS session states meaning the patient booked a callback by text.
TEXT_BOOKED_STATES = frozenset({"scheduled", "recall_dispatched", "dialing"})


async def drop_text_booked_from_recalls(api: Api) -> int:
    """A patient who booked a callback by text chose their time: take them out of
    any scheduled Auto-recall run, and delete a run left empty (old proxy, 19 Oct:
    the queue kept the auto-recall next to the text booking). Runs a nurse made are
    left alone. Returns how many patients were dropped."""
    dropped = 0
    now = now_iso(api.now())
    for rid, run in await api.store.query(CALL_RUNS, [Where("status", "==", "scheduled")]):
        if not run.get("auto_recall"):
            continue
        booked = []
        for eid in run.get("entry_ids") or []:
            e = await api.store.get(CALL_LIST, eid)
            phone = (e or {}).get("phone")
            sms = await api.store.get("sms_sessions", phone) if phone else None
            if sms and sms.get("state") in TEXT_BOOKED_STATES:
                booked.append(eid)
        if not booked:
            continue
        rest = [e for e in run.get("entry_ids") or [] if e not in booked]
        if rest:
            await api.store.merge(CALL_RUNS, rid, {"entry_ids": rest, "updated_at": now})
        else:
            await api.store.delete(CALL_RUNS, rid)
        for eid in booked:
            await api.store.merge(
                CALL_LIST,
                eid,
                {"next_recall_at": None, "recall_skipped": "patient booked a callback by text"},
            )
        dropped += len(booked)
    if dropped:
        log.info(
            "text-booked patients dropped from auto-recall",
            extra={"event": "recall.text_booked_dropped", "count": dropped},
        )
    return dropped


async def defer_due_runs(api: Api, cfg: compliance.RecallSettings) -> list[str]:
    """Outside calling hours: move every due scheduled run to the next opening
    instead of dialling it. A run can come due out of hours when an earlier run was
    still dialling at closing time, or the scheduler was paused and resumed at
    night (B-8)."""
    now = api.now()
    slot = compliance.next_recall_slot(now, cfg, api.cfg.site.timezone)
    if slot is None:
        return []
    moved = []
    for rid, run in await api.store.query(CALL_RUNS, [Where("status", "==", "scheduled")]):
        when = compliance.parse_when(run.get("scheduled_for"))
        if when is not None and when <= now:
            await api.store.merge(
                CALL_RUNS,
                rid,
                {
                    "scheduled_for": slot.isoformat(),
                    "updated_at": now_iso(now),
                    "note": f"Moved from {run.get('scheduled_for')}: due outside calling hours.",
                },
            )
            moved.append(rid)
    return moved


async def _dispatch_due_call_run(api: Api) -> dict | None:
    """Claim and start the earliest due scheduled run, or None if nothing is due."""
    now = api.now()
    due = []
    for run_id, run in await api.store.query(CALL_RUNS, [Where("status", "==", "scheduled")]):
        when = compliance.parse_when(run.get("scheduled_for"))
        if when is not None and when <= now:
            due.append((when, run_id, run))
    for _, run_id, _run in sorted(due, key=lambda x: x[0]):
        claimed = await api.store.transact(
            CALL_RUNS,
            run_id,
            lambda cur: (
                {
                    **cur,
                    "status": "dispatching",
                    "claimed_at": now_iso(now),
                    "updated_at": now_iso(now),
                }
                if cur and cur.get("status") == "scheduled"
                else None
            ),
        )
        if not claimed or claimed.get("status") != "dispatching":
            continue
        pending = []
        for entry_id in claimed.get("entry_ids", []):
            e = await api.store.get(CALL_LIST, entry_id)
            if e and e.get("list_status") == "pending":
                pending.append(entry_id)
        pending = pending[: api.cfg.dialing.max_dials_per_run]
        stamp = now_iso(now)
        await api.store.merge(
            CALL_RUNS,
            run_id,
            {
                "status": "dispatched",
                "dispatched_at": stamp,
                "dialled": len(pending),
                "updated_at": stamp,
                **(
                    {}
                    if pending
                    else {"finished_at": stamp, "note": "No pending patients at run time."}
                ),
            },
        )
        if pending:
            await enqueue_dials(
                api,
                pending,
                label=claimed.get("label") or "scheduled",
                scheduled_run_id=run_id,
                enforce_hours=True,
            )
        return {"started": bool(pending), "call_run_id": run_id, "dialled": len(pending)}
    return None


# A browser test-call link lives 5 minutes. One never opened leaves a call doc with
# no status, which the dashboards show as "in flight" forever (seen 19 Oct: two
# smoke-test links still "Queued" two days later).
UNUSED_TEST_CALL_AFTER = timedelta(minutes=15)


async def close_unused_test_calls(api: Api) -> int:
    """Mark test calls whose link was never used as canceled. Returns how many."""
    cutoff = now_iso(api.now() - UNUSED_TEST_CALL_AFTER)
    now = now_iso(api.now())
    closed = 0
    for cid, d in await api.store.query(CALLS, [Where("placed_via", "==", "test_call")]):
        if d.get("call_status") or d.get("answered_at"):
            continue
        if (d.get("updated_at") or d.get("initiated_at") or now) > cutoff:
            continue
        await api.store.merge(
            CALLS,
            cid,
            {
                "call_status": lifecycle.CANCELED,
                "ended_at": now,
                "updated_at": now,
                "outcome_note": "test-call link never opened",
            },
        )
        closed += 1
    if closed:
        log.info("unused test calls closed", extra={"event": "test_call.expired", "count": closed})
    return closed


@router.post("/cron/dispatch-due")
async def cron_dispatch_due(request: Request):
    """Every minute (Cloud Scheduler). Claims due scheduled runs; always 200.

    Calling hours are checked HERE, at dial time, not only when a run is created:
    outside them nothing is dialled and due runs move to the next opening (B-8).
    A nurse's "Call now" is not this path."""
    from .routes_list import recall_settings

    api: Api = request.app.state.api
    # Take out anyone who has since booked a time by text. Never allowed to stop
    # the dispatch below.
    try:
        await drop_text_booked_from_recalls(api)
    except Exception:
        log.exception("could not drop text-booked patients", extra={"event": "recall.drop_failed"})
    try:
        await close_unused_test_calls(api)
    except Exception:
        log.exception(
            "could not close unused test calls", extra={"event": "test_call.expire_failed"}
        )
    current = await api.store.get(LIST_RUNS, CURRENT_RUN)
    if current and current.get("status") == "running":
        return {"started": False, "reason": "a run is in progress"}
    cfg = await recall_settings(api)
    now = api.now()
    tz = api.cfg.site.timezone
    if not compliance.in_calling_window(now, cfg, tz):
        moved = await defer_due_runs(api, cfg)
        return {
            "started": False,
            "call_run_id": None,
            "reason": f"Outside calling hours ({compliance.window_text(cfg, tz)})"
            + (f"; moved {len(moved)} due run(s) to the next opening." if moved else "."),
        }
    # Scheduled call runs first; one per tick. A failure here must not stop the
    # per-entry pass below.
    try:
        started = await _dispatch_due_call_run(api)
    except Exception:
        log.exception("call-run dispatch failed", extra={"event": "run.dispatch_failed"})
        started = None
    if started is not None:
        return started
    # Entries with their own planned time and no run.
    due = [
        i
        for i, e in await api.store.query(CALL_LIST, [Where("list_status", "==", "pending")])
        if compliance.is_due(e, now)
    ]
    if due:
        await enqueue_dials(
            api, due[: api.cfg.dialing.max_dials_per_run], label="due", enforce_hours=True
        )
        return {"started": True, "dialled": len(due)}
    return {"started": False, "reason": "nothing due"}
