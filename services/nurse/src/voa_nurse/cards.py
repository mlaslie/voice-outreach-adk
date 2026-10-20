"""A2UI v0.9 cards, built in Python rather than written by the model.

The model decides *what* to show (by calling tools); this module decides *how*.
That keeps the JSON schema-valid on every turn and keeps patient data exactly as
the backend returned it.

Every card can also render as plain text (``Card.as_text``), which is what the
Gemini Enterprise mobile app gets: it renders no A2UI at all.

Components used, all verified in Gemini Enterprise web (a2ui-gemini-enterprise
skill): Card, Column, Row, Text, Button (event actions and the ``openUrl``
functionCall), Divider, Icon, Modal, Tabs, AudioPlayer and TextField/CheckBox/
ChoicePicker/DateTimeInput bound to the data model.

Recordings (review A-9): the private API's recording URL is never sent to the
browser. ``recording_card`` plays a short-lived signed link instead, minted on
demand by the private API for ONE call, valid for 15 minutes and served by the
public gateway (HTTP Range, so the player can seek). The link only ever goes into
the AudioPlayer; it is never returned to the model or logged.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from voa_core import timefmt

from . import triage

V09_BASIC = "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"
PAGE = 8

# Set once at startup by configure(); module globals so every card sees them.
BRAND = "#22873B"
TZ = ZoneInfo("America/New_York")
DASHBOARD_URL = ""
DISPLAY_NAME = "Post-op Calling Agent (ADK)"
# Clinic-wide nurse time format ("12h" | "24h"). configure() sets the config default;
# worklists.refresh_time_format() follows the dashboard's Settings at runtime.
TIME_FORMAT: timefmt.TimeFormat = "24h"


def configure(
    *,
    timezone: str,
    dashboard_url: str = "",
    brand: str = "#22873B",
    display_name: str = DISPLAY_NAME,
    time_format: timefmt.TimeFormat = "24h",
) -> None:
    global BRAND, TZ, DASHBOARD_URL, DISPLAY_NAME, TIME_FORMAT
    TZ = ZoneInfo(timezone)
    DASHBOARD_URL = dashboard_url.rstrip("/")
    BRAND = brand
    DISPLAY_NAME = display_name
    TIME_FORMAT = time_format


def set_time_format(fmt: timefmt.TimeFormat) -> None:
    global TIME_FORMAT
    TIME_FORMAT = fmt


def clock(dt) -> str:
    """A clinic-local clock time in the clinic's nurse time format."""
    return timefmt.clock(dt, TIME_FORMAT)


# Colour status markers, one per verdict tone. Colour comes from emoji: the v0.9
# Icon takes only a name and no colour. Emoji also survive in as_text().
#
# The green check means the patient was ASSESSED and cleared. Voicemail, no
# answer, a dropped call, a call in progress and "no disposition recorded" are
# all "incomplete" (white circle): a patient nobody reached must never look done.
MARK = {
    "emergency": ("\U0001f534", "Emergency"),
    "urgent": ("\U0001f7e0", "RN callback today"),
    "routine": ("\U0001f7e1", "RN callback next business day"),
    "info": ("\U0001f535", "FYI, no callback"),
    "clear": ("✅", "No follow-up needed"),
    "incomplete": ("⚪", "Not assessed"),
}
MARK_ORDER = ("emergency", "urgent", "routine", "info", "clear", "incomplete")
LEVEL_MARK = {
    "alert": "\U0001f534",
    "watch": "\U0001f7e1",
    "normal": "✅",
    "unknown": "❓",  # the pale ❔ all but vanished on Gemini Enterprise's grey card
    "unasked": "⚪",
}


def marked(card: Card, emoji: str, first: str, *more: str | None) -> str:
    """A marker beside a block of lines, lined up with the FIRST line: same text size
    as the line, top-aligned (centred markers sat between the lines of a two-line
    block, and heading-size markers sat lower than body text)."""
    return card.row(card.text(emoji, "body"), card.col(first, *more), align="start")


def sentence(s: str) -> str:
    s = (s or "").strip()
    return s[:1].upper() + s[1:]


def mark(call: dict) -> tuple[str, str]:
    return MARK[triage.verdict_for(call)["tone"]]


def mark_summary(calls: list[dict]) -> str:
    counts = dict.fromkeys(MARK_ORDER, 0)
    for c in calls:
        counts[triage.verdict_for(c)["tone"]] += 1
    return " · ".join(f"{MARK[t][0]} {counts[t]} {MARK[t][1]}" for t in MARK_ORDER if counts[t])


def local(iso) -> datetime | None:
    dt = triage.parse_time(iso)
    return dt.astimezone(TZ) if dt else None


