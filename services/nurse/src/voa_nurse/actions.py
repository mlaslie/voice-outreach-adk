"""Views and actions, shared by the model's tools and by button clicks.

Every view returns ``(Card | None, data)``: the card goes to the user, the compact
data goes back to the model so it can answer follow-up questions.

Anything that dials a phone, cancels, reschedules or changes a setting is never
executed directly. A ``propose_*`` function stores the request (see
``proposals.py``) and returns a confirmation card; only a Confirm click or a typed
``confirm <id>`` runs it (:func:`execute`).
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime, timedelta

from voa_core import languages
from voa_core.compliance import coerce_bool
from voa_core.logging import safe_extra

from . import cards, triage, turn
from .backend import BackendError

log = logging.getLogger(__name__)

E164 = re.compile(r"^\+[1-9]\d{7,14}$")
PATIENT_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def backend():
    return turn.current().backend


async def propose(kind: str, params: dict, title: str, lines: list[str], confirm_label: str):
    p = await turn.current().proposals.create(kind, params, title)
    card = cards.confirm_card(p.id, title, lines, confirm_label)
    return card, {
        "status": "awaiting_user_confirmation",
        "summary": title,
        "note": "Not done yet. The user must press the confirm button on the card "
        f"(or type: confirm {p.id}).",
    }


# --- lookups -----------------------------------------------------------------------


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (s or "").lower())


def match_patient(c: dict, query: str) -> bool:
    """MRN (exact), phone (7+ digits) or name, where every word typed must START a
    word of the name: "Ann" matches "Ann Lee" but not "Joanne Lee"."""
    q = query.strip().lower()
    if not q:
        return False
    if q == (c.get("patient_id") or "").lower():
        return True
    digits = re.sub(r"\D", "", q)
    if len(digits) >= 7 and digits in re.sub(r"\D", "", c.get("to") or ""):
        return True
    name = _tokens(c.get("patient_name") or "")
    qt = _tokens(q)
    return bool(qt) and all(any(n.startswith(t) for n in name) for t in qt)


def _patient_key(c: dict) -> str:
    return (
        f"pid:{c['patient_id']}"
        if c.get("patient_id")
        else f"phone:{c.get('to') or c.get('doc_id')}"
    )


def newest_first(calls: list[dict]) -> list[dict]:
    return sorted(calls, key=triage.ts, reverse=True)


def match_patients(query: str, calls: list[dict]) -> list[dict]:
    """The latest call of each distinct patient matching ``query``, newest first.
    An exact MRN, or an exact full name, wins outright over partial matches."""
    q = query.strip().lower()
    hits = [c for c in calls if match_patient(c, query)]
    exact_mrn = [c for c in hits if q == (c.get("patient_id") or "").lower()]
    exact_name = [c for c in hits if q == (c.get("patient_name") or "").strip().lower()]
    hits = exact_mrn or exact_name or hits
    latest: dict[str, dict] = {}
    for c in newest_first(hits):
        latest.setdefault(_patient_key(c), c)
    return list(latest.values())


def brief(c: dict) -> dict:
    return {
        "call_sid": c["doc_id"],
        "patient": c.get("patient_name"),
        "patient_id": c.get("patient_id"),
        "status": triage.status_label(c),
        "follow_up": c.get("follow_up"),
        "follow_up_reason": c.get("follow_up_reason"),
        "placed": cards.when(c.get("initiated_at")),
        **({"questions_summary": qs} if (qs := triage.questions_summary(c)) else {}),
    }


async def resolve(
    call_sid: str | None = None, patient: str | None = None
) -> tuple[dict | None, list[dict]]:
    """(the call, other candidates). When a name matches more than one patient, the
    call is None and the candidates are one latest call per patient: the caller
    must ask which, never guess."""
    if call_sid:
        return await backend().get_call(call_sid), []
    if patient:
        calls, _ = await backend().list_calls()
        found = match_patients(patient, calls)
        if len(found) == 1:
            return found[0], []
        if len(found) > 1:
            return None, found
    return None, []


def which_patient(query: str, choices: list[dict], then: str, key: str = "call_sid"):
    card = cards.choose_patient_card(query, choices, then, key=key)
    return card, {
        "status": "ambiguous",
        "note": "Several patients match. Ask the user which one; the card lists them.",
        "candidates": [
            {"patient": c.get("patient_name"), "patient_id": c.get("patient_id")} for c in choices
        ],
    }


def not_found(query: str | None):
    return cards.result_card(
        "No call on record", [f"No follow-up call found for '{query}'."], ok=False
    ), {"found": False}


# --- views -------------------------------------------------------------------------


def _midnight() -> datetime:
    return datetime.now(cards.TZ).replace(hour=0, minute=0, second=0, microsecond=0)


async def view_calls(
    status: str = "all",
    needs_follow_up: bool = False,
    patient: str | None = None,
    today: bool = False,
    before_today: bool = False,
    since_hours: int | None = None,
    offset: int = 0,
):
    server_status = status if status not in (None, "all", "active") else None
    calls, truncated = await backend().list_calls(status=server_status)
    if status == "active":
        calls = [c for c in calls if triage.is_active(c)]
    elif status and status != "all":
        calls = [c for c in calls if c.get("call_status") == status]
    if needs_follow_up:
        keep = {c["doc_id"] for c in triage.follow_up_calls(calls)}
        calls = [c for c in calls if c["doc_id"] in keep]
    if patient:
        calls = [c for c in calls if match_patient(c, patient)]
    if today or since_hours:
        cutoff = (
            _midnight() if today else datetime.now(cards.TZ) - timedelta(hours=int(since_hours))
        )
        calls = [c for c in calls if (t := triage.call_time(c)) and t >= cutoff]
    if before_today:
        midnight = _midnight()
        calls = [c for c in calls if (t := triage.call_time(c)) and t < midnight]
    calls = newest_first(calls)
    head = (
        "Today's calls"
        if today
        else "Previous calls"
        if before_today
        else f"Calls, last {since_hours} h"
        if since_hours
        else "Follow-up calls"
    )
    bits = [
        b
        for b in (
            triage.STATUS_LABELS.get(status, status).lower()
            if status not in (None, "all")
            else None,
            "needs RN follow-up" if needs_follow_up else None,
            f"patient '{patient}'" if patient else None,
        )
        if b
    ]
    title = head + (f" ({', '.join(bits)})" if bits else "")
    filt = {
        k: v
        for k, v in dict(
            status=status,
            needs_follow_up=needs_follow_up,
            patient=patient,
            today=today,
            before_today=before_today,
            since_hours=since_hours,
        ).items()
        if v and not (k == "status" and v == "all")
    }
    card = cards.calls_card(calls, title, filter_args=filt, offset=offset, truncated=truncated)
    return card, {
        "total": len(calls),
        "calls": [brief(c) for c in calls[:30]],
        "may_be_incomplete": truncated,
    }


async def view_follow_up_queue():
    calls, truncated = await backend().list_calls()
    outstanding = triage.follow_up_calls(calls)
    return cards.follow_up_card(calls, truncated), {
        "outstanding": [brief(c) for c in outstanding],
        "may_be_incomplete": truncated,
    }


async def view_call(call_sid: str | None = None, patient: str | None = None):
    c, choices = await resolve(call_sid, patient)
    if choices:
        return which_patient(patient or "", choices, "view_call")
    if not c:
        return not_found(patient or call_sid)
    reasons = triage.follow_up_reasons(c)
    data = {
        **brief(c),
        "verdict": triage.verdict_for(c),
        # Every reason, each with its own tier, most urgent first: answer "why does
        # this patient need a call?" from these, all of them.
        "follow_up_reasons": triage.reasons_data(reasons),
        "findings": triage.findings_for(c),
        # In the patient's words; each needs a nurse's answer, never the agent's.
        "patient_questions": [
            {k: q[k] for k in ("text", "category_label", "priority_label")}
            for q in triage.patient_questions(c)
        ],
        "has_questions_none_captured": triage.has_uncaptured_questions(c),
        **(
            {"agent_original_reasons": triage.reasons_data(triage.agent_original_reasons(c))}
            if triage.reasons_set_by_nurse(reasons)
            else {}
        ),
        "patient_context": c.get("patient_context"),
        "phone": c.get("to"),
        "dob_verification": c.get("dob_verification_result"),
        "outcome": c.get("outcome"),
        "duration_seconds": c.get("duration_seconds"),
        "has_recording": cards.has_recording(c),
        "recording_note": (
            "The card's Play recording button plays it in the card (web only)."
            if cards.has_recording(c)
            else "No recording for this call."
        ),
    }
    return cards.call_detail_card(c), data


async def play_recording(call_sid: str):
    """The call's recording, playing in the card (A-9). The signed link is short-lived,
    for this one call only, and goes into the card's AudioPlayer: it is never returned
    to the model, which needs no bearer links."""
    msg = "Could not get the recording right now."
    try:
        c = await backend().get_call(call_sid)
        link = await backend().recording_link(call_sid)
        url = link["url"]
        if not isinstance(url, str) or not url.startswith("https://"):
            raise TypeError("not an https link")
    except BackendError as e:
        if e.status == 404 or str(e).startswith("404"):
            msg = "No recording for this call."
        log.warning(
            "recording link unavailable",
            extra=safe_extra({"event": "nurse.recording.link_error", "status": e.status}),
        )
        url = None
    except (KeyError, TypeError):
        url = None
    if url is None:
        back = [("Back to call", cards.event("view_call", call_sid=call_sid))]
        card = cards.result_card("Call recording", [msg], ok=False, buttons=back)
        return card, {"recording": msg}
    return cards.recording_card(c, url), {
        "recording": "shown in the card",
        "expires_in": link.get("expires_in", 900),
    }


async def view_transcript(call_sid: str | None = None, patient: str | None = None):
    c, choices = await resolve(call_sid, patient)
    if choices:
        return which_patient(patient or "", choices, "show_transcript")
    if not c:
        return not_found(patient or call_sid)
    if c.get("channel") == "sms":
        from . import worklists

        return await worklists.view_texts(c["doc_id"])
    t = await backend().get_transcript(c["doc_id"])
    turns = [{"role": x.get("role"), "text": x.get("text")} for x in t.get("turns") or []]
    return cards.transcript_card(c, t), {"available": t.get("available"), "turns": turns[:60]}


async def view_call_list():
    entries, run = await backend().get_call_list(), await backend().get_run()
    data = {
        "run": run.get("run"),
        "run_active": run.get("active"),
        "entries": [
            {
                "entry_id": e["entry_id"],
                "patient": e.get("patient_name"),
                "patient_id": e.get("patient_id"),
                "status": e.get("list_status"),
                "attempts": e.get("attempts"),
                "last_error": e.get("last_error"),
            }
            for e in entries[:50]
        ],
    }
    return cards.call_list_card(entries, run), data


async def view_sms_followups():
    b = backend()
    sessions, callbacks, settings = (
        await b.get_sessions(),
        await b.get_callbacks(),
        await b.get_sms_settings(),
    )
    data = {
        "settings": settings,
        "sessions": [
            {
                k: s.get(k)
                for k in (
                    "patient_name",
                    "state",
                    "scheduled_for_text",
                    "message_count",
                    "updated_at",
                )
            }
            for s in sessions[:30]
        ],
        "callbacks": [
            {k: c.get(k) for k in ("callback_id", "patient_name", "run_at", "status")}
            for c in callbacks[:30]
        ],
    }
    return cards.followups_card(sessions, callbacks, settings), data


async def view_allowlist():
    resp = await backend().get_allowlist()
    return cards.allowlist_card(resp), {
        "mode": resp.get("mode"),
        "entries": [
            {k: e.get(k) for k in ("phone", "label", "editable")} for e in resp.get("entries", [])
        ],
    }


# --- proposals -------------------------------------------------------------------------


def normalize_phone(raw: str | None) -> str:
    """Typed US numbers ("202 555 0123", "(202) 555-0123") to E.164; anything else unchanged."""
    raw = (raw or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return raw


def patient_problems(p: dict) -> list[str]:
    problems = []
    if not E164.match(p.get("phone") or ""):
        problems.append("phone number must be a full number, e.g. (202) 555-0123 or +12025550123")
    if not (p.get("patient_name") or "").strip():
        problems.append("patient name is required")
    if not PATIENT_ID.match(p.get("patient_id") or ""):
        problems.append("MRN is required (letters, digits, dot, dash or underscore)")
    if not ISO_DATE.match(p.get("patient_dob") or ""):
        problems.append("DOB is required as a full date")
    lang = p.get("language")
    if lang and str(lang).strip() and languages.normalize(lang) is None:
        problems.append(
            f"language {lang!r} is not one the caller speaks; use one of "
            + ", ".join(f"{x.name} ({x.code})" for x in languages.SUPPORTED)
        )
    return problems


def patient_body(p: dict) -> dict:
    body = {
        "phone": p["phone"].strip(),
        "patient_name": p["patient_name"].strip(),
        "patient_id": p["patient_id"].strip(),
        "patient_dob": p["patient_dob"].strip(),
        "is_diabetic": bool(p.get("is_diabetic")),
    }
    # campaign_id: passed through when present (a redial keeps the call's use
    # case); the API validates it.
    for k in ("operation_name", "operation_date", "campaign_id"):
        if p.get(k) and str(p[k]).strip():
            body[k] = str(p[k]).strip()
    # The patient's language (normalised; patient_problems refused anything
    # unsupported) and the "call in English" override.
    lang = languages.normalize(p.get("language"))
    if lang:
        body["language"] = lang
    if _yes(p.get("call_in_english")):
        body["call_in_english"] = True
    return body


def _yes(v) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "yes", "y", "1"))


def hold_lines(b: dict) -> list[str]:
    """A held language, said on the confirmation card before anyone confirms."""
    reason = languages.hold_reason(
        b.get("language"), b.get("campaign_id"), coerce_bool(b.get("call_in_english"))
    )
    if not reason:
        return []
    return [f"(!) {languages.held_message(b['patient_name'], b.get('language'))}"]


def _patient_lines(b: dict) -> list[str]:
    a = cards.age(b["patient_dob"])
    proc = f"Procedure: {b.get('operation_name') or 'not recorded'}"
    if b.get("operation_date"):
        proc += f" on {cards.us_date(b['operation_date'])}"
    head = f"{b['patient_name']} · MRN {b['patient_id']} · DOB {cards.us_date(b['patient_dob'])}"
    return [
        head + (f" ({a} y)" if a is not None else ""),
        f"Phone {cards.phone(b['phone'])}",
        proc,
        "DM: Yes (blood glucose questions included)" if b["is_diabetic"] else "DM: No",
    ] + _language_lines(b)


def _language_lines(b: dict) -> list[str]:
    line = cards.language_line(b.get("language"))
    if not line:
        return []
    if b.get("call_in_english"):
        line += " · will be called in English"
    return [line]


def allowlisted(phone: str, entries: list[str]) -> bool:
    """Exact numbers, or prefixes written with a trailing "*" (review A-7)."""
    return any(phone.startswith(e[:-1]) if e.endswith("*") else phone == e for e in entries)


async def allowlist_warning(phones: list[str]) -> list[str]:
    try:
        al = await backend().get_allowlist()
    except BackendError:
        return []
    if al.get("unrestricted") or al.get("mode") == "open":
        return []
    entries = [e["phone"] for e in al.get("entries", []) if e.get("phone")]
    blocked = [p for p in phones if not allowlisted(p, entries)]
    if not blocked:
        return []
    shown = ", ".join(cards.phone(p) for p in blocked)
    return [f"(!) Not an approved number, so the call will be blocked: {shown}"]


async def propose_place_call(**p):
    p = {**p, "phone": normalize_phone(p.get("phone"))}
    if problems := patient_problems(p):
        return None, {"status": "invalid", "problems": problems}
    body = patient_body(p)
    lines = _patient_lines(body) + hold_lines(body) + await allowlist_warning([body["phone"]])
    return await propose(
        "place_call",
        {"body": body},
        f"Place follow-up call to {body['patient_name']} now?",
        lines
        + ["The automated caller will verify DOB, then complete the post-discharge check-in."],
        "Place call",
    )


async def submit_call_form(values: dict):
    """Submit on the form card: re-show the form with what was wrong, or confirm."""
    p = {f: values.get(f) for f in cards.CALL_FORM_FIELDS}
    p["is_diabetic"] = p["is_diabetic"] is True or str(p["is_diabetic"]).lower() == "true"
    p["call_in_english"] = _yes(p.get("call_in_english"))
    p = {k: v.strip() if isinstance(v, str) else v for k, v in p.items()}
    card, data = await propose_place_call(**p)
    if card is None:
        return cards.place_call_form_card(p, [f"Check: {x}" for x in data["problems"]]), data
    return card, data


async def show_form(prefill: dict | None = None):
    return cards.place_call_form_card(prefill), {"status": "form_shown"}


async def propose_redial(call_sid: str, call_in_english: bool = False):
    """Call a patient back from an earlier call. The call record never holds the DOB
    (it is PHI kept off call docs), so it comes from the patient's worklist entry;
    without one, the form opens pre-filled and asks for it."""
    c = await backend().get_call(call_sid)
    ctx = c.get("patient_context") or {}
    p = {
        "phone": c.get("to"),
        "patient_name": c.get("patient_name") or ctx.get("patient_name"),
        "patient_id": c.get("patient_id") or ctx.get("patient_id"),
        "patient_dob": None,
        "operation_name": ctx.get("operation_name"),
        "operation_date": ctx.get("operation_date"),
        "is_diabetic": bool(ctx.get("is_diabetic", False)),
        "campaign_id": c.get("campaign_id"),
        "language": c.get("language") or ctx.get("language"),
        "call_in_english": bool(
            call_in_english or c.get("call_in_english") or ctx.get("call_in_english")
        ),
    }
    if p["patient_id"]:
        try:
            entry = next(
                (
                    e
                    for e in await backend().get_call_list()
                    if (e.get("patient_id") or "").lower() == p["patient_id"].lower()
                ),
                None,
            )
        except BackendError:
            entry = None
        if entry:
            p["patient_dob"] = entry.get("patient_dob")
    if not p["patient_dob"] and turn.current().renders_cards:
        card = cards.place_call_form_card(p, ["Check: enter the DOB to call this patient back"])
        return card, {"status": "form_shown", "missing": ["patient_dob"]}
    card, data = await propose_place_call(**p)
    if card is None:
        return cards.result_card(
            "Unable to call back from this record", data["problems"], ok=False
        ), data
    return card, data


async def propose_add_entries(entries: list[dict], source: str = "chat"):
    bodies, problems = [], []
    for i, e in enumerate(entries, 1):
        if not isinstance(e, dict):
            problems.append(f"Row {i}: not a patient record")
            continue
        e = {**e, "phone": normalize_phone(e.get("phone"))}
        if probs := patient_problems(e):
            problems.append(f"Row {i} ({e.get('patient_name') or 'no name'}): {'; '.join(probs)}")
        else:
            bodies.append(patient_body(e))
    if problems:
        card = cards.result_card("Some patients could not be added", problems[:10], ok=False)
        return card, {"status": "invalid", "problems": problems}
    if not bodies:
        return cards.result_card("No patients to add", ["No patient rows were found."], ok=False), {
            "status": "empty"
        }
    if len(bodies) > 200:
        return cards.result_card(
            "Too many patients", ["Add at most 200 patients at a time."], ok=False
        ), {"status": "too_many"}
    lines = [
        f"{b['patient_name']} ({b['patient_id']}), {cards.phone(b['phone'])}"
        + (
            f" · {cards.language_line(b.get('language'))}"
            if cards.language_line(b.get("language"))
            else ""
        )
        for b in bodies[:10]
    ]
    held = [b["patient_name"] for b in bodies if languages.entry_hold_reason(b)]
    if held:
        lines.append(
            f"(!) Held until their language is available: {', '.join(held[:10])}. "
            "An admin can turn on 'Call in English' for a patient."
        )
    if len(bodies) > 10:
        lines.append(f"…and {len(bodies) - 10} more")
    lines += await allowlist_warning([b["phone"] for b in bodies])
    n = len(bodies)
    src = f" from {source}" if source != "chat" else ""
    return await propose(
        "add_entries",
        {"entries": bodies},
        f"Add {n} patient{'s' * (n != 1)} to the outreach worklist{src}?",
        lines + ["No calls are placed until outreach starts."],
        "Add to worklist",
    )


async def propose_remove_entry(entry_id: str | None = None, patient_id: str | None = None):
    entries = await backend().get_call_list()
    e = next(
        (
            x
            for x in entries
            if (entry_id and x["entry_id"] == entry_id)
            or (patient_id and (x.get("patient_id") or "").lower() == patient_id.lower())
        ),
        None,
    )
    if not e:
        return cards.result_card(
            "Not on the worklist", [f"No worklist entry for {patient_id or entry_id}."], ok=False
        ), {"status": "not_found"}
    return await propose(
        "remove_entry",
        {"entry_id": e["entry_id"]},
        f"Remove {e.get('patient_name')} from the outreach worklist?",
        [f"MRN {e.get('patient_id')} · {cards.phone(e.get('phone'))}"],
        "Remove",
    )


async def propose_allow_add(phone: str, label: str = ""):
    phone = normalize_phone(phone)
    if not E164.match(phone or ""):
        return None, {"status": "invalid", "problems": ["phone must be in +12025550123 form"]}
    user = turn.current().user
    label = f"{label.strip()} (added via Gemini Enterprise{' by ' + user if user else ''})".strip()
    return await propose(
        "allow_add",
        {"phone": phone, "label": label[:100]},
        f"Approve calls to {cards.phone(phone)}?",
        [
            "This approves calls only. Consent to text messages is recorded separately in "
            "the dashboard."
        ],
        "Allow",
    )


async def propose_allow_remove(phone: str):
    phone = normalize_phone(phone)
    return await propose(
        "allow_remove",
        {"phone": phone},
        f"Remove approval for {cards.phone(phone)}?",
        ["Calls to this number will be blocked."],
        "Remove",
    )


async def propose_cancel_callback(callback_id: str):
    cb = next(
        (c for c in await backend().get_callbacks() if c.get("callback_id") == callback_id), None
    )
    if not cb:
        return cards.result_card(
            "No such callback", ["That scheduled callback was not found."], ok=False
        ), {"status": "not_found"}
    who = cb.get("patient_name") or cards.phone(cb.get("phone"))
    return await propose(
        "cancel_callback",
        {"callback_id": callback_id},
        f"Cancel the scheduled callback to {who}?",
        [f"Scheduled for {cards.when(cb.get('run_at'))}."],
        "Cancel callback",
    )


async def propose_cooldown(enabled: bool):
    line = (
        "A patient is texted at most once per 24 hours."
        if enabled
        else "Testing aid: a patient can be texted again immediately."
    )
    return await propose(
        "cooldown",
        {"enabled": bool(enabled)},
        f"Turn the 24-hour re-text limit {'on' if enabled else 'off'}?",
        [line],
        "Turn on" if enabled else "Turn off",
    )


# --- execution -------------------------------------------------------------------------


async def execute(proposal_id: str):
    """Run one confirmed proposal. Unknown, forged, expired, already-used or other-
    conversation ids all get the same answer and nothing runs."""
    t = turn.current()
    p = await t.proposals.claim(proposal_id)
    if p is None:
        return cards.result_card(
            "This confirmation has expired",
            ["Ask again to get a new confirmation. No action was taken."],
            ok=False,
        )
    log.info(
        "executing confirmed action",
        extra=safe_extra({"event": "nurse.execute", "kind": p.kind, "verified_user": bool(t.user)}),
    )
    k, a = p.kind, p.params
    b = t.backend
    try:
        if k == "place_call":
            try:
                r = await b.place_call(a["body"])
            except BackendError as e:
                if e.status != 409:
                    raise
                # A held language (or another conflict): the API's own words.
                detail = str(e).split(": ", 1)[-1]
                return cards.result_card(
                    f"Not called: {a['body']['patient_name']}", [detail], ok=False
                )
            return cards.result_card(
                f"Follow-up call placed to {a['body']['patient_name']}",
                [f"Call status: {r.get('status')}. Results appear when the call ends."],
                buttons=[("View this call", cards.event("view_call", call_sid=r["call_sid"]))],
            )
        if k == "add_entries":
            r = await b.bulk_add_entries(a["entries"])
            return cards.result_card(
                f"Added {r.get('count', len(a['entries']))} to the outreach worklist",
                [],
                buttons=[("View worklist", cards.event("call_list"))],
            )
        if k == "remove_entry":
            await b.delete_entry(a["entry_id"])
            return cards.result_card(
                "Removed from the outreach worklist",
                [],
                buttons=[("View worklist", cards.event("call_list"))],
            )
        if k == "allow_add":
            await b.add_allowlist(a["phone"], a["label"])
            return cards.result_card(
                f"{cards.phone(a['phone'])} is now approved for calls",
                [],
                buttons=[("View approved numbers", cards.event("allowlist"))],
            )
        if k == "allow_remove":
            await b.remove_allowlist(a["phone"])
            return cards.result_card(
                f"Approval removed for {cards.phone(a['phone'])}",
                [],
                buttons=[("View approved numbers", cards.event("allowlist"))],
            )
        if k == "cancel_callback":
            await b.cancel_callback(a["callback_id"])
            return cards.result_card(
                "Scheduled callback canceled",
                [],
                buttons=[("View text follow-up", cards.event("followups"))],
            )
        if k == "cooldown":
            await b.set_sms_setting("invite_cooldown_enabled", a["enabled"])
            return cards.result_card(
                f"24-hour re-text limit turned {'on' if a['enabled'] else 'off'}",
                [],
                buttons=[("View text follow-up", cards.event("followups"))],
            )
        from . import worklists

        card = await worklists.execute(k, a)
        if card is not None:
            return card
    except BackendError as e:
        return cards.result_card(f"Not completed: {p.title.rstrip('?')}", [str(e)], ok=False)
    return cards.error_card(f"Unknown action {k}.")


# --- CSV import (a file attached in the chat) --------------------------------------

CSV_COLUMNS = (
    "phone",
    "patient_name",
    "patient_id",
    "patient_dob",
    "operation_name",
    "operation_date",
    "is_diabetic",
    "language",
    "call_in_english",
)
OPTIONAL_CSV_COLUMNS = ("language", "call_in_english")
CSV_MAX_BYTES = 1_000_000


def parse_call_list_csv(raw: bytes) -> list[dict]:
    """Same columns as the dashboard's import."""
    if len(raw) > CSV_MAX_BYTES:
        raise ValueError("The file is larger than 1 MB.")
    text = raw.decode("utf-8-sig", errors="replace")
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        r = {
            (k or "").strip().lower(): (v or "").strip() for k, v in r.items() if isinstance(v, str)
        }
        if not any(r.values()):
            continue
        r["is_diabetic"] = r.get("is_diabetic", "").lower() in ("true", "yes", "y", "1")
        row = {k: r.get(k) for k in CSV_COLUMNS if k not in OPTIONAL_CSV_COLUMNS}
        # The optional language columns, only when the file has them.
        if r.get("language"):
            row["language"] = r["language"]
        if r.get("call_in_english"):
            row["call_in_english"] = _yes(r["call_in_english"])
        out.append(row)
    return out


