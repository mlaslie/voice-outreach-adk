"""Who we may dial, when, and whether a call-list row may be dialled now.

Pure functions only: configuration, timezone names and ``now`` are passed in.

IMPORTANT, and easy to get wrong: an allowlist stops *strangers* being dialled. It
does NOT stop the people on the list being rung repeatedly; it authorises exactly
them. Dial budgets and the claim rule below are what bound repetition.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

from . import timefmt

# ---------------------------------------------------------------------------
# Booleans from loosely typed stores
# ---------------------------------------------------------------------------


def coerce_bool(value: Any) -> bool:
    """A real bool from a loosely typed value.

    The string ``"false"`` is truthy in Python, and a store that allows mixed types
    per field once reported a non-diabetic patient as diabetic because of it. Only
    real bools, the string "true" (any case, trimmed) and non-zero numbers are True.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    if isinstance(value, int | float):
        return bool(value)
    return False


# ---------------------------------------------------------------------------
# Destination allowlist
# ---------------------------------------------------------------------------

#: E.164. re.ASCII so ``\d``-style classes can't match Unicode decimal digits.
E164_RE = re.compile(r"^\+[1-9][0-9]{6,14}$", re.ASCII)

#: A prefix entry must pin at least this many digits, so "+1*" can't open a whole
#: numbering plan by typo.
MIN_PREFIX_DIGITS = 8


class AllowlistConfigError(ValueError):
    """Malformed or over-broad allowlist: refuse to start rather than guess."""


class AllowlistMode(StrEnum):
    UNSET = "unset"  # nothing configured: fail closed at dial time
    OPEN = "open"  # the literal "*": dial anything (explicit escape hatch)
    LIST = "list"


@dataclass(frozen=True)
class Allowlist:
    mode: AllowlistMode = AllowlistMode.UNSET
    exact: frozenset[str] = frozenset()
    prefixes: tuple[str, ...] = ()

    def matches(self, phone: str) -> bool:
        """Exact or prefix match. ``phone`` must already be canonical E.164."""
        if phone in self.exact:
            return True
        return any(phone.startswith(p) for p in self.prefixes)

    @property
    def is_empty(self) -> bool:
        return not self.exact and not self.prefixes


def parse_allowlist(raw: str | None) -> Allowlist:
    """Parse a comma-separated allowlist (replaces _parse_outbound_allowlist).

    Entries are exact E.164; a trailing ``*`` makes a prefix that must pin at least
    ``MIN_PREFIX_DIGITS`` digits; ``*`` alone means open. Raises
    ``AllowlistConfigError`` on anything malformed or too broad, so a bad config
    fails the deploy instead of silently widening who can be called.
    """
    raw = (raw or "").strip()
    if not raw:
        return Allowlist(AllowlistMode.UNSET)
    items = [i.strip() for i in raw.split(",") if i.strip()]
    if items == ["*"]:
        return Allowlist(AllowlistMode.OPEN)
    if "*" in items:
        raise AllowlistConfigError('allowlist: "*" must be the only entry if used.')
    exact: set[str] = set()
    prefixes: list[str] = []
    for item in items:
        if item.endswith("*"):
            stem = item[:-1]
            digits = stem[1:] if stem.startswith("+") else ""
            if not digits.isascii() or not digits.isdigit() or digits[0] == "0":
                raise AllowlistConfigError(f"allowlist: {item!r} is not a valid E.164 prefix.")
            if len(digits) < MIN_PREFIX_DIGITS:
                raise AllowlistConfigError(
                    f"allowlist: prefix {item!r} only pins {len(digits)} digits; at least "
                    f"{MIN_PREFIX_DIGITS} are required so a typo can't open a whole country."
                )
            prefixes.append(stem)
        else:
            if not E164_RE.match(item):
                raise AllowlistConfigError(f"allowlist: {item!r} is not E.164.")
            exact.add(item)
    return Allowlist(AllowlistMode.LIST, frozenset(exact), tuple(prefixes))


def clean_runtime_allowlist(phones: Iterable[Any]) -> frozenset[str]:
    """Keep only well-formed E.164 entries from a runtime (datastore) allowlist."""
    return frozenset(p for p in phones if isinstance(p, str) and E164_RE.match(p))