def when(iso, with_date: bool = True) -> str:
    """Clinic-local time the way a nurse scans it: "Today 14:05", "Yesterday 09:10",
    otherwise "10/03/2026 14:05" (12-hour clinics: "Today 2:05 PM").
    Missing -> "—"; garbled -> shown as is."""
    if not iso:
        return "—"
    dt = local(iso)
    if dt is None:
        return str(iso)
    hm = clock(dt)
    if not with_date:
        return hm
    days = (datetime.now(TZ).date() - dt.date()).days
    if days == 0:
        return f"Today {hm}"
    if days == 1:
        return f"Yesterday {hm}"
    if days == -1:
        return f"Tomorrow {hm}"
    return f"{dt:%m/%d/%Y} {hm}"


def us_date(iso_date) -> str:
    try:
        return date.fromisoformat(iso_date).strftime("%m/%d/%Y")
    except (TypeError, ValueError):
        return iso_date or "—"


def age(dob) -> int | None:
    try:
        b, t = date.fromisoformat(dob), datetime.now(TZ).date()
    except (TypeError, ValueError):
        return None
    return t.year - b.year - ((t.month, t.day) < (b.month, b.day))


def pod(procedure_date, call_iso) -> int | None:
    """Post-procedure day on which the call was placed (procedure day = POD 0)."""
    try:
        d0 = date.fromisoformat(procedure_date)
    except (TypeError, ValueError):
        return None
    called = local(call_iso)
    return ((called.date() if called else datetime.now(TZ).date()) - d0).days


def duration(seconds) -> str:
    try:
        m, s_ = divmod(int(seconds), 60)
    except (TypeError, ValueError):
        return ""
    return f"{m}m {s_:02d}s" if m else f"{s_}s"


def phone(p) -> str:
    if isinstance(p, str) and len(p) == 12 and p.startswith("+1"):
        return f"({p[2:5]}) {p[5:8]}-{p[8:]}"
    return p or "—"


def event(_name: str, **context) -> dict:
    return {"event": {"name": _name, "context": context}}


def open_url(url: str) -> dict:
    return {"functionCall": {"call": "openUrl", "args": {"url": url}}}


class Card:
    """One A2UI surface: a Card holding a Column. Components are a flat list
    referenced by id, which is what A2UI requires."""

    def __init__(self, title: str | None = None, icon: str | None = None, wrap: bool = True):
        self.comps: list[dict] = []
        self.body: list[str] = []
        self.wrap = wrap
        self.data_model: dict | None = None  # initial values for input components
        self.is_menu = False  # the home card gets no Main menu button
        self.has_menu_button = False  # placed by the card itself; don't add another
        self.has_dashboard_button = False
        self.text_lines: list[str] = []  # appended to as_text() only (text mode)
        self._n = 0
        if title:
            self.put(self.titled(title, icon))

    # --- primitives (return an id; put() places it in the body) ---

    def add(self, component: str, **props) -> str:
        cid = f"c{self._n}"
        self._n += 1
        self.comps.append({"id": cid, "component": component, **props})
        return cid

    def text(self, s: str, variant: str = "body") -> str:
        return self.add("Text", text=s, variant=variant)

    def icon(self, name: str) -> str:
        return self.add("Icon", name=name)

    def row(self, *children: str | None, justify: str | None = None, align: str = "center") -> str:
        props: dict = {"children": [c for c in children if c], "align": align}
        if justify:
            props["justify"] = justify
        return self.add("Row", **props)

    def col(self, *children: str | None) -> str:
        return self.add("Column", children=[c for c in children if c])

    def button(self, label: str, action: dict, primary: bool = False) -> str:
        props = {"child": self.text(label), "action": action}
        if primary:
            props["variant"] = "primary"
        return self.add("Button", **props)

    def dashboard_button(self, label: str = "Open dashboard") -> str | None:
        if not DASHBOARD_URL:
            return None
        self.has_dashboard_button = True
        return self.button(label, open_url(DASHBOARD_URL))

    def divider(self) -> str:
        return self.add("Divider")

    def titled(self, title: str, icon: str | None = None, variant: str = "h3") -> str:
        t = self.text(title, variant)
        return self.row(self.icon(icon), t) if icon else t

    def menu_button(self) -> str:
        """A Main menu button to place in the card's own button row."""
        self.has_menu_button = True
        return self.button("Main menu", event("welcome"))

    def add_menu_button(self) -> None:
        """The footer every card gets: Main menu, plus a quick link to the web
        dashboard unless the card already has one."""
        if not self.has_menu_button:
            dash = None if self.has_dashboard_button else self.dashboard_button("Dashboard")
            self.put(self.row(self.menu_button(), dash))

    def put(self, *ids: str | None) -> Card:
        self.body.extend(i for i in ids if i)
        return self

    def line(self, s: str, variant: str = "body") -> Card:
        return self.put(self.text(s, variant))

    def buttons(self, *ids: str | None) -> Card:
        kept = [i for i in ids if i]
        return self.put(self.row(*kept)) if kept else self

    # --- output ---

    def messages(self) -> list[dict]:
        sid = f"app_{uuid.uuid4().hex[:8]}"  # fresh per card, or GE rewrites the previous one
        comps = list(self.comps)
        col = {"id": "body", "component": "Column", "children": self.body}
        if self.wrap:
            comps += [col, {"id": "root", "component": "Card", "child": "body"}]
        else:
            comps.append({**col, "id": "root"})
        out = [
            {
                "version": "v0.9",
                "createSurface": {
                    "surfaceId": sid,
                    "catalogId": V09_BASIC,
                    "theme": {"primaryColor": BRAND},
                },
            },
            {"version": "v0.9", "updateComponents": {"surfaceId": sid, "components": comps}},
        ]
        if self.data_model is not None:
            out.append(
                {
                    "version": "v0.9",
                    "updateDataModel": {"surfaceId": sid, "path": "/", "value": self.data_model},
                }
            )
        return out

    def as_text(self) -> str:
        by_id = {c["id"]: c for c in self.comps}

        def render(cid: str) -> str:
            c = by_id.get(cid, {})
            kind = c.get("component")
            if kind == "Text":
                return c["text"]
            if kind == "Row":
                return "  ".join(filter(None, (render(x) for x in c["children"])))
            if kind in ("Column", "List"):
                return "\n".join(filter(None, (render(x) for x in c["children"])))
            if kind == "Button":
                return f"[{render(c['child'])}]"
            if kind == "Divider":
                return "—"
            if kind in ("TextField", "DateTimeInput", "CheckBox", "ChoicePicker"):
                return f"{c['label']}: ____"
            if kind == "AudioPlayer":  # never the URL: it is a bearer link
                return f"[Audio: {c.get('description') or 'recording'}. Plays on the web.]"
            return ""

        lines = [render(i) for i in self.body] + self.text_lines
        return "\n".join(filter(None, lines))