# --- click registry ----------------------------------------------------------------


async def handle_click(name: str, ctx: dict):
    """Deterministic: a button press never goes through the model. Returns a Card,
    or None for an unknown button."""
    from . import worklists as wl

    if name == "confirm":
        return await execute(str(ctx.get("proposal_id", "")))
    if name == "dismiss":
        await turn.current().proposals.discard(str(ctx.get("proposal_id", "")))
        return cards.result_card("Canceled", ["No action was taken."])
    list_keys = (
        "status",
        "needs_follow_up",
        "patient",
        "today",
        "before_today",
        "since_hours",
        "offset",
    )
    views = {
        "view_call": lambda: view_call(call_sid=ctx.get("call_sid")),
        "show_transcript": lambda: view_transcript(call_sid=ctx.get("call_sid")),
        "play_recording": lambda: play_recording(ctx["call_sid"]),
        "list_calls": lambda: view_calls(**{k: ctx[k] for k in list_keys if k in ctx}),
        "follow_up_queue": view_follow_up_queue,
        "call_list": view_call_list,
        "followups": view_sms_followups,
        "allowlist": view_allowlist,
        "welcome": wl.view_home,
        "call_form": lambda: show_form(),
        "submit_call_form": lambda: submit_call_form(ctx),
        "propose_redial": lambda: propose_redial(ctx["call_sid"]),
        "wl_queue": wl.view_queue,
        "wl_called": wl.view_called,
        "wl_needs": lambda: wl.view_needs(ctx.get("group", "now")),
        "wl_cleared": wl.view_cleared,
        "wl_settings": wl.settings_view,
        "propose_call_now": lambda: wl.propose_call_now(ctx["entry_id"]),
        "wl_cancel_form": lambda: wl.cancel_form(ctx["entry_id"]),
        "wl_cancel_submit": lambda: wl.propose_cancel(ctx["entry_id"], ctx.get("reason") or ""),
        "wl_reschedule_form": lambda: wl.reschedule_form(ctx["entry_id"]),
        "wl_reschedule_submit": lambda: wl.propose_reschedule(
            ctx["entry_id"], ctx.get("choice"), ctx.get("at")
        ),
        "wl_tier_form": lambda: wl.tier_form(ctx["call_sid"]),
        "wl_tier_submit": lambda: wl.submit_tier_form(ctx["call_sid"], ctx),
        "show_texts": lambda: wl.view_texts(ctx["call_sid"]),
        "patient_history": lambda: wl.view_history(call_sid=ctx.get("call_sid")),
        "propose_remove_entry": lambda: propose_remove_entry(entry_id=ctx.get("entry_id")),
        "propose_allow_remove": lambda: propose_allow_remove(ctx["phone"]),
        "propose_cancel_callback": lambda: propose_cancel_callback(ctx["callback_id"]),
        "propose_cooldown": lambda: propose_cooldown(bool(ctx.get("enabled"))),
    }
    if name not in views:
        return None
    card, _ = await views[name]()
    return card
