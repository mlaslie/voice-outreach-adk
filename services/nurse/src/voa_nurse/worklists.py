"""The nurse's worklists.

A supervising nurse asks, in order:
  1. Who is going to be called, and when?            to_be_called
  2. Who has been called today, and what happened?   called_today
  3. Who needs me now, and why?                      needs(group="now")
  4. Who needs me next business day, and why?        needs(group="next_day")
  5. Who doesn't need me, and why not?               cleared
The home card is those five questions as buttons, with counts.

Every view returns ``(Card, data)`` like ``actions.py``; anything that changes
something (call now, cancel, reschedule, change tier) goes through a form and then
a confirmation card.

Not ported from the previous assistant: report sign-off and "resolve with a note".
The outreach API has no sign-off endpoint; a nurse closes a follow-up by changing
its tier (with a reason), which the API records with who and why.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta

from voa_core import timefmt

from . import cards, triage
from .actions import backend, match_patients, propose, resolve, which_patient
from .backend import BackendError
from .cards import MARK, Card, event, phone, us_date, when

IMMEDIATE = ("emergency", "urgent_same_day")
NEXT_DAY = ("routine_next_business_day",)
NOT_REACHED = {
    "left_vmail": "Left voicemail",
    "rna": "No answer",
    "busy": "Line busy",
    "interrupted": "Call dropped",
    "failed": "Call failed",
}
DUE_HOUR = 17
TIER_CHOICES = [
    ("emergency", "Emergent: 911 advised"),
    ("urgent_same_day", "Urgent: RN callback today"),
    ("routine_next_business_day", "Routine: RN callback next business day"),
    ("informational", "FYI: no callback needed"),
    ("none", "No follow-up needed"),
]


# --- shared helpers ----------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(cards.TZ)


def _midnight() -> datetime:
    return _now().replace(hour=0, minute=0, second=0, microsecond=0)


def _is_today(iso) -> bool:
    dt = cards.local(iso)
    return bool(dt and dt.date() == _now().date())


def _today(c: dict) -> bool:
    t = triage.call_time(c)
    return bool(t and t >= _midnight())


def due_by(tier: str, raised: datetime) -> datetime:
    """Emergent is due at once; same-day by 5 PM (or at once if raised later);
    next-business-day by 5 PM, skipping weekends."""
    raised = raised.astimezone(cards.TZ)
    if tier == "emergency":
        return raised
    due = raised
    if tier == "routine_next_business_day":
        due = due + timedelta(days=1)
        while due.weekday() >= 5:
            due += timedelta(days=1)
    due = due.replace(hour=DUE_HOUR, minute=0, second=0, microsecond=0)
    return max(due, raised)


def due_text(tier: str, raised: datetime | None) -> str:
    if tier == "emergency":
        return "now"
    if raised is None:
        return ""
    due = due_by(tier, raised)
    now = _now()
    if due < now:
        mins = int((now - due).total_seconds() // 60)
        span = f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"
        return f"overdue {span}"
    day = "today" if due.date() == now.date() else due.strftime("%a")
    return f"due {day} {cards.clock(due)}"


def clear_rationale(c: dict) -> str:
    """Why no follow-up: the pertinent negatives and positives, in charting language."""
    r = c.get("survey_responses") or {}
    denies, notes = [], []
    pain = str(r.get("pain_present") or "").lower()
    if pain == "no":
        denies.append("pain")
    elif pain == "yes":
        sev = r.get("pain_severity")
        trend = {"better": "improving", "same": "unchanged", "worse": "worsening"}.get(
            str(r.get("pain_trend") or "")
        )
        notes.append(
            "pain "
            + (f"{sev}/10" if sev not in (None, "") else "reported")
            + (f", {trend}" if trend else "")
        )
    if str(r.get("fever_reported") or "").lower() == "no":
        denies.append("fever")
    if str(r.get("incision_concerns") or "").lower() == "no":
        denies.append("incision concerns")
    if str(r.get("legs_swollen") or "").lower() == "no":
        denies.append("leg swelling")
    if str(r.get("blood_sugar_status") or "") == "in_range":
        notes.append("BG in range")
    if str(r.get("sleeping_well") or "").lower() == "no":
        notes.append("not sleeping well")
    parts = []
    if denies:
        joined = ", ".join(denies[:-1]) + " and " + denies[-1] if len(denies) > 1 else denies[0]
        parts.append("Denies " + joined)
    parts += notes
    if said := triage._s(r.get("overall_feeling")):
        parts.append(f"says “{said[:60]}”")
    text = "; ".join(parts)
    text = (
        text[0].upper() + text[1:]
        if text
        else (c.get("follow_up_reason") or "Assessed; no concerns recorded.")
    )
    via = " (by text)" if c.get("channel") == "sms" else ""
    return f"{text}.{via}".replace("..", ".")


def _reason_text(reason: str) -> str:
    """The follow-up reason as the line a nurse should read first: body size, bold,
    capitalised (cards cannot change the font; size and weight are what they offer)."""
    r = (reason or "").strip()
    return f"**{r[:1].upper()}{r[1:]}**" if r else ""


def _row(
    card: Card,
    lead: str,
    title: str,
    lines: list[str],
    *buttons: str | None,
    reason: str | None = None,
) -> str:
    """One patient row: title, then the reason (bold, body size) when given, then the
    small detail lines."""
    first = [card.text(_reason_text(reason), "body")] if reason else []
    info = card.col(
        card.text(title, "h5"), *first, *(card.text(ln, "caption") for ln in lines if ln)
    )
    kept = [b for b in buttons if b]
    return card.row(
        card.row(card.text(lead, "h5"), info, align="start"),  # dot beside the name line
        card.row(*kept) if kept else None,
        justify="spaceBetween",
    )


def _meta(c: dict) -> str:
    ctx = c.get("patient_context") or {}
    return " · ".join(
        x
        for x in (
            f"MRN {c['patient_id']}" if c.get("patient_id") else None,
            ctx.get("operation_name") or None,
            phone(c.get("to")),
        )
        if x
    )


# --- data ------------------------------------------------------------------------------


async def _snapshot() -> dict:
    """Everything the worklists need, fetched together. A failed lookup is an empty
    list plus a note, never a broken card."""
    b = backend()
    calls, entries, runs, cbs, sessions = await asyncio.gather(
        b.list_calls(),
        b.get_call_list(),
        b.get_call_runs(),
        b.get_callbacks(),
        b.get_sessions(),
        return_exceptions=True,
    )
    named = (
        ("calls", calls),
        ("worklist", entries),
        ("runs", runs),
        ("text callbacks", cbs),
        ("text threads", sessions),
    )
    missing = [n for n, v in named if isinstance(v, BaseException)]

    def ok(v):
        return [] if isinstance(v, BaseException) else v

    call_rows, truncated = ([], False) if isinstance(calls, BaseException) else calls
    return {
        "calls": call_rows,
        "truncated": truncated,
        "entries": ok(entries),
        "runs": ok(runs),
        "callbacks": ok(cbs),
        "sessions": ok(sessions),
        "missing": missing,
    }


def _after_miss(c: dict, snap: dict) -> str:
    """What happens next for a patient who was not reached — from the real text
    thread, booking, run and auto-recall, never assumed."""
    ph = c.get("to")
    cb = next(
        (
            x
            for x in snap["callbacks"]
            if x.get("phone") == ph and x.get("status") in (None, "scheduled")
        ),
        None,
    )
    if cb:
        return f"callback booked by text {when(cb.get('run_at'))}"
    sess = next(
        (
            x
            for x in snap["sessions"]
            if x.get("phone") == ph and x.get("call_sid") == c.get("call_sid")
        ),
        None,
    ) or next((x for x in snap["sessions"] if x.get("phone") == ph), None)
    bits = []
    if sess:
        state = sess.get("state")
        if state == "opted_out":
            bits.append("opted out of texts")
        elif state in ("survey", "awaiting_dob"):
            bits.append("answering the questions by text")
        elif sess.get("last_inbound_at"):
            bits.append("replied by text")
        elif sess.get("last_invite_at"):
            bits.append("follow-up text sent, no reply yet")
    else:
        bits.append("no follow-up text")
    e = next(
        (
            x
            for x in snap["entries"]
            if x.get("patient_id") and x.get("patient_id") == c.get("patient_id")
        ),
        None,
    )
    run = next(
        (
            r
            for r in snap["runs"]
            if r.get("status") == "scheduled" and e and e["entry_id"] in (r.get("entry_ids") or [])
        ),
        None,
    )
    if run:
        bits.append(
            ("auto-recall " if run.get("auto_recall") else "next call ")
            + when(run.get("scheduled_for"))
        )
    elif e and e.get("next_recall_at"):
        bits.append(f"auto-recall {when(e['next_recall_at'])}")
    return " · ".join(bits)


def _queue_rows(snap: dict) -> list[dict]:
    """Patients still to be called: members of scheduled runs (and anyone on the
    phone right now), plus callbacks booked by text. Never a past time."""
    by_id = {e["entry_id"]: e for e in snap["entries"]}
    rows = []
    for r in snap["runs"]:
        live = r.get("status") in ("dispatching", "dispatched")
        if r.get("status") == "dispatched" and not _is_today(r.get("scheduled_for")):
            continue
        for eid in r.get("entry_ids") or []:
            e = by_id.get(eid)
            if not e:
                continue
            calling = e.get("list_status") == "calling" or r.get("status") == "dispatching"
            if live and not calling:
                continue  # dialled already: it's on Called today
            if e.get("list_status") not in ("pending", "calling"):
                continue  # reached, cancelled or blocked
            rows.append(
                {
                    "kind": "run",
                    "when": r.get("scheduled_for") or "",
                    "run": r,
                    "entry": e,
                    "calling": calling,
                }
            )
    for cb in snap["callbacks"]:
        if cb.get("status") in (None, "scheduled", "dialing"):
            rows.append(
                {
                    "kind": "text",
                    "when": cb.get("run_at") or "",
                    "callback": cb,
                    "calling": cb.get("status") == "dialing",
                }
            )
    return sorted(rows, key=lambda x: (not x["calling"], x["when"]))


def _buckets(snap: dict) -> dict:
    calls = snap["calls"]
    assessed = triage.latest_assessed_call_per_patient(calls)
    current = {c["doc_id"] for c in assessed}
    fu = triage.follow_up_calls(calls)
    cleared = [
        c
        for c in assessed
        if c.get("follow_up") in ("none", "informational")
        and c.get("outcome") == "questionnaire_completed"
        and _today(c)
    ]
    latest_today: dict[str, dict] = {}
    for c in sorted((c for c in calls if _today(c)), key=triage.ts):
        latest_today[c.get("patient_id") or c["doc_id"]] = c
    not_reached = [
        c
        for c in latest_today.values()
        if c.get("call_status") in NOT_REACHED and c["doc_id"] not in current
    ]
    reschedule = [c for c in latest_today.values() if c.get("outcome") == "reschedule_requested"]
    canceled = [
        e
        for e in snap["entries"]
        if e.get("list_status") == "canceled"
        and e.get("canceled")
        and _is_today((e["canceled"] or {}).get("at"))
    ]
    return {
        "now": [c for c in fu if c["follow_up"] in IMMEDIATE],
        "next_day": [c for c in fu if c["follow_up"] in NEXT_DAY],
        "cleared": sorted(cleared, key=triage.ts, reverse=True),
        "today_patients": len(latest_today),
        "not_reached": sorted(not_reached, key=triage.ts, reverse=True),
        "reschedule": reschedule,
        "canceled": canceled,
    }


# --- 0. home ---------------------------------------------------------------------------


async def view_home():
    snap = await _snapshot()
    b = _buckets(snap)
    q = _queue_rows(snap)
    q_today = [x for x in q if x["calling"] or _is_today(x["when"])]
    counts = {
        "queue": len(q),
        "queue_today": len(q_today),
        "called": b["today_patients"],
        "now": len(b["now"]),
        "next_day": len(b["next_day"]),
        "cleared": len(b["cleared"]),
    }
    card = Card(f"{cards.greeting()}. {cards.DISPLAY_NAME}", "phone")
    card.is_menu = True
    worst = b["now"][0]["follow_up"] if b["now"] else None
    if worst == "emergency":
        e = b["now"][0]
        card.put(
            card.row(
                card.text(MARK["emergency"][0], "h3"),
                card.col(
                    card.text(f"Emergency reported: {e.get('patient_name')}", "h4"),
                    card.text(triage.reason_summary(e), "body"),
                ),
            )
        )
    n_now = counts["now"]
    later = counts["queue"] - counts["queue_today"]
    lines = [
        (
            MARK["emergency"][0]
            if worst == "emergency"
            else MARK["urgent"][0]
            if b["now"]
            else MARK["clear"][0],
            f"{n_now} need{'s' * (n_now == 1)} you now" if n_now else "Nobody needs you right now",
            f"Needs you now ({n_now})",
            event("wl_needs", group="now"),
        ),
        (
            MARK["routine"][0],
            f"{counts['next_day']} for next business day",
            f"Next business day ({counts['next_day']})",
            event("wl_needs", group="next_day"),
        ),
        (
            "\U0001f4de",
            f"{counts['queue_today']} still to be called today"
            + (f" · {later} later" if later else ""),
            f"To be called ({counts['queue']})",
            event("wl_queue"),
        ),
        (
            "\U0001f4cb",
            f"{counts['called']} patient{'s' * (counts['called'] != 1)} called today",
            f"Called today ({counts['called']})",
            event("wl_called"),
        ),
        (
            MARK["clear"][0],
            f"{counts['cleared']} cleared today, no follow-up needed",
            f"Cleared ({counts['cleared']})",
            event("wl_cleared"),
        ),
    ]
    card.put(card.divider())
    for lead, text, label, action in lines:
        primary = label.startswith("Needs you now") and n_now > 0
        card.put(
            card.row(
                card.row(card.text(lead, "h4"), card.text(text)),
                card.button(label, action, primary=primary),
                justify="spaceBetween",
            )
        )
    if snap["missing"]:
        card.line(f"Unavailable right now: {', '.join(snap['missing'])}.", "caption")
    cards.truncation_line(card, snap["truncated"], len(snap["calls"]))
    card.put(
        card.divider(),
        card.row(
            card.button("New follow-up call", event("call_form")),
            card.button("Previous calls", event("list_calls", before_today=True)),
            card.button("Settings", event("wl_settings")),
        ),
    )
    card.line(
        "Ask in your own words too, e.g. “why does Marcus need a callback?” or "
        "“cancel Eleanor's call, she was readmitted”.",
        "caption",
    )
    card.line(
        "Calls are made by an automated caller, not a nurse. Placing a call or changing "
        "anything always needs your confirmation.",
        "caption",
    )
    return card, {**counts, "may_be_incomplete": snap["truncated"], "unavailable": snap["missing"]}


# --- 1. to be called -----------------------------------------------------------------------


async def view_queue():
    snap = await _snapshot()
    rows = _queue_rows(snap)
    card = Card("To be called", "event")
    if not rows:
        card.line(
            "Nobody is scheduled to be called. Runs are set up in the dashboard under Settings."
        )
        return card, {"rows": []}
    n = len(rows)
    card.line(
        f"{n} patient{'s' * (n != 1)} · runs dial at their time; you can call or cancel anyone now",
        "caption",
    )
    group = None
    data = []
    for x in rows[:20]:
        if x["calling"]:
            head = "Calling now"
        elif x["kind"] == "run":
            r = x["run"]
            head = (
                f"{when(x['when'])}"
                + (f" · {r['label']}" if r.get("label") else "")
                + (" · auto-recall" if r.get("auto_recall") else "")
            )
        else:
            head = f"{when(x['when'])} · booked by text"
        if head != group:
            card.put(card.divider(), card.text(head, "h5"))
            group = head
        if x["kind"] == "run":
            e = x["entry"]
            attempt = f"attempt {e.get('attempts', 0) + 1}" if e.get("attempts") else "first call"
            meta = [
                f"MRN {e.get('patient_id')}",
                e.get("operation_name") or None,
                phone(e.get("phone")),
                attempt,
            ]
            btns = (
                []
                if x["calling"]
                else [
                    card.button("Call now", event("propose_call_now", entry_id=e["entry_id"])),
                    card.button("Cancel call", event("wl_cancel_form", entry_id=e["entry_id"])),
                ]
            )
            card.put(
                _row(
                    card,
                    "\U0001f7e2" if x["calling"] else "⏰",
                    e.get("patient_name") or "",
                    [" · ".join(m for m in meta if m)],
                    *btns,
                )
            )
            data.append(
                {
                    "patient": e.get("patient_name"),
                    "patient_id": e.get("patient_id"),
                    "when": x["when"],
                    "calling": x["calling"],
                    "entry_id": e["entry_id"],
                }
            )
        else:
            cb = x["callback"]
            card.put(
                _row(
                    card,
                    "\U0001f4ac",
                    cb.get("patient_name") or phone(cb.get("phone")),
                    [f"MRN {cb.get('patient_id') or '—'} · patient picked this time by text"],
                )
            )
            data.append(
                {
                    "patient": cb.get("patient_name"),
                    "when": x["when"],
                    "booked_by_text": True,
                    "calling": x["calling"],
                }
            )
    if n > 20:
        card.line(f"{n - 20} more not shown.", "caption")
    return card, {"rows": data}


async def _entry(entry_id: str) -> dict | None:
    return next((x for x in await backend().get_call_list() if x["entry_id"] == entry_id), None)


def _gone():
    return cards.result_card(
        "Not on the list", ["That patient is no longer on the call list."], ok=False
    ), {"status": "not_found"}


async def propose_call_now(entry_id: str):
    e = await _entry(entry_id)
    if not e:
        return _gone()
    return await propose(
        "call_now",
        {"entry_id": entry_id, "name": e.get("patient_name")},
        f"Call {e.get('patient_name')} now?",
        [
            f"MRN {e.get('patient_id')} · {phone(e.get('phone'))}",
            "The automated caller dials now, ahead of the planned time.",
        ],
        "Call now",
    )


# --- cancel ----------------------------------------------------------------------------


async def cancel_form(entry_id: str, prefill_reason: str = "", problems: list[str] = ()):
    e = await _entry(entry_id)
    if not e:
        return _gone()
    card = Card(f"Cancel {e.get('patient_name')}'s call", "warning")
    card.line(
        "They come off the queue and show as Cancelled with your reason. Reschedule them any "
        "time from Called today or their history.",
        "caption",
    )
    for p in problems:
        card.line(f"(!) {p}")
    card.put(card.add("TextField", label="Reason (required)", value={"path": "/cancel/reason"}))
    card.buttons(
        card.button(
            "Review cancellation",
            event("wl_cancel_submit", entry_id=entry_id, reason={"path": "/cancel/reason"}),
            primary=True,
        )
    )
    card.data_model = {"cancel": {"reason": prefill_reason}}
    return card, {"status": "form_shown", "patient": e.get("patient_name")}


async def propose_cancel(entry_id: str, reason: str):
    reason = " ".join((reason or "").split())
    if len(reason) < 5:
        card, _ = await cancel_form(entry_id, reason, ["A reason is required (a few words)."])
        return card, {"status": "invalid", "problems": ["reason required"]}
    e = await _entry(entry_id)
    if not e:
        return _gone()
    return await propose(
        "cancel_call",
        {"entry_id": entry_id, "reason": reason[:500], "name": e.get("patient_name")},
        f"Cancel {e.get('patient_name')}'s call?",
        [f"Reason: {reason}"],
        "Cancel call",
    )


# --- reschedule a cancelled call -------------------------------------------------------------

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULT_HOURS = {"window_start": "09:00", "window_end": "17:00", "days": [0, 1, 2, 3, 4, 5]}


_FORMAT_TTL_S = 60.0
_format_checked_at = -_FORMAT_TTL_S


async def refresh_time_format(api) -> None:
    """Follow the clinic's nurse time format (dashboard Settings), at most once a
    minute. On a backend error the last known format stays."""
    global _format_checked_at
    now = time.monotonic()
    if now - _format_checked_at < _FORMAT_TTL_S:
        return
    _format_checked_at = now
    try:
        display = (await api.get_call_settings()).get("display") or {}
    except BackendError:
        return
    cards.set_time_format(timefmt.coerce(display.get("time_format"), cards.TIME_FORMAT))


async def _calling_hours() -> dict:
    """The clinic's calling hours, which the API also enforces on every reschedule."""
    try:
        r = (await backend().get_call_settings()).get("recall") or {}
    except BackendError:
        r = {}
    return {k: r.get(k) or v for k, v in DEFAULT_HOURS.items()}