# --- reading cards -----------------------------------------------------------------


def note(text: str) -> Card:
    """The model's short reply, shown above the cards it asked for."""
    return Card(wrap=False).line(text)


def error_card(message: str) -> Card:
    return Card("Unable to complete request", "error").line(message)


def truncation_line(card: Card, truncated: bool, shown: int) -> None:
    """Review A-1: lists are bounded by the API; say so instead of pretending."""
    if truncated:
        card.line(
            f"Based on the newest {shown} calls; older records are in the dashboard.", "caption"
        )


def call_row(card: Card, c: dict) -> str:
    name = c.get("patient_name") or phone(c.get("to"))
    emoji, words = mark(c)
    meta = [words, triage.status_label(c), when(c.get("initiated_at"))]
    if qs := triage.questions_summary(c):
        meta.insert(1, qs)
    if c.get("patient_id"):
        meta.insert(0, f"MRN {c['patient_id']}")
    info = card.col(card.text(name, "h5"), card.text(" · ".join(meta), "caption"))
    open_btn = card.button("View", event("view_call", call_sid=c["doc_id"]))
    return card.row(
        card.row(card.text(emoji, "h5"), info, align="start"), open_btn, justify="spaceBetween"
    )


def calls_card(
    calls: list[dict],
    title: str,
    *,
    filter_args: dict | None = None,
    offset: int = 0,
    truncated: bool = False,
) -> Card:
    card = Card(title, "call")
    total = len(calls)
    if not total:
        card.line("No calls found.", "caption")
        truncation_line(card, truncated, total)
        return card
    page = calls[offset : offset + PAGE]
    card.line(mark_summary(calls))
    card.line(f"Showing {offset + 1}–{offset + len(page)} of {total}", "caption")
    for i, c in enumerate(page):
        if i:
            card.put(card.divider())
        card.put(call_row(card, c))
    truncation_line(card, truncated, total)
    more = None
    if offset + PAGE < total:
        more = card.button(
            "Show more", event("list_calls", offset=offset + PAGE, **(filter_args or {}))
        )
    return card.buttons(more, card.dashboard_button())


