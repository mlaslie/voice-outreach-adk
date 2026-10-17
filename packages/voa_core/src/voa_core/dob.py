"""Date-of-birth parsing and verification, for spoken (voice) and typed (SMS) answers.

Separate from :mod:`voa_core.timeparse` on purpose. That module resolves
*appointment* times -- "next Tuesday", "tomorrow at 3" -- against a clock and a
business window. A birthdate is none of those things, and letting the two
share code is how "3" becomes both a pain score and three o'clock.

The important distinction here is **unparseable vs. mismatch**, and it is not
cosmetic. Two wrong dates means someone is holding this handset who does not
know the patient's birthday. Two replies of "1" means someone changed their
mind about answering by text. Treating the second as the first turns a
patient's spouse into an identity-fraud case. :func:`parse_dob` therefore
returns ``None`` for anything that is not a full date (including a birthday
with the year left off), and callers must not count that as a failed attempt.

Accepted forms: ``1950-03-15``; ``3/15/1950``, ``03-15-50``, ``3.15.1950``
(month first, US order -- day-first input such as ``15/03/1950`` is *not*
swapped, it is unparseable); compressed ``03151950`` / ``031550``; and month
names in either order (``March 15th, 1950``, ``15 March 1950``). Two-digit
years expand to the most recent plausible birth year.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

# Oldest plausible living patient. Used to expand a two-digit year, which
# handsets send constantly ("070468"), and to reject absurd dates.
_MAX_AGE = 120

# Digit runs longer than this are never part of a date. Bounding them keeps
# int() away from multi-thousand-digit strings, which raise ValueError past
# Python's int-string limit (review finding S-10).
_MAX_DIGIT_RUN = 4

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


class DobResult(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNPARSEABLE = "unparseable"  # no full date found (missing year, gibberish)


@dataclass(frozen=True)
class DobCheck:
    result: DobResult
    heard: date | None


def _expand_year(year: int, today: date) -> int:
    """Two-digit year -> the most recent plausible birth year."""
    if year >= 100:
        return year
    candidate = (today.year // 100) * 100 + year
    if candidate > today.year:
        candidate -= 100
    if today.year - candidate > _MAX_AGE:
        candidate += 100
    return candidate


def parse_dob(text: str, today: date | None = None) -> date | None:
    """Read a birthdate, or None if the text is not a full date at all.

    None means "this was not a date" -- the caller must NOT count it as a
    failed identity attempt. ``today`` defaults to the local date and only
    matters for two-digit years and the future/too-old checks.
    """
    today = today or datetime.date.today()
    text = (text or "").strip().lower()
    if not text:
        return None

    def _build(y: int, m: int, d: int) -> date | None:
        try:
            value = datetime.date(_expand_year(y, today), m, d)
        except ValueError:
            return None
        # A future date, or one older than any living person, is not a
        # birthdate -- read it as unparseable rather than as a wrong answer.
        if value > today or today.year - value.year > _MAX_AGE:
            return None
        return value

    # YYYY-MM-DD
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if m:
        return _build(int(m[1]), int(m[2]), int(m[3]))

    # MM/DD/YYYY, M-D-YY, etc.
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", text)
    if m:
        return _build(int(m[3]), int(m[1]), int(m[2]))

    # MMDDYYYY / MMDDYY, digits only
    digits = re.fullmatch(r"(\d{8})|(\d{6})", re.sub(r"\s+", "", text))
    if digits:
        raw = digits[0]
        return _build(int(raw[4:]), int(raw[:2]), int(raw[2:4]))

    # A numeric date inside a sentence -- "it's 3/15/1950", "3 15 1950" --
    # which is how a voice transcript arrives. Four-digit year required here:
    # with free text around it, a shorter form is too easy to find by accident.
    m = re.search(r"(?<![\d/.-])(\d{4})-(\d{1,2})-(\d{1,2})(?![\d/-])", text)
    if m:
        return _build(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"(?<![\d/.-])(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{4})(?![\d/-])", text)
    if m:
        return _build(int(m[3]), int(m[1]), int(m[2]))

    # "March 15 1950" / "15 March 1950" / "March 15th, 1950"
    words = re.findall(r"[a-z]+|\d+", text)
    month = day = year = None
    for tok in words:
        if tok in _MONTHS and month is None:
            month = _MONTHS[tok]
        elif tok.isdigit():
            if len(tok) > _MAX_DIGIT_RUN:
                continue
            n = int(tok)
            if len(tok) == 4 and year is None:
                year = n
            elif day is None and 1 <= n <= 31:
                day = n
            elif year is None:
                year = n
    if month and day and year is not None:
        return _build(year, month, day)

    return None


def matches(supplied: date, on_file: str | None) -> bool:
    """Whether a parsed date equals the DOB we hold.

    ``on_file`` is the ISO ``YYYY-MM-DD`` string from the patient context. A
    missing or malformed stored value returns False -- we cannot verify, so we
    must not claim to have.
    """
    stored_text = (on_file or "").strip()
    if not stored_text:
        return False
    try:
        stored = datetime.date.fromisoformat(stored_text[:10])
    except ValueError:
        return False
    return supplied == stored


def check_dob(spoken: str, expected_iso: str, today: date | None = None) -> DobCheck:
    """Parse ``spoken`` and compare it with the ISO date on file.

    UNPARSEABLE costs the caller no attempt; MISMATCH does. A missing or
    malformed ``expected_iso`` can never MATCH.
    """
    heard = parse_dob(spoken, today)
    if heard is None:
        return DobCheck(DobResult.UNPARSEABLE, None)
    return DobCheck(DobResult.MATCH if matches(heard, expected_iso) else DobResult.MISMATCH, heard)


def speakable(d: date) -> str:
    """'March 15, 1950'."""
    return f"{d.strftime('%B')} {d.day}, {d.year}"