def in_hours(t: datetime, hours: dict) -> bool:
    lt = t.astimezone(cards.TZ)
    return (
        lt.weekday() in hours["days"]
        and hours["window_start"] <= lt.strftime("%H:%M") < hours["window_end"]
    )


def hours_text(hours: dict) -> str:
    days = ", ".join(DAY_NAMES[d] for d in sorted(hours["days"]))
    start = timefmt.hhmm(hours["window_start"], cards.TIME_FORMAT)
    end = timefmt.hhmm(hours["window_end"], cards.TIME_FORMAT)
    return f"{days}, {start}–{end}"


def next_slot(after: datetime, hours: dict) -> datetime | None:
    """First quarter-hour at or after ``after`` inside calling hours."""
    t = after.astimezone(cards.TZ).replace(second=0, microsecond=0)
    t += timedelta(minutes=-t.minute % 15)
    for _ in range(15 * 24 * 4):
        if in_hours(t, hours):
            return t
        t += timedelta(minutes=15)
    return None


def parse_local(value) -> datetime | None:
    """A DateTimeInput value (ISO, usually without a zone) in clinic time."""
    if isinstance(value, list):
        value = value[0] if value else ""
    try:
        t = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.replace(tzinfo=cards.TZ) if t.tzinfo is None else t