def follow_up_card(calls: list[dict], truncated: bool = False) -> Card:
    outstanding = triage.follow_up_calls(calls)
    card = Card("Needs RN follow-up", "notifications")
    if not outstanding:
        card.line("No patients currently need RN follow-up.")
        truncation_line(card, truncated, len(calls))
        return card
    card.line(mark_summary(outstanding))
    card.line("Highest acuity first", "caption")
    for c in outstanding[:PAGE]:
        card.put(card.divider())
        reason = triage.reason_summary(c) or triage.verdict_for(c)["reason"]
        meta = " · ".join(
            x
            for x in (
                f"MRN {c['patient_id']}" if c.get("patient_id") else None,
                phone(c.get("to")),
                f"called {when(c.get('initiated_at'))}",
                triage.questions_summary(c),
            )
            if x
        )
        name = c.get("patient_name") or phone(c.get("to"))
        info = card.col(
            card.text(f"{name}: {triage.follow_up_label(c['follow_up'])}", "h5"),
            card.text(reason, "body"),
            card.text(meta, "caption"),
        )
        card.put(
            card.row(
                card.row(card.text(mark(c)[0], "h5"), info, align="start"),
                card.button("View", event("view_call", call_sid=c["doc_id"])),
                justify="spaceBetween",
            )
        )
    if len(outstanding) > PAGE:
        card.line(f"{len(outstanding) - PAGE} more not shown.", "caption")
    truncation_line(card, truncated, len(calls))
    return card


def has_recording(c: dict) -> bool:
    return bool(c.get("recording_uri") or c.get("recording_url"))


def call_detail_card(c: dict) -> Card:
    v = triage.verdict_for(c)
    ctx = c.get("patient_context") or {}
    name = c.get("patient_name") or ctx.get("patient_name") or "Unknown patient"
    card = Card(name, "person")
    facts = [f"MRN {c.get('patient_id') or '—'}"]
    if "is_diabetic" in ctx:
        facts.append(f"DM: {'Yes' if ctx['is_diabetic'] else 'No'}")
    facts.append(phone(c.get("to")))
    card.line(" · ".join(facts), "caption")
    if ctx.get("operation_name") or ctx.get("operation_date"):
        proc = f"Procedure: {ctx.get('operation_name') or 'not recorded'}"
        if ctx.get("operation_date"):
            proc += f" on {us_date(ctx['operation_date'])}"
            n = pod(ctx["operation_date"], c.get("initiated_at"))
            if n is not None and n >= 0:
                proc += f" · called POD {n}"
        card.line(proc, "caption")
    reasons = triage.follow_up_reasons(c) if triage.is_assessment(c) else []
    card.put(
        card.row(
            card.text(MARK[v["tone"]][0], "h2"),
            card.col(
                card.text(v["headline"], "h4"),
                None if reasons else card.text(v["reason"]),  # listed in full below
            ),
        )
    )
    nurse_set = follow_up_reasons_block(card, c, reasons)

    questions = triage.patient_questions(c)
    uncaptured = triage.has_uncaptured_questions(c)
    findings = triage.findings_for(c)
    if questions or uncaptured:  # listed in full in their own block below
        findings = [f for f in findings if f["label"] != "Patient questions"]
    if findings and c.get("survey_responses"):
        card.put(card.divider(), card.text("Symptom review", "h5"))
        for f in findings:
            label = f"{f['label']}: {f['value']}"
            if f["level"] == "alert":
                label = f"(!) {label}"  # abnormal flag, as in an EHR result
            parts = [card.text(label)]
            if f.get("detail"):
                parts.append(card.text(f["detail"], "caption"))
            card.put(marked(card, LEVEL_MARK[f["level"]], *parts))

    if questions or uncaptured:
        patient_questions_block(card, questions)

    card.put(card.divider())
    timing = [f"Call: {triage.status_label(c)}", when(c.get("initiated_at"))]
    if c.get("answered_at"):
        timing.append(f"answered {when(c['answered_at'], False)}")
    if c.get("duration_seconds") is not None:
        timing.append(duration(c["duration_seconds"]))
    if c.get("failure_reason") or c.get("failure_message"):
        timing.append(c.get("failure_reason") or c["failure_message"])
    card.line(" · ".join(timing), "caption")
    override = c.get("follow_up_override") or {}
    note_repeats_reasons = nurse_set and (override.get("reason") or "") == "; ".join(
        r["text"] for r in reasons
    )
    if override and not note_repeats_reasons:
        card.line(
            f"Tier set by {override.get('set_by') or 'nurse'} {when(override.get('at'))}: "
            f"{override.get('reason') or ''}",
            "caption",
        )

    sid = c["doc_id"]
    rec = None
    if has_recording(c):
        # A-9: the button asks for a short-lived signed link to this one call
        # (recording_card); the private API's recording URL never reaches the browser.
        rec = card.button("Play recording", event("play_recording", call_sid=sid))
    dash = card.dashboard_button("Dashboard")
    assessed = triage.is_assessment(c)
    sms = c.get("channel") == "sms"
    # Two short rows: six buttons in one row ran past the card's edge in Gemini
    # Enterprise. Row 1 is for reviewing the call, row 2 for acting on it.
    card.buttons(
        card.button("History", event("patient_history", call_sid=sid)),
        card.button(
            "Texts" if sms else "Transcript",
            event("show_texts" if sms else "show_transcript", call_sid=sid),
        ),
        rec,
    )
    return card.buttons(
        card.button("Call back", event("propose_redial", call_sid=sid)),
        card.button("Change tier", event("wl_tier_form", call_sid=sid)) if assessed else None,
        dash,
        card.menu_button(),
    )


