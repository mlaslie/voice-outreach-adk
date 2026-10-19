"""Turn "tomorrow at 3" into a callback time.

Deliberately deterministic. Every resolved time is read back to the patient
in words before it is booked ("You're set for Thu Aug 13, 3:00 PM"), so a
misreading is visible and correctable rather than silent. Anything this
module cannot read returns ``None`` and the patient is asked once more --
**a time is never guessed**, the same rule the voice agent has about never
inventing a pain score.

The clinic and its patients share one timezone, so everything here works in
the :class:`SchedulingPolicy` timezone. No global configuration is read: the
caller passes a policy, and the defaults mirror the old service's defaults.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from . import timefmt


@dataclass(frozen=True)
class SchedulingPolicy:
    """When we may call, and how far ahead a callback may be booked."""

    tz: str = "America/New_York"
    quiet_start: int = 8  # earliest hour we will call
    quiet_end: int = 20  # last bookable START hour (8:00 PM yes, 8:01 PM no)
    max_days: int = 14


DEFAULT_SCHEDULING = SchedulingPolicy()

_WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

# Named parts of the day. Values sit inside the calling window on purpose.
_DAYPARTS = {
    "morning": (9, 0),
    "afternoon": (14, 0),
    "evening": (18, 0),
    "tonight": (18, 0),
    "noon": (12, 0),
    "midday": (12, 0),
    "lunch": (12, 0),
    "lunchtime": (12, 0),
}

# Vague phrases patients actually use, most specific first. ``"END"`` and
# ``"START"`` mean the last / first bookable start time of the policy, so "late
# tomorrow night after the kids sleep" lands at the end of the window instead of
# falling through to the 10 AM default (a gap found during the port).
_PHRASES: tuple[tuple[str, tuple[int, int] | str], ...] = (
    (
        r"\blate (?:tomorrow |at )?night\b|\btomorrow night late\b|\bbedtime\b"
        r"|\blate (?:in the )?evening\b"
        r"|\bafter (?:the )?(?:kids?|children|baby) "
        r"(?:go(?:es)? to (?:bed|sleep)|(?:are|is) asleep|sleeps?)\b",
        "END",
    ),
    (r"\b(?:early (?:in the )?morning|first thing)\b", "START"),
    (r"\bafter (?:dinner|supper)\b", (19, 0)),
    (r"\bafter work\b|\bend of (?:the )?(?:work ?)?day\b", (17, 30)),
    (r"\bafter school\b", (15, 30)),
    (r"\blate afternoon\b", (16, 0)),
    (r"\bearly afternoon\b", (13, 0)),
    (r"\blate morning\b", (11, 0)),
    (r"\bearly evening\b", (17, 0)),
    (r"\b(?:tomorrow )?night\b", (19, 0)),
)

# Day parts that put a bare hour in the afternoon/evening ("evening 8" = 8 PM).
_PM_WORDS = re.compile(r"\b(afternoon|evening|tonight|night|dinner|supper)\b")

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty five": 45,
    "couple of": 2,
    "few": 3,
}

# A day named with no time attached. The patient confirms the result, so a
# sensible mid-morning default is better than a second round trip.
_DEFAULT_HOUR = 10


@dataclass(frozen=True)
class Resolved:
    """A booking time, and whether we had to move what the patient asked for."""

    when: datetime.datetime  # timezone-aware, clinic timezone
    adjusted: bool = False  # True when clamped into the calling window


# A clock or calendar word that makes "a time" the obvious reading.
# Used to tell "1 pm" (a time) from "1 please" (menu option one) -- a leading
# digit alone cannot distinguish them.
_EXPLICIT_TIME_RE = re.compile(
    r"\b(a\.?m\.?|p\.?m\.?|o'?clock|noon|midday|morning|afternoon|"
    r"evening|tonight|today|tomorrow|" + "|".join(_WEEKDAYS) + "|" + "|".join(_MONTHS) + r")\b"
    r"|\d{1,2}:\d{2}|\b\d{1,2}/\d{1,2}\b|\bin \d+ (?:min|minute|hour|hr|day)"
    # A meridiem glued to its digit ("3pm", "10a.m."). The word-bounded
    # alternative above cannot see it -- there is no \b between "3" and "p" --
    # so the old service read "3pm" as menu option 3 and "1pm" as "call now".
    r"|\d\s*(?:a\.?m|p\.?m)\b\.?",
    re.IGNORECASE,
)


def has_explicit_time_signal(text: str) -> bool:
    return bool(_EXPLICIT_TIME_RE.search(text or ""))


def is_bare_number_without_time_signal(text: str) -> bool:
    """A digit with nothing that makes it a clock: "2", "2 thanks", "at 3".

    Review finding C-7. ``_parse_clock`` reads a bare 1-7 as an afternoon
    hour, which is right for "tomorrow at 3" but wrong for a menu digit that
    arrives in the time-asking state -- a carrier redelivery of the "2" that
    moved the patient there booked 2:00 PM and confirmed it to them. Where
    this returns True the caller must ask again rather than book.
    """
    t = text or ""
    return bool(re.search(r"\d", t)) and not has_explicit_time_signal(t)


def clinic_tz(policy: SchedulingPolicy = DEFAULT_SCHEDULING) -> ZoneInfo:
    return ZoneInfo(policy.tz)


def now_local(policy: SchedulingPolicy = DEFAULT_SCHEDULING) -> datetime.datetime:
    return datetime.datetime.now(clinic_tz(policy))


def _norm(text: str) -> str:
    text = (text or "").lower().strip()
    text = text.replace("@", " at ")
    text = re.sub(r"[^\w:/\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_clock(
    text: str, policy: SchedulingPolicy = DEFAULT_SCHEDULING
) -> tuple[int, int] | None:
    """(hour, minute) in 24h, or None.

    An explicit number wins over a day-part word: "tomorrow evening at 7" is 7 PM,
    not the 6 PM "evening" default (a bug found during the port). A bare hour with
    no am/pm is read as a *callback-hours* time: 1-7 means afternoon, 8-11 morning,
    unless a PM word ("evening 8") says otherwise.
    """
    numeric = _parse_numeric_clock(text, pm_context=bool(_PM_WORDS.search(text)))
    if numeric is not None:
        return numeric
    for pattern, value in _PHRASES:
        if re.search(pattern, text):
            if value == "END":
                return policy.quiet_end, 0
            if value == "START":
                return policy.quiet_start, 0
            return value
    for word, (h, m) in _DAYPARTS.items():
        if re.search(rf"\b{word}\b", text):
            return h, m
    return None


def _parse_numeric_clock(text: str, *, pm_context: bool = False) -> tuple[int, int] | None:
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?|am|pm)?\b", text)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    meridiem = (m.group(3) or "").replace(".", "")

    if minute > 59:
        return None
    if meridiem.startswith("p"):
        if hour < 1 or hour > 12:
            return None
        hour = hour if hour == 12 else hour + 12
    elif meridiem.startswith("a"):
        if hour < 1 or hour > 12:
            return None
        hour = 0 if hour == 12 else hour
    else:
        if hour > 23:
            return None
        # Bare number: only treat as a clock time in a plausible range.
        if pm_context and 1 <= hour <= 11:
            hour += 12
        elif 1 <= hour <= 7:
            hour += 12
        elif hour == 12:
            hour = 12
        elif hour > 20:
            return None
    return hour, minute


def _parse_relative(text: str, now: datetime.datetime) -> datetime.datetime | None:
    words = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
    m = re.search(
        rf"\bin (?:about |around )?(?:an? )?(?:(\d{{1,3}})|({words}))?\s*"
        r"(minute|min|hour|hr|day)s?\b",
        text,
    )
    if not m:
        return None
    qty = int(m.group(1)) if m.group(1) else _NUMBER_WORDS.get(m.group(2) or "", 1)
    unit = m.group(3)
    if unit in ("minute", "min"):
        delta = datetime.timedelta(minutes=qty)
    elif unit in ("hour", "hr"):
        delta = datetime.timedelta(hours=qty)
    else:
        delta = datetime.timedelta(days=qty)
    return now + delta


def _parse_day(text: str, now: datetime.datetime) -> tuple[datetime.date | None, str]:
    """(date, text with the date removed).

    Removing what it consumed matters: "8/14 at 11am" and "aug 15 2pm" both
    contain digits that belong to the *date*, and a clock parser let loose
    on the whole string reads "8" or "15" as the hour and books the wrong
    time. The date is taken out before the clock is read.
    """

    def cut(m: re.Match) -> str:
        return re.sub(r"\s+", " ", text[: m.start()] + " " + text[m.end() :]).strip()

    m = re.search(r"\b(?:the )?day after tomorrow\b", text)
    if m:
        return now.date() + datetime.timedelta(days=2), cut(m)
    m = re.search(r"\btomorrow\b", text)
    if m:
        # Keep "tomorrow night" etc. readable by the clock parser: only the
        # day word is consumed.
        return now.date() + datetime.timedelta(days=1), cut(m)
    m = re.search(r"\b(today|tonight|this (?:morning|afternoon|evening))\b", text)
    if m:
        # "tonight"/"this evening" also name a time; leave them in place for
        # the clock parser.
        keep = m.group(1) in ("tonight",) or m.group(1).startswith("this ")
        return now.date(), (text if keep else cut(m))

    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = now.year
        if m.group(3):
            year = int(m.group(3))
            if year < 100:
                year += 2000
        try:
            return datetime.date(year, month, day), cut(m)
        except ValueError:
            return None, text

    m = re.search(rf"\b({'|'.join(_MONTHS)})\w*\s+(\d{{1,2}})\b", text)
    if m:
        try:
            day = datetime.date(now.year, _MONTHS[m.group(1)], int(m.group(2)))
        except ValueError:
            return None, text
        if day < now.date():
            # "Jan 5" said in December means next January. Rolling forward is
            # safe because the booking horizon still refuses anything absurd --
            # "Aug 1" said in September rolls to next year and is then
            # rejected, which is the same answer as before.
            try:
                day = day.replace(year=now.year + 1)
            except ValueError:
                return None, text
        return day, cut(m)

    m = re.search(rf"\b(next\s+)?({'|'.join(_WEEKDAYS)})\b", text)
    if m:
        target = _WEEKDAYS[m.group(2)]
        if m.group(1):
            # "next Thursday" = that weekday in the *following* calendar week.
            # English is genuinely ambiguous here; the confirmation read-back
            # is what protects the patient, but the two phrasings should at
            # least not silently collide.
            next_week_start = now.date() + datetime.timedelta(days=7 - now.weekday())
            return next_week_start + datetime.timedelta(days=target), cut(m)
        # A bare weekday means the next one still to come; "Monday" said on a
        # Monday means a week today, not five minutes ago.
        ahead = (target - now.weekday()) % 7 or 7
        return now.date() + datetime.timedelta(days=ahead), cut(m)

    return None, text


def parse_when(
    text: str,
    now: datetime.datetime | None = None,
    policy: SchedulingPolicy = DEFAULT_SCHEDULING,
) -> datetime.datetime | None:
    """Best-effort local datetime from a patient's own words. None if unclear."""
    now = now or now_local(policy)
    t = _norm(text)
    if not t:
        return None

    relative = _parse_relative(t, now)
    if relative is not None:
        return relative

    day, remainder = _parse_day(t, now)
    clock = _parse_clock(remainder, policy)

    if day is None and clock is None:
        return None

    if clock is None:
        hour, minute = _DEFAULT_HOUR, 0
    else:
        hour, minute = clock

    if day is None:
        # A time with no day: today if it is still ahead of us, else tomorrow.
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += datetime.timedelta(days=1)
        return candidate

    return datetime.datetime.combine(day, datetime.time(hour, minute), tzinfo=now.tzinfo)