async def reschedule_form(entry_id: str, prefill: dict | None = None, problems: list[str] = ()):
    b = backend()
    entries, runs, hours = await asyncio.gather(
        b.get_call_list(), b.get_call_runs(), _calling_hours()
    )
    e = next((x for x in entries if x["entry_id"] == entry_id), None)
    if not e:
        return _gone()
    if e.get("list_status") not in ("canceled", "pending"):
        return cards.result_card(
            f"{e.get('patient_name')} can't be rescheduled",
            [f"Their call is {e.get('list_status')}."],
            ok=False,
        ), {"status": e.get("list_status")}
    now = _now()
    upcoming = []
    for r in runs:
        t = parse_local(r.get("scheduled_for"))
        if r.get("status") == "scheduled" and t and t > now and in_hours(t, hours):
            upcoming.append(r)
    upcoming = upcoming[:6]
    slot = next_slot(now + timedelta(minutes=15), hours)
    options = [
        {
            "label": f"{when(r['scheduled_for'])}"
            + (f" · {r['label']}" if r.get("label") else "")
            + f" ({len(r.get('entry_ids') or [])} patients)",
            "value": f"run:{r['run_id']}",
        }
        for r in upcoming
    ]
    if slot:
        options.append(
            {"label": f"Next opening: {when(slot.isoformat())} (new run)", "value": "next"}
        )
    options.append({"label": "Another time (pick below)", "value": "custom"})
    card = Card(f"Reschedule {e.get('patient_name')}", "event")
    cx = e.get("canceled") or {}
    if cx.get("reason"):
        card.line(f"Cancelled by {cx.get('by') or 'nurse'}: {cx['reason']}", "caption")
    card.line(f"Calling hours: {hours_text(hours)}. Times outside them are refused.", "caption")
    for p in problems:
        card.line(f"(!) {p}")
    card.put(
        card.add(
            "ChoicePicker",
            label="When",
            variant="mutuallyExclusive",
            options=options,
            value={"path": "/resched/choice"},
        )
    )
    card.put(
        card.add(
            "DateTimeInput",
            label="Another time",
            value={"path": "/resched/at"},
            enableDate=True,
            enableTime=True,
        )
    )
    card.buttons(
        card.button(
            "Review reschedule",
            event(
                "wl_reschedule_submit",
                entry_id=entry_id,
                choice={"path": "/resched/choice"},
                at={"path": "/resched/at"},
            ),
            primary=True,
        )
    )
    p = prefill or {}
    card.data_model = {
        "resched": {"choice": [p.get("choice") or options[0]["value"]], "at": p.get("at") or ""}
    }
    return card, {
        "status": "form_shown",
        "patient": e.get("patient_name"),
        "calling_hours": hours_text(hours),
        "options": [o["label"] for o in options],
    }