RECORDING_TTL_NOTE = "This link works for 15 minutes. Click Play recording again for a fresh one."


def recording_card(c: dict, url: str) -> Card:
    """Plays one call's recording in the card. ``url`` is the short-lived signed
    gateway link from ``POST /api/calls/{id}/recording-link`` (A-9): it goes into
    the AudioPlayer and nowhere else."""
    name = c.get("patient_name") or (c.get("patient_context") or {}).get("patient_name")
    name = name or phone(c.get("to"))
    card = Card(f"Call recording: {name}", "volumeUp")
    meta = [when(c.get("initiated_at")), duration(c.get("duration_seconds"))]
    card.line(" · ".join(m for m in meta if m), "caption")
    card.put(card.add("AudioPlayer", url=url, description=f"Call recording, {name}"))
    card.line(RECORDING_TTL_NOTE, "caption")
    return card.buttons(
        card.button("Back to call", event("view_call", call_sid=c["doc_id"])),
        card.dashboard_button("Open in dashboard"),
        card.menu_button(),
    )


REASON_TONE = {
    "emergency": "emergency",
    "urgent_same_day": "urgent",
    "routine_next_business_day": "routine",
    "informational": "info",
    "none": "clear",
}


def reason_mark(tier: str | None) -> tuple[str, str]:
    """The colour marker and words for one reason's tier (the verdict's own MARKs)."""
    if tier in REASON_TONE:
        return MARK[REASON_TONE[tier]]
    return ("❔", "Tier not recorded")


def _reason_rows(card: Card, reasons: list[dict]) -> None:
    for r in reasons:
        emoji, words = reason_mark(r["tier"])
        card.put(marked(card, emoji, card.text(sentence(r["text"])), card.text(words, "caption")))


def follow_up_reasons_block(card: Card, c: dict, reasons: list[dict]) -> bool:
    """Every reason for the follow-up, each with its own tier, most urgent first.
    When a nurse set them: who and when, then the automated assessment's original
    reasons underneath. Returns whether a nurse set them."""
    if not reasons:
        return False
    nurse = triage.reasons_set_by_nurse(reasons)
    head = "Reasons for follow-up" + (", most urgent first" if len(reasons) > 1 else "")
    card.put(card.divider(), card.text(head, "h5"))
    if nurse:
        override = c.get("follow_up_override") or {}
        who = reasons[0]["set_by"] or override.get("set_by") or "nurse"
        at = reasons[0]["at"] or override.get("at")
        card.line(f"Set by {who}" + (f" {when(at)}" if at else ""), "caption")
    _reason_rows(card, reasons)
    if nurse and (original := triage.agent_original_reasons(c)):
        card.line("Automated assessment's original reasons", "caption")
        _reason_rows(card, original)
    return nurse


QUESTION_MARK = {triage.Q_IMMEDIATE: MARK["urgent"][0], triage.Q_NEXT_DAY: MARK["routine"][0]}


def patient_questions_block(card: Card, questions: list[dict]) -> None:
    """The patient's questions in their own words, immediate first. The automated
    caller never answers them; a nurse does."""
    card.put(card.divider(), card.text("Patient questions — nurse follow-up required", "h5"))
    if not questions:
        card.line("Patient has questions — none captured; call to ask.")
        return
    for q in questions:
        card.put(
            marked(
                card,
                QUESTION_MARK[q["priority"]],
                card.text(f"“{q['text']}”"),
                card.text(f"{q['category_label']} · {q['priority_label']}", "caption"),
            )
        )