def clamp(
    when: datetime.datetime,
    now: datetime.datetime | None = None,
    policy: SchedulingPolicy = DEFAULT_SCHEDULING,
) -> Resolved | None:
    """Move ``when`` into the calling window. None if it cannot be honoured.

    Rules: never in the past, never outside quiet_start..quiet_end local,
    never more than max_days ahead. A clamped result is flagged so the
    confirmation message can say we moved it.
    """
    now = now or now_local(policy)
    adjusted = False

    if when <= now:
        # Only a same-day slot that has already passed is recoverable; an
        # explicitly past date is a misreading, not a preference.
        if when.date() < now.date():
            return None
        when = now + datetime.timedelta(minutes=5)
        adjusted = True

    if when.hour < policy.quiet_start:
        when = when.replace(hour=policy.quiet_start, minute=0, second=0, microsecond=0)
        adjusted = True
    elif when.hour > policy.quiet_end or (when.hour == policy.quiet_end and when.minute > 0):
        # quiet_end is the last bookable *start* time, so 8:00 PM is allowed
        # and 8:01 PM is not. Treating it as exclusive silently bounced every
        # "tomorrow 8pm" to 8 the next morning.
        when = (when + datetime.timedelta(days=1)).replace(
            hour=policy.quiet_start, minute=0, second=0, microsecond=0
        )
        adjusted = True

    if when <= now:
        # Clamping to the window can land in the past (e.g. "6am today"
        # clamped to 8am when it is already 9am). Roll to the next day.
        when = (when + datetime.timedelta(days=1)).replace(
            hour=policy.quiet_start, minute=0, second=0, microsecond=0
        )
        adjusted = True

    if when - now > datetime.timedelta(days=policy.max_days):
        return None

    return Resolved(when=when.replace(second=0, microsecond=0), adjusted=adjusted)