async def propose_reschedule(entry_id: str, choice, at=None):
    choice = (choice[0] if isinstance(choice, list) and choice else choice) or ""
    b = backend()
    entries, runs, hours = await asyncio.gather(
        b.get_call_list(), b.get_call_runs(), _calling_hours()
    )
    e = next((x for x in entries if x["entry_id"] == entry_id), None)
    if not e:
        return _gone()
    now = _now()
    params: dict = {"entry_id": entry_id, "name": e.get("patient_name")}
    problems = []
    if choice.startswith("run:"):
        run = next(
            (r for r in runs if r.get("run_id") == choice[4:] and r.get("status") == "scheduled"),
            None,
        )
        if not run:
            problems.append("That run has already been dialled or removed; pick another time.")
        else:
            params.update(run_id=run["run_id"], when=run["scheduled_for"], label=run.get("label"))
    elif choice in ("next", "custom"):
        t = next_slot(now + timedelta(minutes=15), hours) if choice == "next" else parse_local(at)
        if t is None:
            problems.append("Pick a date and time.")
        elif t <= now:
            problems.append("That time has passed; pick a later one.")
        elif not in_hours(t, hours):
            problems.append(f"That is outside calling hours ({hours_text(hours)}).")
        else:
            params["when"] = t.astimezone(cards.TZ).isoformat()
    else:
        problems.append("Pick when to call.")
    if problems:
        prefill = {"choice": choice, "at": at if isinstance(at, str) else ""}
        card, _ = await reschedule_form(entry_id, prefill, problems)
        return card, {"status": "invalid", "problems": problems}
    if params.get("run_id"):
        into = f"into the {when(params['when'])} run" + (
            f" ({params['label']})" if params.get("label") else ""
        )
    else:
        into = f"as a new run at {when(params['when'])}"
    return await propose(
        "reschedule_call",
        params,
        f"Put {e.get('patient_name')} back on the queue?",
        [f"Called {into}.", f"MRN {e.get('patient_id')} · {phone(e.get('phone'))}"],
        "Reschedule",
    )