def transcript_card(c: dict, t: dict, limit: int = 40) -> Card:
    card = Card(f"Call transcript: {c.get('patient_name') or c['doc_id']}", "visibility")
    card.line(when(c.get("initiated_at")), "caption")
    if not t.get("available"):
        return card.line(t.get("reason") or "No transcript is available for this call.")
    turns = t.get("turns") or []
    if not turns:
        card.line("No transcript content was captured for this call.")
    for turn in turns[:limit]:
        who = "Automated caller" if turn.get("role") == "agent" else "Patient"
        card.put(card.col(card.text(who, "caption"), card.text(turn.get("text") or "")))
    if len(turns) > limit:
        card.line(
            f"{len(turns) - limit} more exchanges. Open the dashboard for the full transcript.",
            "caption",
        )
    return card.buttons(
        card.button("Back to call summary", event("view_call", call_sid=c["doc_id"]))
    )


LIST_STATUS = {
    "pending": "Pending",
    "calling": "Calling now",
    "completed": "Reached",
    "blocked": "Unable to dial",
    "canceled": "Cancelled",
}


def run_line(run: dict | None) -> str | None:
    if not run:
        return None
    head = {
        "running": "Outreach in progress",
        "completed": "Last outreach completed",
        "canceled": "Last outreach stopped",
        "error": "Last outreach failed",
        "budget_exhausted": "Last outreach stopped: daily call limit reached",
    }.get(run.get("status"), run.get("status") or "Outreach")
    if run.get("cancel_requested") and run.get("status") == "running":
        head = "Outreach stopping"
    return (
        f"{head}: {run.get('dispatched', 0)} of {run.get('total', 0)} dialed, "
        f"{run.get('succeeded', 0)} connected, {run.get('failed', 0)} unsuccessful"
    )


def call_list_card(entries: list[dict], run_resp: dict) -> Card:
    run = run_resp.get("run")
    running = bool(run_resp.get("active"))
    card = Card("Outreach worklist", "event")
    if line := run_line(run):
        card.line(line, "h5" if running else "caption")
        for f in (run or {}).get("in_flight") or []:
            card.line(f"On the line now: {f.get('patient_name')}", "caption")
    counts = {k: sum(e.get("list_status") == k for e in entries) for k in LIST_STATUS}
    card.line(
        " · ".join(f"{n} {LIST_STATUS[k].lower()}" for k, n in counts.items() if n)
        or "No patients on the worklist."
    )
    for e in entries[:12]:
        n = e.get("attempts") or 0
        meta = [
            LIST_STATUS.get(e.get("list_status"), e.get("list_status") or "—"),
            f"{n} call attempt{'s' * (n != 1)}",
        ]
        if e.get("last_error"):
            meta.append(str(e["last_error"])[:80])
        info = card.col(
            card.text(f"{e.get('patient_name')} · MRN {e.get('patient_id')}", "h5"),
            card.text(f"{phone(e.get('phone'))} · " + " · ".join(meta), "caption"),
        )
        remove = (
            None
            if running
            else card.button("Remove", event("propose_remove_entry", entry_id=e["entry_id"]))
        )
        card.put(card.divider(), card.row(info, remove, justify="spaceBetween"))
    if len(entries) > 12:
        card.line(f"{len(entries) - 12} more not shown.", "caption")
    return card.buttons(
        card.button("Refresh", event("call_list"), primary=running),
        card.button("To be called", event("wl_queue")),
    )


SESSION_STATE = {
    "awaiting_choice": "Awaiting patient reply",
    "awaiting_time": "Selecting a callback time",
    "scheduled": "Callback scheduled",
    "awaiting_dob": "Verifying identity",
    "survey": "Completing assessment by text",
    "recall_dispatched": "Callback queued",
    "dialing": "Calling now",
    "closed": "Closed",
    "expired": "No response",
    "opted_out": "Opted out (STOP)",
}
CALLBACK_STATUS = {
    "scheduled": "Scheduled",
    "dialing": "Calling",
    "dispatched": "Called",
    "canceled": "Canceled",
    "superseded": "Replaced",
    "failed": "Failed",
}


