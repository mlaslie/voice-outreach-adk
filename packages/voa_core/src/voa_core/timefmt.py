"""How a clock time is written, by audience.

* Patients (voice agent, SMS, anything read back to them) always get 12-hour
  times with AM/PM: "2:30 PM". That never changes with any setting.
* Nurses (dashboard, the Gemini Enterprise agent's cards, API messages about
  calling hours) get the clinic's ``time_format``: "12h" or "24h". The default is
  ``site.nurse_time_format``; the dashboard's Settings can change it at runtime
  (``call_settings/display``).
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Literal, get_args

TimeFormat = Literal["12h", "24h"]
TIME_FORMATS: tuple[str, ...] = get_args(TimeFormat)


def clock(t: datetime | time, fmt: TimeFormat) -> str:
    """'14:05' (24h, zero-padded) or '2:05 PM' (12h)."""
    if fmt == "24h":
        return f"{t.hour:02d}:{t.minute:02d}"
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def hhmm(value: str, fmt: TimeFormat) -> str:
    """A stored 'HH:MM' (e.g. a calling-window bound) in ``fmt``; garbage passes through."""
    try:
        return clock(time.fromisoformat(value), fmt)
    except (TypeError, ValueError):
        return value


def patient_clock(t: datetime | time) -> str:
    """The only way a time is written for a patient: 12-hour with AM/PM."""
    return clock(t, "12h")


def coerce(value: object, default: TimeFormat) -> TimeFormat:
    return value if value in TIME_FORMATS else default  # type: ignore[return-value]