# --- 2. called today ---------------------------------------------------------------------------


async def view_called():
    snap = await _snapshot()
    b = _buckets(snap)
    card = Card("Called today", "call")
    if not b["today_patients"] and not b["canceled"]:
        card.line("No calls yet today.")
        return card, {}

    def section(title: str, items: list, render) -> None:
        if not items:
            return
        card.put(card.divider(), card.text(f"{title} ({len(items)})", "h5"))
        for it in items[:8]:
            card.put(render(it))
        if len(items) > 8:
            card.line(f"{len(items) - 8} more.", "caption")

    def view_btn(c):
        return card.button("View", event("view_call", call_sid=c["doc_id"]))

    today_now = [c for c in b["now"] if _today(c)]
    today_next = [c for c in b["next_day"] if _today(c)]
    older = len(b["now"]) - len(today_now) + len(b["next_day"]) - len(today_next)
    section(
        "Needs you now",
        today_now,
        lambda c: _row(
            card,
            cards.mark(c)[0],
            c.get("patient_name") or "",
            [triage.questions_summary(c), _meta(c)],
            view_btn(c),
            reason=triage.reason_summary(c),
        ),
    )
    section(
        "Next business day",
        today_next,
        lambda c: _row(
            card,
            cards.mark(c)[0],
            c.get("patient_name") or "",
            [triage.questions_summary(c), _meta(c)],
            view_btn(c),
            reason=triage.reason_summary(c),
        ),
    )
    section(
        "Cleared, no follow-up",
        b["cleared"],
        lambda c: _row(
            card,
            MARK["clear"][0],
            c.get("patient_name") or "",
            [],
            view_btn(c),
            reason=clear_rationale(c),
        ),
    )
    section(
        "Not reached",
        b["not_reached"],
        lambda c: _row(
            card,
            MARK["incomplete"][0],
            c.get("patient_name") or phone(c.get("to")),
            [
                f"{NOT_REACHED.get(c.get('call_status'))} {when(c.get('initiated_at'), False)} · "
                f"{_after_miss(c, snap)}",
                _meta(c),
            ],
            view_btn(c),
        ),
    )
    section(
        "Asked to be called another time",
        b["reschedule"],
        lambda c: _row(
            card,
            "\U0001f4c5",
            c.get("patient_name") or "",
            [c.get("follow_up_reason") or "", _meta(c)],
            view_btn(c),
        ),
    )
    section(
        "Cancelled",
        b["canceled"],
        lambda e: _row(
            card,
            "❌",
            e.get("patient_name") or "",
            [
                f"Cancelled by {(e['canceled'] or {}).get('by') or 'nurse'}: "
                f"{(e['canceled'] or {}).get('reason')}"
            ],
            card.button("Reschedule", event("wl_reschedule_form", entry_id=e["entry_id"])),
        ),
    )
    if older:
        card.put(card.divider())
        card.line(
            f"Also still open from earlier days: {older} follow-up{'s' * (older != 1)} "
            "(see Needs you now / Next business day).",
            "caption",
        )
    cards.truncation_line(card, snap["truncated"], len(snap["calls"]))
    data = {
        "needs_you_now_called_today": [c.get("patient_name") for c in today_now],
        "next_business_day_called_today": [c.get("patient_name") for c in today_next],
        "still_open_from_earlier_days": older,
        "cleared": [c.get("patient_name") for c in b["cleared"]],
        "not_reached": [
            {"patient": c.get("patient_name"), "then": _after_miss(c, snap)}
            for c in b["not_reached"]
        ],
        "asked_to_reschedule": [c.get("patient_name") for c in b["reschedule"]],
        "canceled": [e.get("patient_name") for e in b["canceled"]],
        "may_be_incomplete": snap["truncated"],
    }
    return card, data