def followups_card(sessions: list[dict], callbacks: list[dict], settings: dict) -> Card:
    card = Card("Text message follow-up", "mail")
    open_cbs = [c for c in callbacks if c.get("status") in (None, "scheduled", "dialing")]
    live = [s for s in sessions if s.get("state") not in ("closed", "expired", "opted_out")]
    card.line(f"{len(live)} active text threads · {len(open_cbs)} callbacks scheduled", "caption")
    if not settings.get("invite_cooldown_enabled", True):
        card.line("24-hour re-text limit is OFF: a patient can be texted again immediately.", "h5")
    if open_cbs:
        card.put(card.divider(), card.text("Scheduled callbacks", "h5"))
        for cb in sorted(open_cbs, key=lambda c: c.get("run_at") or "")[:PAGE]:
            who = cb.get("patient_name") or phone(cb.get("phone"))
            info = card.col(
                card.text(f"{who}: {when(cb.get('run_at'))}"),
                card.text(
                    CALLBACK_STATUS.get(cb.get("status"), cb.get("status") or "Booked"), "caption"
                ),
            )
            cancel = card.button(
                "Cancel callback", event("propose_cancel_callback", callback_id=cb["callback_id"])
            )
            card.put(card.row(card.icon("calendarToday"), info, cancel, justify="spaceBetween"))
    if sessions:
        card.put(card.divider(), card.text("Text threads", "h5"))
        recent = sorted(sessions, key=lambda s: s.get("updated_at") or "", reverse=True)[:PAGE]
        for s in recent:
            state = SESSION_STATE.get(s.get("state"), s.get("state") or "—")
            extra = f" · callback {s['scheduled_for_text']}" if s.get("scheduled_for_text") else ""
            mc = s.get("message_count") or 0
            who = s.get("patient_name") or phone(s.get("phone"))
            card.put(
                card.col(
                    card.text(f"{who}: {state}"),
                    card.text(
                        f"{mc} message{'s' * (mc != 1)} · last activity "
                        f"{when(s.get('updated_at'))}{extra}",
                        "caption",
                    ),
                )
            )
    on = settings.get("invite_cooldown_enabled", True)
    return card.buttons(
        card.button(
            "Turn 24-hour limit off" if on else "Turn 24-hour limit on",
            event("propose_cooldown", enabled=not on),
            primary=not on,
        ),
        card.button("Refresh", event("followups")),
    )


def allowlist_card(resp: dict) -> Card:
    card = Card("Approved phone numbers", "lock")
    if resp.get("unrestricted"):
        card.line("The dialing allowlist is open: every number can be called.", "h5")
    else:
        card.line("Pilot safeguard: only these numbers can be called.", "caption")
    for e in resp.get("entries", []):
        label = e.get("label") or ("set in deployment config" if e.get("source") == "env" else "")
        info = card.col(card.text(phone(e.get("phone")), "h5"), card.text(label, "caption"))
        remove = (
            card.button("Remove", event("propose_allow_remove", phone=e["phone"]))
            if e.get("editable")
            else None
        )
        card.put(card.divider(), card.row(info, remove, justify="spaceBetween"))
    return card.line(
        "Consent to text messages is recorded in the dashboard, where the patient sees the "
        "registered consent statement.",
        "caption",
    )


def choose_patient_card(query: str, choices: list[dict], then: str, key: str = "call_sid") -> Card:
    """Several patients match a name: one row per patient, each with a button that
    repeats the action for that patient. Never guess between them."""
    card = Card("Which patient?", "person")
    card.line(
        f"More than one patient matches “{query}”." if query else "More than one patient matches."
    )
    label = {
        "view_call": "View",
        "show_transcript": "Transcript",
        "propose_redial": "Call back",
        "wl_cancel_form": "Cancel call",
        "wl_tier_form": "Change tier",
        "wl_reschedule_form": "Reschedule",
        "patient_history": "History",
        "play_recording": "Play recording",
    }.get(then, "Select")
    for c in choices[:PAGE]:
        ctx = c.get("patient_context") or {}
        meta = [
            f"MRN {c['patient_id']}" if c.get("patient_id") else None,
            ctx.get("operation_name") or None,
            phone(c.get("to")),
            f"last call {when(c.get('initiated_at'))}",
        ]
        info = card.col(
            card.text(c.get("patient_name") or phone(c.get("to")), "h5"),
            card.text(" · ".join(m for m in meta if m), "caption"),
        )
        card.put(
            card.divider(),
            card.row(
                info, card.button(label, event(then, **{key: c["doc_id"]})), justify="spaceBetween"
            ),
        )
    if len(choices) > PAGE:
        card.line(f"{len(choices) - PAGE} more match. Use the MRN to narrow it down.", "caption")
    return card


# --- acting cards --------------------------------------------------------------------


def confirm_card(proposal_id: str, title: str, lines: list[str], confirm_label: str) -> Card:
    """The only door to a write. The Confirm button carries just the proposal id;
    in text mode the user types ``confirm <id>`` (never a bare "ok"/"yes": C-5)."""
    card = Card(title, "warning")
    for ln in lines:
        card.line(ln)
    card.line(f"No action is taken until you confirm. Reference {proposal_id}.", "caption")
    card.text_lines.append(f"To go ahead, reply: confirm {proposal_id}")
    return card.buttons(
        card.button(confirm_label, event("confirm", proposal_id=proposal_id), primary=True),
        card.button("Cancel", event("dismiss", proposal_id=proposal_id)),
    )


