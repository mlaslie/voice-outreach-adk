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

``day_first=True`` (a patient or call whose language is not English, docs/
LANGUAGES-PLAN.md §3.3) reads the numeric forms day first: ``05/03/1950`` is
5 March. ISO and month names are unaffected. :func:`ambiguous_numeric` tells a
caller when the order matters (both numbers 12 or less, and different), so it
can ask for the month by name rather than guess.
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


def parse_dob(text: str, today: date | None = None, *, day_first: bool = False) -> date | None:
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

    def _numeric(y: int, a: int, b: int) -> date | None:
        # a/b are the first and second numbers: month/day, or day/month.
        return _build(y, b, a) if day_first else _build(y, a, b)

    # MM/DD/YYYY, M-D-YY, etc. (DD/MM/YYYY when day_first)
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", text)
    if m:
        return _numeric(int(m[3]), int(m[1]), int(m[2]))

    # MMDDYYYY / MMDDYY, digits only
    digits = re.fullmatch(r"(\d{8})|(\d{6})", re.sub(r"\s+", "", text))
    if digits:
        raw = digits[0]
        return _numeric(int(raw[4:]), int(raw[:2]), int(raw[2:4]))

    # A numeric date inside a sentence -- "it's 3/15/1950", "3 15 1950" --
    # which is how a voice transcript arrives. Four-digit year required here:
    # with free text around it, a shorter form is too easy to find by accident.
    m = re.search(r"(?<![\d/.-])(\d{4})-(\d{1,2})-(\d{1,2})(?![\d/-])", text)
    if m:
        return _build(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"(?<![\d/.-])(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{4})(?![\d/-])", text)
    if m:
        return _numeric(int(m[3]), int(m[1]), int(m[2]))

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


_NUMERIC_FORMS = (
    re.compile(r"^\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\s*$"),
    re.compile(r"^\s*(\d{2})(\d{2})(\d{4}|\d{2})\s*$"),
    re.compile(r"(?<![\d/.-])(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{4})(?![\d/-])"),
)


def ambiguous_numeric(text: str) -> bool:
    """A numeric date whose order is a guess: both of its first two numbers are
    12 or less and differ ("05/03/1950" is 3 May in the US and 5 March in most
    other countries). ISO dates and month names are never ambiguous."""
    t = (text or "").strip().lower()
    if re.search(r"\d{4}-\d{1,2}-\d{1,2}", t) or any(
        w in _MONTHS for w in re.findall(r"[a-z]+", t)
    ):
        return False
    for rx in _NUMERIC_FORMS:
        m = rx.search(re.sub(r"\s+", "", t) if rx is _NUMERIC_FORMS[1] else t)
        if m:
            a, b = int(m[1]), int(m[2])
            return a != b and 1 <= a <= 12 and 1 <= b <= 12
    return False


# --- Spoken numbers in a TRANSLATED date ---------------------------------------
#
# A date said in words in another language comes back from the translator in
# English words: "cinco de marzo de mil novecientos cincuenta" -> "March fifth,
# nineteen fifty". Only machine-translated text goes through this (an English
# caller's words are parsed exactly as before).

_UNITS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "first": 1, "second": 2, "third": 3, "fourth": 4,
    "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9,
}  # fmt: skip
_TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19,
}  # fmt: skip
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "twentieth": 20, "thirtieth": 30,
}  # fmt: skip


def _small(tokens: list[str], i: int) -> tuple[int, int] | None:
    """(value 1..99, tokens used) at ``tokens[i]``."""
    t = tokens[i] if i < len(tokens) else ""
    if t in _TEENS:
        return _TEENS[t], 1
    if t in _UNITS:
        return _UNITS[t], 1
    if t in _TENS:
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if nxt in _UNITS and not t.endswith("ieth"):
            return _TENS[t] + _UNITS[nxt], 2
        return _TENS[t], 1
    return None


def spoken_numbers_to_digits(text: str) -> str:
    """English number words in a date as digits: "March fifth, nineteen fifty"
    -> "March 5 1950"; "the twenty-first of June, two thousand three" -> "21
    June 2003". Words that are not numbers are kept."""
    tokens = [t for t in re.split(r"[\s,\-]+", (text or "").lower()) if t]
    out: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        # "two thousand [and] three" / "two thousand thirteen"
        if t in ("two", "one") and i + 1 < len(tokens) and tokens[i + 1] == "thousand":
            base = 2000 if t == "two" else 1000
            j = i + 2
            if j < len(tokens) and tokens[j] == "and":
                j += 1
            rest = _small(tokens, j)
            if rest:
                out.append(str(base + rest[0]))
                i = j + rest[1]
            else:
                out.append(str(base))
                i = j
            continue
        # "nineteen fifty" / "nineteen oh five" / "nineteen hundred"
        if t in ("eighteen", "nineteen", "twenty") and i + 1 < len(tokens):
            century = {"eighteen": 1800, "nineteen": 1900, "twenty": 2000}[t]
            j = i + 1
            if tokens[j] in ("oh", "o", "zero") and j + 1 < len(tokens) and tokens[j + 1] in _UNITS:
                out.append(str(century + _UNITS[tokens[j + 1]]))
                i = j + 2
                continue
            if tokens[j] == "hundred":
                rest = _small(tokens, j + 1 + (tokens[j + 1 : j + 2] == ["and"]))
                k = j + 1 + (tokens[j + 1 : j + 2] == ["and"])
                if rest:
                    out.append(str(century + rest[0]))
                    i = k + rest[1]
                else:
                    out.append(str(century))
                    i = j + 1
                continue
            rest = _small(tokens, j)
            if rest and rest[0] >= 10 and t != "twenty":
                out.append(str(century + rest[0]))
                i = j + rest[1]
                continue
        small = _small(tokens, i)
        if small:
            out.append(str(small[0]))
            i += small[1]
            continue
        out.append(t)
        i += 1
    return " ".join(out)


def parse_translated(
    original: str, english: str | None, today: date | None = None, *, day_first: bool = False
) -> date | None:
    """A birthdate from a reply in another language: the original first (digits
    need no translation), then its English translation, then that English with
    its number words as digits."""
    heard = parse_dob(original, today, day_first=day_first)
    if heard is not None or not english:
        return heard
    return parse_dob(english, today, day_first=day_first) or parse_dob(
        spoken_numbers_to_digits(english), today, day_first=day_first
    )


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


def check_dob(
    spoken: str, expected_iso: str, today: date | None = None, *, day_first: bool = False
) -> DobCheck:
    """Parse ``spoken`` and compare it with the ISO date on file.

    UNPARSEABLE costs the caller no attempt; MISMATCH does. A missing or
    malformed ``expected_iso`` can never MATCH.
    """
    heard = parse_dob(spoken, today, day_first=day_first)
    if heard is None:
        return DobCheck(DobResult.UNPARSEABLE, None)
    return DobCheck(DobResult.MATCH if matches(heard, expected_iso) else DobResult.MISMATCH, heard)


def speakable(d: date) -> str:
    """'March 15, 1950'."""
    return f"{d.strftime('%B')} {d.day}, {d.year}"