# --- 3/4. needs you -------------------------------------------------------------------------


async def view_needs(group: str = "now"):
    snap = await _snapshot()
    b = _buckets(snap)
    items = b["now"] if group == "now" else b["next_day"]
    title = "Needs you now" if group == "now" else "Next business day"
    card = Card(title, "notifications")
    if not items:
        card.line("Nobody right now." if group == "now" else "Nobody for next business day.")
        cards.truncation_line(card, snap["truncated"], len(snap["calls"]))
        return card, {"patients": [], "may_be_incomplete": snap["truncated"]}
    card.line(cards.mark_summary(items) + " · most urgent first", "caption")
    for c in items[:10]:
        reason = triage.reason_summary(c) or triage.verdict_for(c)["reason"]
        due = due_text(c["follow_up"], triage.call_time(c))
        nurse = " · tier set by nurse" if c.get("follow_up_override") else ""
        name = c.get("patient_name") or phone(c.get("to"))
        card.put(
            card.divider(),
            _row(
                card,
                cards.mark(c)[0],
                f"{name}: {triage.follow_up_label(c['follow_up'])}",
                [
                    triage.questions_summary(c),
                    f"{_meta(c)} · called {when(c.get('initiated_at'))} · {due}{nurse}",
                ],
                # Changing the tier lives on the call's detail card, after reading it.
                card.button("View", event("view_call", call_sid=c["doc_id"])),
                reason=reason,
            ),
        )
    if len(items) > 10:
        card.line(f"{len(items) - 10} more not shown.", "caption")
    cards.truncation_line(card, snap["truncated"], len(snap["calls"]))
    return card, {
        "patients": [
            {
                "patient": c.get("patient_name"),
                "tier": c["follow_up"],
                "reason": c.get("follow_up_reason"),
                "all_reasons": [
                    f"{r['tier_label']}: {r['text']}"
                    for r in triage.reasons_data(triage.follow_up_reasons(c))
                ],
                "questions_summary": triage.questions_summary(c),
                "call_sid": c["doc_id"],
                "due": due_text(c["follow_up"], triage.call_time(c)),
            }
            for c in items
        ],
        "may_be_incomplete": snap["truncated"],
    }


# --- 5. cleared --------------------------------------------------------------------------------


async def view_cleared():
    snap = await _snapshot()
    items = _buckets(snap)["cleared"]
    card = Card("Cleared today: no follow-up needed", "check")
    if not items:
        card.line("Nobody has been cleared today yet.")
        return card, {"patients": []}
    card.line(
        "Each was assessed by the automated caller; the reason is what the patient reported.",
        "caption",
    )
    for c in items[:12]:
        card.put(
            card.divider(),
            _row(
                card,
                MARK["clear"][0],
                c.get("patient_name") or "",
                [_meta(c)],
                card.button("View", event("view_call", call_sid=c["doc_id"])),
                reason=clear_rationale(c),
            ),
        )
    return card, {
        "patients": [
            {"patient": c.get("patient_name"), "why": clear_rationale(c), "call_sid": c["doc_id"]}
            for c in items
        ]
    }


# --- change tier with a reason ---------------------------------------------------------------


REASON_FIELDS = 3  # reason lines on the form; the tool takes up to MAX_REASONS
MAX_REASONS = 10  # the API's limit
MAX_REASON_CHARS = 200


def clean_reasons(reasons) -> list[str]:
    """Whitespace collapsed, blanks and repeats dropped, order kept."""
    if isinstance(reasons, str):
        reasons = [reasons]
    out = [" ".join(str(r).split()) for r in reasons or [] if isinstance(r, str | int | float)]
    return list(dict.fromkeys(r for r in out if r))


async def tier_form(call_sid: str, prefill: dict | None = None, problems: list[str] = ()):
    c = await backend().get_call(call_sid)
    name = c.get("patient_name") or phone(c.get("to"))
    card = Card(f"Change follow-up: {name}", "edit")
    agent_said = (
        f" (agent said {triage.follow_up_label(c.get('agent_follow_up'))})"
        if c.get("follow_up_override")
        else ""
    )
    card.line(f"Now: {triage.follow_up_label(c.get('follow_up'))}{agent_said}", "caption")
    if summary := triage.reason_summary(c):
        card.line(f"Current reasons: {summary}", "caption")
    for p in problems:
        card.line(f"(!) {p}")
    card.put(
        card.add(
            "ChoicePicker",
            label="New follow-up",
            variant="mutuallyExclusive",
            options=[{"label": label, "value": v} for v, label in TIER_CHOICES],
            value={"path": "/tier/choice"},
        )
    )
    for i in range(REASON_FIELDS):
        label = (
            "Reason for the change (required)" if i == 0 else f"Another reason {i + 1} (optional)"
        )
        card.put(card.add("TextField", label=label, value={"path": f"/tier/reason{i}"}))
    card.buttons(
        card.button(
            "Review change",
            event(
                "wl_tier_submit",
                call_sid=call_sid,
                choice={"path": "/tier/choice"},
                **{f"reason{i}": {"path": f"/tier/reason{i}"} for i in range(REASON_FIELDS)},
            ),
            primary=True,
        )
    )
    p = prefill or {}
    given = clean_reasons(p.get("reasons")) or clean_reasons(p.get("reason"))
    card.data_model = {
        "tier": {
            "choice": [p.get("tier") or c.get("follow_up") or "none"],
            **{f"reason{i}": given[i] if i < len(given) else "" for i in range(REASON_FIELDS)},
        }
    }
    return card, {"status": "form_shown", "patient": name, "current": c.get("follow_up")}