class DialDecision(StrEnum):
    ALLOWED = "allowed"
    ALLOWED_OPEN = "allowed_open"  # allowed only because the list is "*": log it
    NOT_CONFIGURED = "not_configured"  # nothing allowlisted anywhere (setup problem, 503)
    BLOCKED = "blocked"  # configured, but not this number (403)

    @property
    def allowed(self) -> bool:
        return self in (DialDecision.ALLOWED, DialDecision.ALLOWED_OPEN)


def check_destination(
    phone: str, static: Allowlist, runtime: Iterable[str] | None = None
) -> DialDecision:
    """May we dial ``phone``? Effective allowlist = static ∪ runtime.

    Union, not intersection: either source authorises. Fails closed: pass
    ``runtime=None`` (or empty) when the runtime list could not be read, which can
    only narrow who is dialable, never widen it.
    """
    if static.mode is AllowlistMode.OPEN:
        return DialDecision.ALLOWED_OPEN
    runtime_set = clean_runtime_allowlist(runtime or ())
    if E164_RE.match(phone or "") and (static.matches(phone) or phone in runtime_set):
        return DialDecision.ALLOWED
    if static.is_empty and not runtime_set:
        return DialDecision.NOT_CONFIGURED
    return DialDecision.BLOCKED


# ---------------------------------------------------------------------------
# Calling hours and automatic recall
# ---------------------------------------------------------------------------

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class RecallSettings(BaseModel):
    """Calling hours and automatic-recall policy (one per site)."""

    enabled: bool = True
    #: Time from the missed call to the recall, before the window moves it.
    interval_minutes: int = Field(120, ge=5, le=7 * 24 * 60)
    #: Site-local window calls may be placed in: [start, end).
    window_start: str = "09:00"
    window_end: str = "17:00"
    #: Allowed weekdays, Monday = 0.
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5])
    #: Automatic recalls in a row before giving up (the text thread stays open).
    max_recalls: int = Field(2, ge=0, le=10)

    @field_validator("window_start", "window_end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        v = v.strip()
        if not _HHMM_RE.match(v):
            raise ValueError("use 24-hour HH:MM, e.g. 09:00")
        return v

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        v = sorted(set(v))
        if not v or any(d < 0 or d > 6 for d in v):
            raise ValueError("pick at least one weekday (0=Mon … 6=Sun)")
        return v

    @model_validator(mode="after")
    def _window(self) -> RecallSettings:
        if self.window_start >= self.window_end:
            raise ValueError("the window must end after it starts")
        return self


def _zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"unknown timezone {tz_name!r}") from e


def _aware(when: datetime, name: str) -> datetime:
    if when.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return when


def in_calling_window(when: datetime, cfg: RecallSettings, tz_name: str) -> bool:
    """Is ``when`` inside the site's calling hours? End of the window is exclusive."""
    local = _aware(when, "when").astimezone(_zone(tz_name))
    hhmm = local.strftime("%H:%M")
    return local.weekday() in cfg.days and cfg.window_start <= hhmm < cfg.window_end


def window_text(cfg: RecallSettings, tz_name: str, fmt: timefmt.TimeFormat = "24h") -> str:
    """Nurse-facing: the window in the clinic's time format."""
    days = ", ".join(DAY_NAMES[d] for d in cfg.days)
    start, end = timefmt.hhmm(cfg.window_start, fmt), timefmt.hhmm(cfg.window_end, fmt)
    return f"{days}, {start}–{end} ({tz_name})"


def next_recall_slot(after: datetime, cfg: RecallSettings, tz_name: str) -> datetime | None:
    """The first whole minute at or after ``after`` inside the window, in UTC.

    Seconds round UP to the next minute. None if no slot exists within two weeks
    (impossible with a valid config, but never loop forever).
    """
    tz = _zone(tz_name)
    local = _aware(after, "after").astimezone(tz)
    if local.second or local.microsecond:
        local = (local + timedelta(minutes=1)).replace(second=0, microsecond=0)
    sh, sm = map(int, cfg.window_start.split(":"))
    eh, em = map(int, cfg.window_end.split(":"))
    for i in range(15):
        day = (local + timedelta(days=i)).date()
        if day.weekday() not in cfg.days:
            continue
        start = datetime(day.year, day.month, day.day, sh, sm, tzinfo=tz)
        end = datetime(day.year, day.month, day.day, eh, em, tzinfo=tz)
        cand = max(local, start) if i == 0 else start
        if cand < end:
            return cand.astimezone(UTC)
    return None


