"""Date-of-birth parsing and comparison for spoken or typed answers.

Phase-0 version: enough for the voice spike. The full port of the SMS service's
``dobcheck.py`` (two-digit years, day/month swap, compressed forms) replaces this
in phase 1, keeping the same public functions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

_MONTHS = {
    name: i
    for i, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}
_ORDINAL = re.compile(r"(\d+)(st|nd|rd|th)\b")


class DobResult(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNPARSEABLE = "unparseable"  # no full date found (missing year, gibberish)


@dataclass(frozen=True)
class DobCheck:
    result: DobResult
    heard: date | None


def _valid(y: int, m: int, d: int) -> date | None:
    try:
        parsed = date(y, m, d)
    except ValueError:
        return None
    return parsed if 1900 <= y <= date.today().year else None


def parse_dob(text: str) -> date | None:
    t = _ORDINAL.sub(r"\1", text.lower())
    t = re.sub(r"[,.]", " ", t)

    if m := re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t):  # ISO
        return _valid(int(m[1]), int(m[2]), int(m[3]))
    if m := re.search(r"\b(\d{1,2})[/\- ](\d{1,2})[/\- ](\d{4})\b", t):  # US M/D/YYYY
        return _valid(int(m[3]), int(m[1]), int(m[2]))

    month = next((num for name, num in _MONTHS.items() if re.search(rf"\b{name}\b", t)), None)
    if month:
        year = re.search(r"\b(19\d{2}|20\d{2})\b", t)
        rest = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", t)
        day = re.search(r"\b(\d{1,2})\b", rest)
        if year and day:
            return _valid(int(year[1]), month, int(day[1]))
    return None


def check_dob(spoken: str, expected_iso: str) -> DobCheck:
    heard = parse_dob(spoken)
    if heard is None:
        return DobCheck(DobResult.UNPARSEABLE, None)
    return DobCheck(
        DobResult.MATCH if heard.isoformat() == expected_iso else DobResult.MISMATCH, heard
    )


def speakable(d: date) -> str:
    return f"{d.strftime('%B')} {d.day}, {d.year}"