async def submit_tier_form(call_sid: str, values: dict):
    """The form's reason lines become the call's reasons. A form from before the
    reason lines existed sends one ``reason``: it stays a note, as it was."""
    if any(f"reason{i}" in values for i in range(REASON_FIELDS)):
        lines = [values.get(f"reason{i}") or "" for i in range(REASON_FIELDS)]
        return await propose_tier(call_sid, values.get("choice"), reasons=lines)
    return await propose_tier(call_sid, values.get("choice"), values.get("reason") or "")


async def propose_tier(call_sid: str, tier, reason: str = "", reasons: list[str] | None = None):
    """Change a call's follow-up tier. ``reasons`` (one or more) replace the call's
    reasons list; ``reason`` is a note on the change. At least one is required."""
    tier = (tier[0] if isinstance(tier, list) and tier else tier) or ""
    note = " ".join((reason or "").split())
    reasons = clean_reasons(reasons)
    c = await backend().get_call(call_sid)
    problems = []
    if tier not in dict(TIER_CHOICES):
        problems.append("Pick a follow-up level.")
    elif tier == c.get("follow_up"):
        problems.append("That is the current level; pick a different one.")
    if reasons:
        if len(reasons) > MAX_REASONS:
            problems.append(f"Give at most {MAX_REASONS} reasons.")
        if any(len(r) < 3 for r in reasons):
            problems.append("Each reason needs a few words.")
        if any(len(r) > MAX_REASON_CHARS for r in reasons):
            problems.append(f"Keep each reason under {MAX_REASON_CHARS} characters.")
        if note and len(note) < 3:
            note = ""  # too short to be a note; the reasons say it
    elif len(note) < 5:
        problems.append("A reason is required (a few words).")
    if problems:
        prefill = {"tier": tier, "reasons": reasons or [note]}
        card, _ = await tier_form(call_sid, prefill, problems)
        return card, {"status": "invalid", "problems": problems}
    name = c.get("patient_name") or phone(c.get("to"))
    if reasons:
        n = len(reasons)
        lines = [f"From: {triage.follow_up_label(c.get('follow_up'))}"]
        lines.append(f"Reason{'s' * (n != 1)} to be saved ({n}):")
        lines += [f"{i}. {r}" for i, r in enumerate(reasons, 1)]
        if note:
            lines.append(f"Note: {note}")
        lines.append(
            "These replace the call's reasons; the agent's own reasons are kept alongside."
        )
    else:
        lines = [
            f"From: {triage.follow_up_label(c.get('follow_up'))}",
            f"Reason: {note}",
            "The agent's own assessment is kept alongside.",
        ]
    return await propose(
        "set_tier",
        {
            "call_sid": call_sid,
            "tier": tier,
            "reason": note[:500] or None,
            "reasons": reasons or None,
            "name": name,
        },
        f"Change {name} to {triage.follow_up_label(tier)}?",
        lines,
        "Change follow-up",
    )


# --- texts on a call and the patient timeline --------------------------------------------------


async def view_texts(call_sid: str):
    """The text conversation that belongs to one call. A questionnaire done by text
    has no voice transcript; this is its record."""
    c = await backend().get_call(call_sid)
    name = c.get("patient_name") or phone(c.get("to"))
    card = Card(f"Text messages: {name}", "mail")
    try:
        msgs = (await backend().get_messages(c.get("to") or "", call_sid)).get("messages") or []
    except BackendError as e:
        card.line(f"Text messages are unavailable: {e}")
        return card, {"available": False}
    if not msgs:
        card.line("No text messages for this call.")
    for m in sorted(msgs, key=lambda m: m.get("at") or "")[:40]:
        who_ = "Patient" if m.get("direction") == "inbound" else "Clinic (automated)"
        card.put(
            card.col(
                card.text(f"{who_} · {when(m.get('at'))}", "caption"),
                card.text(m.get("body") or ""),
            )
        )
    card.buttons(card.button("Back to call", event("view_call", call_sid=call_sid)))
    return card, {
        "messages": [
            {"from": m.get("direction"), "at": m.get("at"), "text": m.get("body")}
            for m in msgs[:40]
        ]
    }