def resolve(
    text: str,
    now: datetime.datetime | None = None,
    policy: SchedulingPolicy = DEFAULT_SCHEDULING,
) -> Resolved | None:
    """parse_when + clamp. None means "ask the patient again"."""
    now = now or now_local(policy)
    when = parse_when(text, now, policy)
    if when is None:
        return None
    return clamp(when, now, policy)


def next_send_window(
    when: datetime.datetime, policy: SchedulingPolicy = DEFAULT_SCHEDULING
) -> datetime.datetime:
    """The first moment at or after ``when`` that we may contact someone.

    Bookings were clamped into the calling window from the start; the
    unsolicited invite was not. A call that went unanswered at 23:40 would
    have produced a text just after midnight.
    """
    if when.hour < policy.quiet_start:
        return when.replace(hour=policy.quiet_start, minute=0, second=0, microsecond=0)
    if when.hour > policy.quiet_end or (when.hour == policy.quiet_end and when.minute > 0):
        return (when + datetime.timedelta(days=1)).replace(
            hour=policy.quiet_start, minute=0, second=0, microsecond=0
        )
    return when


def humanize(when: datetime.datetime, now: datetime.datetime | None = None) -> str:
    """'Thu Aug 13, 3:00 PM' -- unambiguous, and short enough for SMS.

    The year is included only when it differs from ``now``'s year (the wall
    clock in ``when``'s timezone if ``now`` is omitted): "Jan 5" booked in
    December is otherwise ambiguous, and the read-back is the safeguard.
    """
    current_year = (now or datetime.datetime.now(when.tzinfo)).year
    year = f" {when.year}" if when.year != current_year else ""
    # Patient-facing: always 12-hour, whatever the clinic's nurse time format.
    return f"{when:%a %b} {when.day}{year}, {timefmt.patient_clock(when)}"