def result_card(
    title: str, lines: list[str], ok: bool = True, buttons: list[tuple[str, dict]] = ()
) -> Card:
    card = Card(title, "check" if ok else "error")
    for ln in lines:
        card.line(ln)
    return card.buttons(*(card.button(label, action) for label, action in buttons))


CALL_FORM_FIELDS = (
    "phone",
    "patient_name",
    "patient_id",
    "patient_dob",
    "operation_name",
    "operation_date",
    "is_diabetic",
)


def place_call_form_card(prefill: dict | None = None, problems: list[str] = ()) -> Card:
    """Patient details as a form. Submit sends the edited values back and leads to
    the usual confirmation card; it never dials by itself."""
    p = prefill or {}
    card = Card("New follow-up call", "call")
    for problem in problems:
        card.put(card.row(card.icon("error"), card.text(problem)))
    card.put(card.add("TextField", label="Patient phone number", value={"path": "/call/phone"}))
    card.put(
        card.add(
            "TextField",
            label="Patient name (first last, as the caller should say it)",
            value={"path": "/call/patient_name"},
        )
    )
    card.put(card.add("TextField", label="MRN", value={"path": "/call/patient_id"}))
    card.put(
        card.add(
            "DateTimeInput",
            label="DOB (used to verify identity; never read aloud)",
            value={"path": "/call/patient_dob"},
            enableDate=True,
            enableTime=False,
        )
    )
    card.put(
        card.add("TextField", label="Procedure (optional)", value={"path": "/call/operation_name"})
    )
    card.put(
        card.add(
            "DateTimeInput",
            label="Procedure date (optional)",
            value={"path": "/call/operation_date"},
            enableDate=True,
            enableTime=False,
        )
    )
    card.put(
        card.add(
            "CheckBox",
            label="Diabetic (adds blood glucose questions)",
            value={"path": "/call/is_diabetic"},
        )
    )
    card.buttons(
        card.button(
            "Review before calling",
            event("submit_call_form", **{f: {"path": f"/call/{f}"} for f in CALL_FORM_FIELDS}),
            primary=True,
        )
    )
    card.data_model = {
        "call": {
            f: (bool(p.get(f)) if f == "is_diabetic" else str(p.get(f) or ""))
            for f in CALL_FORM_FIELDS
        }
    }
    return card


def greeting() -> str:
    hour = datetime.now(TZ).hour
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"


def components_check_card() -> Card:
    """Debug card for the v0.9 components (``ui check``, off unless enabled).
    Review A-6: it embeds no patient data and no recording; inputs are fictional."""
    card = Card("A2UI component check", "settings")
    card.line(
        "Each item below is an A2UI component. Note which ones are missing, then press "
        "Submit to test that input values reach the agent."
    )
    card.put(
        card.row(
            card.text("1. Icons:"),
            card.icon("check"),
            card.icon("warning"),
            card.icon("error"),
            card.icon("phone"),
        )
    )
    card.put(
        card.row(
            card.text("1b. Colour markers:"), *(card.text(MARK[t][0], "h3") for t in MARK_ORDER)
        )
    )
    card.line("2. Inputs bound to the data model:")
    card.put(card.add("TextField", label="Patient name", value={"path": "/form/name"}))
    card.put(
        card.add(
            "DateTimeInput",
            label="Date of birth",
            value={"path": "/form/dob"},
            enableDate=True,
            enableTime=False,
        )
    )
    card.put(card.add("CheckBox", label="Diabetic", value={"path": "/form/diabetic"}))
    card.put(
        card.add(
            "ChoicePicker",
            label="Procedure",
            variant="mutuallyExclusive",
            options=[{"label": "Knee", "value": "knee"}, {"label": "Hip", "value": "hip"}],
            value={"path": "/form/procedure"},
        )
    )
    tab_a, tab_b = card.text("First tab content"), card.text("Second tab content")
    card.line("3. Tabs:")
    card.put(
        card.add(
            "Tabs", tabs=[{"title": "First", "child": tab_a}, {"title": "Second", "child": tab_b}]
        )
    )
    card.line("4. Modal (press to open):")
    card.put(
        card.add(
            "Modal",
            trigger=card.button("Open modal", event("noop")),
            content=card.text("Modal content rendered."),
        )
    )
    card.buttons(
        card.button(
            "Submit",
            event(
                "check_submit",
                patient_name={"path": "/form/name"},
                dob={"path": "/form/dob"},
                diabetic={"path": "/form/diabetic"},
                procedure={"path": "/form/procedure"},
            ),
            primary=True,
        ),
        card.dashboard_button("5. openUrl link"),
    )
    card.data_model = {
        "form": {
            "name": "Jordan Rivera",
            "dob": "1950-03-15",
            "diabetic": False,
            "procedure": ["knee"],
        }
    }
    return card