async def view_history(call_sid: str | None = None, patient: str | None = None):
    """Everything about one patient, newest first: each call and its outcome, texts,
    cancellations, tier changes, and what is planned next."""
    if call_sid:
        anchor = await backend().get_call(call_sid)
    else:
        anchor, choices = await resolve(None, patient)
        if choices:
            return which_patient(patient or "", choices, "patient_history")
        if not anchor:
            return cards.result_card(
                "No patient found", [f"No calls on record for '{patient}'."], ok=False
            ), {"found": False}
    snap = await _snapshot()
    pid, ph = anchor.get("patient_id"), anchor.get("to")
    calls = [
        c
        for c in snap["calls"]
        if (pid and c.get("patient_id") == pid) or (not pid and c.get("to") == ph)
    ]
    if not any(c.get("doc_id") == anchor.get("doc_id") for c in calls):
        calls.append(anchor)
    entry = next((e for e in snap["entries"] if pid and e.get("patient_id") == pid), None)
    name = anchor.get("patient_name") or phone(ph)
    events: list[tuple[str, str, str]] = []  # (iso, emoji, text)
    for c in calls:
        verdict = triage.verdict_for(c)
        head = "Questionnaire by text" if c.get("channel") == "sms" else "Call"
        tier = f" · {triage.follow_up_label(c['follow_up'])}" if triage.is_assessment(c) else ""
        events.append(
            (
                c.get("initiated_at") or "",
                MARK[verdict["tone"]][0],
                f"{head}: {triage.status_label(c)}{tier}",
            )
        )
        for h in c.get("follow_up_history") or []:
            events.append(
                (
                    h.get("at") or "",
                    "✏️",
                    f"Tier changed by {h.get('set_by') or 'nurse'}: "
                    f"{triage.follow_up_label(h.get('from'))} → "
                    f"{triage.follow_up_label(h.get('to'))} "
                    f"({'; '.join(h.get('reasons') or []) or h.get('reason')})",
                )
            )
    for cx in (entry or {}).get("cancel_history") or []:
        events.append(
            (
                cx.get("at") or "",
                "❌",
                f"Scheduled call cancelled by {cx.get('by') or 'nurse'}: {cx.get('reason')}",
            )
        )
    for sess in snap["sessions"]:
        if sess.get("phone") == ph and sess.get("last_invite_at"):
            replied = " · patient replied" if sess.get("last_inbound_at") else ""
            events.append((sess["last_invite_at"], "\U0001f4ac", "Follow-up text sent" + replied))
    card = Card(f"History: {name}", "person")
    ctx = anchor.get("patient_context") or {}
    card.line(
        " · ".join(
            x
            for x in (
                f"MRN {pid}" if pid else None,
                ctx.get("operation_name"),
                f"on {us_date(ctx.get('operation_date'))}" if ctx.get("operation_date") else None,
                phone(ph),
            )
            if x
        ),
        "caption",
    )
    nxt = None
    if entry:
        run = next(
            (
                r
                for r in snap["runs"]
                if r.get("status") == "scheduled"
                and entry["entry_id"] in (r.get("entry_ids") or [])
            ),
            None,
        )
        if run:
            nxt = (
                f"Auto-recall {when(run.get('scheduled_for'))}, unless they answer the follow-up "
                "text first"
                if run.get("auto_recall")
                else f"Next call {when(run.get('scheduled_for'))}"
            )
        elif entry.get("list_status") == "canceled":
            nxt = "Scheduled call cancelled; not on the queue"
        elif entry.get("list_status") == "completed":
            nxt = "Reached; no more calls planned"
    cb = next(
        (
            x
            for x in snap["callbacks"]
            if x.get("phone") == ph and x.get("status") in (None, "scheduled")
        ),
        None,
    )
    if cb:
        nxt = f"Callback booked by text {when(cb.get('run_at'))}"
    card.line(f"Next: {nxt or 'nothing planned'}", "h5")
    card.put(card.divider())
    ordered = sorted(events, key=lambda e: e[0], reverse=True)[:25]
    for at, emoji, text in ordered:
        card.put(
            card.row(
                card.text(emoji, "h4"), card.col(card.text(text), card.text(when(at), "caption"))
            )
        )
    cards.truncation_line(card, snap["truncated"], len(snap["calls"]))
    latest = max(calls, key=triage.ts)
    btns = [card.button("Latest call", event("view_call", call_sid=latest["doc_id"]))]
    if entry and entry.get("list_status") == "canceled":
        btns.append(
            card.button("Reschedule", event("wl_reschedule_form", entry_id=entry["entry_id"]))
        )
    card.buttons(*btns)
    return card, {
        "patient": name,
        "next": nxt,
        "events": [{"at": at, "what": text} for at, _, text in ordered],
    }


# --- settings (admin, out of the nurse's way) -------------------------------------------------


def settings_card() -> Card:
    card = Card("Settings", "settings")
    card.line(
        "Admin views. Call runs, calling hours and auto-recall are set in the dashboard.", "caption"
    )
    return card.buttons(
        card.button("Patient list", event("call_list")),
        card.button("Approved numbers", event("allowlist")),
        card.button("Text threads", event("followups")),
        card.dashboard_button(),
    )


async def settings_view():
    return settings_card(), {}


# --- entries matching a name (cancel / reschedule by name) ---------------------------------------


async def entries_matching(patient: str, list_status: str) -> list[dict]:
    """Worklist entries in one status matching a name or MRN, shaped like calls so
    the same matcher and "Which patient?" card apply."""
    rows = [e for e in await backend().get_call_list() if e.get("list_status") == list_status]
    as_calls = [
        {
            "doc_id": e["entry_id"],
            "patient_name": e.get("patient_name"),
            "patient_id": e.get("patient_id"),
            "to": e.get("phone"),
            "initiated_at": e.get("created_at"),
            "patient_context": {"operation_name": e.get("operation_name")},
        }
        for e in rows
    ]
    return match_patients(patient or "", as_calls)


# --- execution of this module's proposals ------------------------------------------------------


async def execute(kind: str, a: dict) -> Card | None:
    """Run a confirmed proposal of one of this module's kinds; None if not ours.
    BackendError propagates to actions.execute."""
    b = backend()
    if kind == "call_now":
        await b.start_run([a["entry_id"]])
        return cards.result_card(
            f"Calling {a['name']} now",
            ["Results appear on Called today when the call ends."],
            buttons=[("Called today", event("wl_called"))],
        )
    if kind == "cancel_call":
        r = await b.cancel_entries([a["entry_id"]], a["reason"])
        st = (r.get("results") or [{}])[0].get("status")
        if st != "canceled":
            msg = {
                "in_progress": "They are on the phone right now.",
                "completed": "They were already reached.",
            }
            return cards.result_card(
                f"Not cancelled: {a['name']}", [msg.get(st, f"Status: {st}")], ok=False
            )
        return cards.result_card(
            f"Cancelled {a['name']}'s call",
            [f"Reason: {a['reason']}"],
            buttons=[("To be called", event("wl_queue"))],
        )
    if kind == "reschedule_call":
        r = await b.reschedule_entry(
            a["entry_id"],
            run_id=a.get("run_id"),
            scheduled_for=None if a.get("run_id") else a["when"],
        )
        return cards.result_card(
            f"{a['name']} is back on the queue",
            [f"Will be called {when(r.get('scheduled_for') or a['when'])}."],
            buttons=[("To be called", event("wl_queue"))],
        )
    if kind == "set_tier":
        reasons = a.get("reasons") or []
        await b.set_follow_up(a["call_sid"], a["tier"], a.get("reason"), reasons=reasons or None)
        lines = [f"Reason: {r}" for r in reasons] or [f"Reason: {a.get('reason')}"]
        if reasons and a.get("reason"):
            lines.append(f"Note: {a['reason']}")
        return cards.result_card(
            f"{a['name']}: {triage.follow_up_label(a['tier'])}",
            lines,
            buttons=[
                ("View", event("view_call", call_sid=a["call_sid"])),
                ("Needs you now", event("wl_needs", group="now")),
            ],
        )
    return None