# ---------------------------------------------------------------------------
# Call-list entries
# ---------------------------------------------------------------------------

#: Only a completed conversation clears a call-list entry. rna, voicemail (even a
#: report-promoted left_vmail), busy, failed: still pending for the next run.
CLEARING_STATUSES: frozenset[str] = frozenset({"success"})


def _parse_ts(raw: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_when(raw: Any) -> datetime | None:
    """A stored time, or None. A time with no offset is taken as UTC: a hand-edited
    or seeded doc must not fail every scheduler tick on a naive/aware comparison
    (old proxy B-8)."""
    if raw is None or raw == "":
        return None
    return _parse_ts(raw)


#: Rows a scheduled or automatic run never dials. Only a nurse naming the row
#: ("Call now", "Call again") may (old proxy B-2).
NOT_AUTO_DIALLED: frozenset[str] = frozenset({"completed", "blocked", "canceled"})


def is_due(entry: Mapping[str, Any], now: datetime) -> bool:
    """Should the scheduler dial this entry now?

    Only an entry WITH a planned time, once it has passed, and only if it has not
    been attempted since. One automatic attempt per planned time: an unreached
    patient gets the follow-up text, not a redial every minute. No planned time
    means hand-dial only; an unparseable time is never auto-dialled.
    """
    _aware(now, "now")
    raw = entry.get("scheduled_for")
    if not raw:
        return False
    when = _parse_ts(raw)
    if when is None or when > now:
        return False
    last = entry.get("last_attempt_at")
    if last:
        last_dt = _parse_ts(last)
        if last_dt is not None and last_dt >= when:
            return False
    return True


def can_claim_entry(
    current: Mapping[str, Any] | None,
    attempts: int,
    *,
    recover_stranded: bool = False,
    explicit: bool = False,
) -> bool:
    """May this run claim the entry for dial attempt number ``attempts``?

    ``attempts`` is a compare-and-swap token: the dialer is the only writer that
    increments it, so the stored value must be exactly ``attempts - 1`` or the run's
    snapshot is stale (another instance dialled, or finished, this row). This is
    checked for EVERY status; "Call again" on a completed row still passes because
    its snapshot is current. A "calling" row is someone else dialling, unless the
    caller has proven the leg dead and passes ``recover_stranded``.

    A nurse's cancel (or a completion, or a block) can land after a run queued its
    dial and does not touch ``attempts``, so the token cannot see it: the status is
    re-checked here too. Only a nurse naming the row (``explicit``) may dial a
    completed, blocked or cancelled patient; a scheduled run never does (B-2).
    Evaluate inside the same transaction as the write.
    """
    if current is None:
        return False
    try:
        stored = int(current.get("attempts") or 0)
    except (TypeError, ValueError):
        return False
    if stored != attempts - 1:
        return False
    if current.get("list_status") in NOT_AUTO_DIALLED and not explicit:
        return False
    return not (current.get("list_status") == "calling" and not recover_stranded)


def claim_entry(
    current: Mapping[str, Any] | None,
    attempts: int,
    *,
    now: datetime,
    recover_stranded: bool = False,
    explicit: bool = False,
) -> dict[str, Any] | None:
    """The claimed entry doc, or None when the claim is refused (write nothing)."""
    if not can_claim_entry(current, attempts, recover_stranded=recover_stranded, explicit=explicit):
        return None
    now_s = _aware(now, "now").astimezone(UTC).isoformat()
    return {
        **(current or {}),
        "list_status": "calling",
        "attempts": attempts,
        "last_attempt_at": now_s,
        "last_error": "",
        "updated_at": now_s,
    }


def entry_status_after_call(final_status: str | None) -> tuple[str, str]:
    """(list_status, last_call_status) once the runner stops waiting on a call.
    None means the runner timed out."""
    list_status = "completed" if final_status in CLEARING_STATUSES else "pending"
    return list_status, final_status or "timeout"


def reconcile_entry_patch(
    entry: Mapping[str, Any], call_status: str, *, attempt: int | None = None
) -> dict[str, Any] | None:
    """Patch settling an entry when a call ends, or a late report settles it.

    * None for an entry that is already completed (never reopen it).
    * A nurse's cancel and a block stand: a late result, a late report or a
      retried webhook must not put the patient back on the list. The call's
      outcome is still recorded (old proxy B-2 and its review).
    * ``attempt``: the dial attempt the call belongs to. A result for an OLDER
      attempt never resets the row a newer attempt is calling on.
    """
    status = entry.get("list_status")
    if status == "completed":
        return None
    outcome_only = {"last_call_status": call_status}
    if status in ("canceled", "blocked"):
        return outcome_only
    if status == "calling" and attempt is not None:
        try:
            if int(entry.get("attempts") or 0) != attempt:
                return outcome_only
        except (TypeError, ValueError):
            return outcome_only
    list_status, _ = entry_status_after_call(call_status)
    return {"list_status": list_status, "last_call_status": call_status}


# ---------------------------------------------------------------------------
# Stranded "calling" rows
# ---------------------------------------------------------------------------

#: The ``last_error`` a stranded row is re-queued with.
STRANDED_REQUEUED = "call never finished: re-queued"


@dataclass(frozen=True)
class StrandedFix:
    """What :func:`stranded_entry_fix` decided for one "calling" row.

    ``reason``: ``terminal_unapplied`` (its call ended but the result never reached
    the row), ``call_never_finished`` (its call has no final status after the
    in-flight TTL) or ``no_call`` (no call doc for this attempt after the TTL).
    ``patch``: the fields to write. ``call_status``: the call's final status, or
    None when the row is re-queued."""

    reason: str
    patch: dict[str, Any]
    call_status: str | None


def stranded_entry_fix(
    entry: Mapping[str, Any] | None,
    call: Mapping[str, Any] | None,
    *,
    now: datetime,
    in_flight_ttl_s: int,
    terminal_grace_s: int,
    terminal_statuses: Iterable[str],
) -> StrandedFix | None:
    """The fix for a call-list row left in "calling", or None to leave it alone.

    ``call``: the call doc of the row's CURRENT attempt (``entry_attempt`` equal to
    the row's ``attempts``), or None when there is none.

    * Not "calling": None.
    * The call has a terminal status: the row takes what that status implies
      (:func:`entry_status_after_call`), once the call ended at least
      ``terminal_grace_s`` ago (before that, ``/tasks/call-terminal`` is probably
      still on its way).
    * The call has no terminal status: None while it started less than
      ``in_flight_ttl_s`` ago (a live call is never touched); after that the row
      goes back to pending.
    * No call doc: None while the claim is less than ``in_flight_ttl_s`` old (the
      dial task may still be placing the call); after that, back to pending.

    A missing or unparseable time counts as old: a row stuck with no times would
    otherwise stay "calling" for good. Pure: the caller writes the patch inside a
    transaction that re-checks the row is still "calling" on the same attempt."""
    if not entry or entry.get("list_status") != "calling":
        return None
    _aware(now, "now")
    terminal = frozenset(terminal_statuses)
    status = (call or {}).get("call_status")
    if call is not None and status in terminal:
        ended = parse_when(call.get("ended_at") or call.get("updated_at"))
        if ended is not None and now - ended < timedelta(seconds=terminal_grace_s):
            return None
        list_status, last = entry_status_after_call(status)
        return StrandedFix(
            "terminal_unapplied", {"list_status": list_status, "last_call_status": last}, status
        )
    if call is not None:
        started = parse_when(call.get("initiated_at")) or parse_when(entry.get("last_attempt_at"))
        reason = "call_never_finished"
    else:
        started = parse_when(entry.get("last_attempt_at") or entry.get("updated_at"))
        reason = "no_call"
    if started is not None and now - started < timedelta(seconds=in_flight_ttl_s):
        return None
    list_status, last = entry_status_after_call(None)
    return StrandedFix(
        reason,
        {"list_status": list_status, "last_call_status": last, "last_error": STRANDED_REQUEUED},
        None,
    )
