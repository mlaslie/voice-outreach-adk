"""Answer parsers: what a handset (or a voice model) actually sends, typed.

Parsing is by question TYPE (docs/USE-CASES-PLAN.md): every question of a type
gets the same tested parser on voice and SMS. A question may instead name a
parser (``parser: blood_sugar``) for a field whose wording needs reviewed code;
named parsers live here, in ``NAMED_PARSERS``, and adding one is a code change.

Pure functions, no I/O. The post-op parsers were moved here unchanged from
``voa_core.survey`` (which re-exports them).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .campaign import Question

_YES = {
    "y",
    "yes",
    "yeah",
    "yep",
    "yup",
    "ya",
    "sure",
    "correct",
    "true",
    "affirmative",
    "i do",
    "i am",
    "some",
    "a little",
    "a bit",
    "a little bit",
    "kind of",
    "kinda",
    "slightly",
    "mild",
    "mildly",
    "somewhat",
}
_NO = {
    "n",
    "no",
    "nope",
    "nah",
    "none",
    "not really",
    "no pain",
    "negative",
    "false",
    "nothing",
    "no thanks",
    "all good",
    "im fine",
    "i'm fine",
}

# "a little" is a YES. The voice script is explicit: any hint of pain
# ("just a little bit", "not bad", "mild") is "yes". Treating a hedged yes as
# a no is the single easiest way to under-report pain in this whole feature.

_NUMBER_WORDS = {
    "zero": 0,
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
}

_TREND = {
    "better": "better",
    "b": "better",
    "improving": "better",
    "improved": "better",
    "same": "same",
    "s": "same",
    "unchanged": "same",
    "no change": "same",
    "worse": "worse",
    "w": "worse",
    "worsening": "worse",
    "getting worse": "worse",
}

# "range" / "ok" / "good" alone are NOT enough: "out of range" and "ok a bit
# high" both contain one, and reading either as normal under-triages a
# diabetic who just told us the opposite. High/low are checked first, and
# "out of range" is matched explicitly.
_SUGAR_IN_RANGE = {
    "in range",
    "in my usual range",
    "normal",
    "usual",
    "fine",
    "ok",
    "okay",
    "good",
    "steady",
    "stable",
}
_SUGAR_OUT = {"out of range", "out of my range", "outside my range", "not in range"}
_SUGAR_HIGH = {"high", "up", "elevated", "higher", "too high"}
_SUGAR_LOW = {"low", "down", "lower", "too low"}


def _norm(body: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (body or "").lower()).strip()


def parse_yes_no(body: str) -> str | None:
    """ "yes"/"no", or None if it cannot be read as either."""
    t = _norm(body)
    if not t:
        return None
    if t in _YES:
        return "yes"
    if t in _NO:
        return "no"
    # Fallback for a sentence rather than a bare word. Both sides are scanned
    # and an answer containing BOTH signals is treated as unclear rather than
    # resolved by scan order -- "no fever but some pain" is a real thing to
    # send, and a first-match-wins loop reads it as whichever list ran first.
    # On a clinical form, asking again beats guessing.
    negated = _negated_words(body)

    def _hits(vocab: set[str]) -> list[str]:
        return [
            p
            for p in vocab
            if p not in _YES_EXACT_ONLY
            and re.search(rf"(?<![a-z]){re.escape(p)}(?![a-z])", t)
            and p.split()[0] not in negated
        ]

    no_hits, yes_hits = _hits(_NO), _hits(_YES)
    if no_hits and yes_hits:
        return None
    if no_hits:
        return "no"
    if yes_hits:
        return "yes"
    return None


# "3/10" and "3 out of 10" are how people write a pain score, and the scale
# half must be removed BEFORE looking for numbers. Without this, normalising
# "0/10" to "0 10" and taking the maximum reports every routine score as a 10
# and escalates the entire cohort.
_OUT_OF_TEN = re.compile(r"\s*(?:/|\bout of\b|\bof\b)\s*10\b")

# Answers to "how bad is it" that mean zero. Exact matches only.
_NO_PAIN = frozenset({"no", "none", "no pain", "zero", "nothing", "not any", "n"})


def parse_scale(body: str, lo: int = 0, hi: int = 10) -> int | None:
    """A score on a ``lo``-``hi`` scale (default: a 0-10 pain score).

    Handles what people actually send: ``8``, ``eight``, ``3/10``,
    ``about a 4``, ``4-5``, ``4 or 5``, ``none``. A range takes the HIGHER
    end, matching the voice script -- under-reporting pain is the more
    dangerous error, and over-reporting only costs a phone call.
    """
    raw = (body or "").lower()
    out_of = _OUT_OF_TEN if hi == 10 else re.compile(rf"\s*(?:/|\bout of\b|\bof\b)\s*{hi}\b")
    t = _norm(out_of.sub(" ", raw))
    if not t:
        return None
    # "none" / "no pain" is a real answer to "how bad is it", and it means 0.
    # Deliberately an exact whitelist rather than parse_yes_no: "no idea"
    # also reads as a no, and it means the opposite of zero.
    if t in _NO_PAIN and lo <= 0 <= hi:
        return 0
    # Only short digit runs can be a score. Checking the length BEFORE
    # int() matters: int() on a multi-thousand-digit string raises ValueError
    # (Python's int-string limit), and in a webhook that is a 500 the carrier
    # retries in a loop (review finding S-10).
    width = max(2, len(str(hi)))
    nums = [
        int(n) for n in re.findall(r"\d+", t) if len(n.lstrip("0")) <= width and lo <= int(n) <= hi
    ]
    if nums:
        return max(nums)
    for word, val in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", t) and lo <= val <= hi:
            return val
    return None


# "not worse" and "no better" invert what follows them. A plain substring
# scan reads both as the word itself, which is the wrong direction on the one
# field that escalates on its own.
#
# The negation reaches the next few words of the SAME clause: "not getting
# worse" negates "worse" (the first version negated only "getting", and
# "worse" escalated as urgent), while "no pain but getting worse" is two
# clauses and the second one stands. Clauses split on punctuation and "but",
# on the RAW text, because _norm strips the punctuation that marks them.
_NEGATORS = frozenset(
    {
        "not",
        "no",
        "never",
        "without",
        "isn",
        "aren",
        "wasn",
        "hasn",
        "don",
        "doesn",
        "didn",
        "cant",
        "cannot",
    }
)
_NEGATION_REACH = 3
_CLAUSE_SPLIT = re.compile(r"[,;.!?]|\bbut\b|\bhowever\b|\bthough\b")


def _negated_words(body: str) -> set[str]:
    out: set[str] = set()
    for clause in _CLAUSE_SPLIT.split((body or "").lower()):
        toks = _norm(clause).split()
        for i, tok in enumerate(toks):
            if tok in _NEGATORS:
                out.update(toks[i + 1 : i + 1 + _NEGATION_REACH])
    return out


# Entries that only mean "yes" as a WHOLE answer. Inside a sentence they are
# ordinary words: "not sure" and "I am not sure" both scanned as yes, which on
# the pain question fabricated a pain report and three follow-ups.
_YES_EXACT_ONLY = frozenset({"sure", "i am", "i do"})


def parse_trend(body: str) -> str | None:
    t = _norm(body)
    if not t:
        return None
    if t in _TREND:
        return _TREND[t]
    negated = _negated_words(body)
    for phrase in sorted(_TREND, key=len, reverse=True):
        if len(phrase) > 1 and phrase in t:
            if phrase in negated or any(w in negated for w in phrase.split()):
                # "not worse" says what it is NOT. Skip it: "not better,
                # worse" still has an answer in it, and a bare "not worse"
                # falls through to None, i.e. ask again rather than record
                # the opposite of what they said.
                continue
            return _TREND[phrase]
    return None


# A typed meter reading, in mg/dL (review finding S-10: "250" used to be
# unparseable and the question was re-asked). The thresholds are the
# conventional ones -- below 70 is hypoglycaemia, above 180 is above the usual
# target -- and the plausibility bounds are roughly what a home meter can
# display; anything outside them (or a decimal, which is likely mmol/L) is not
# read as a reading at all. Over-triaging is the chosen direction: an abnormal
# number wins over reassuring words ("fine, 250" is high), and abnormal words
# win over an in-range number ("130, a bit high for me" is high).
SUGAR_LOW_MGDL = 70
SUGAR_HIGH_MGDL = 180
_SUGAR_MIN_READING = 20
_SUGAR_MAX_READING = 600
_SUGAR_READING_RE = re.compile(r"(?<![\d.])(\d{2,3})(?![\d.]|\s*%)")


def _sugar_from_reading(body: str) -> str | None:
    """ "high"/"low"/"in_range" from typed numbers, None if there are none.

    Several readings resolve to the most abnormal one ("120 then 250" is
    high). A high AND a low ("65 this morning, 250 at night") returns
    "unclear": that patient needs a person, but recording one direction
    would misstate what they said, so the question is asked again.
    """
    values = {
        "low" if n < SUGAR_LOW_MGDL else "high" if n > SUGAR_HIGH_MGDL else "in_range"
        for n in (int(x) for x in _SUGAR_READING_RE.findall(body or ""))
        if _SUGAR_MIN_READING <= n <= _SUGAR_MAX_READING
    }
    if "high" in values and "low" in values:
        return "unclear"
    for value in ("high", "low", "in_range"):
        if value in values:
            return value
    return None


def _sugar_from_words(body: str) -> tuple[bool, str | None]:
    """(matched, value). matched=False means no sugar vocabulary at all."""
    t = _norm(body)
    if not t:
        return False, None
    negated = _negated_words(body)
    # "Out of range" says abnormal without saying which way. Unclear is the
    # honest answer -- re-asking beats recording "normal".
    for phrase in _SUGAR_OUT:
        if phrase in t:
            return True, None
    # High/low first: "ok a bit high" must not resolve on "ok".
    for vocab, value in (
        (_SUGAR_HIGH, "high"),
        (_SUGAR_LOW, "low"),
        (_SUGAR_IN_RANGE, "in_range"),
    ):
        for phrase in sorted(vocab, key=len, reverse=True):
            if phrase in t:
                if phrase in negated or any(w in negated for w in phrase.split()):
                    return True, None
                return True, value
    return False, None


def parse_sugar(body: str) -> str | None:
    """ "high"/"low"/"in_range", or None to ask again."""
    reading = _sugar_from_reading(body)
    if reading == "unclear":
        return None
    if reading in ("high", "low"):
        return reading
    matched, value = _sugar_from_words(body)
    if matched:
        return value
    return reading  # "in_range" from a number alone, or None


MAX_FREE_TEXT = 500

# How many times to re-ask a field the proxy requires as a typed value before
# giving up and filing a partial. Looping forever strands the patient.
MAX_STRICT_RETRIES = 3


def parse_free_text(body: str) -> str | None:
    t = (body or "").strip()
    return t[:MAX_FREE_TEXT] if t else None


# --- Generic parsers for the other types -----------------------------------------


def parse_choice(body: str, choices: list[str]) -> str | None:
    """One of ``choices``: an exact reply, or a sentence naming exactly one of them
    (a negated one, "not worse", does not count). None when unclear."""
    t = _norm(body)
    if not t or not choices:
        return None
    by_norm = {_norm(c): c for c in choices}
    if t in by_norm:
        return by_norm[t]
    negated = _negated_words(body)
    found: list[str] = []
    for key in sorted(by_norm, key=len, reverse=True):
        if not key:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", t) and not any(
            w in negated for w in key.split()
        ):
            if by_norm[key] not in found:
                found.append(by_norm[key])
    return found[0] if len(found) == 1 else None


_NUMBER_RE = re.compile(r"(?<![\d.])-?\d{1,9}(?:\.\d{1,6})?(?![\d.])")


def parse_number(body: str, lo: float | None = None, hi: float | None = None) -> int | float | None:
    """Exactly one number in the reply (digits or zero..ten), inside [lo, hi]."""
    t = (body or "").strip().lower()
    if not t:
        return None
    found = _NUMBER_RE.findall(t)
    values: list[int | float] = []
    if found:
        values = [float(n) if "." in n else int(n) for n in found]
    else:
        values = [v for w, v in _NUMBER_WORDS.items() if re.search(rf"\b{w}\b", _norm(t))]
    if len(values) != 1:
        return None
    v = values[0]
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        return None
    return v


_ISO_DATE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\b")


def parse_date(body: str) -> str | None:
    """An ISO date (YYYY-MM-DD) from ``2026-10-21`` or ``10/21/2026``; None otherwise."""
    import datetime as _dt

    t = (body or "").strip()
    m = _ISO_DATE.search(t)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    else:
        m = _US_DATE.search(t)
        if not m:
            return None
        mo, d, y = (int(x) for x in m.groups())
        if y < 100:
            y += 2000
    try:
        return _dt.date(y, mo, d).isoformat()
    except ValueError:
        return None


_CLOCK = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?(?![\d])", re.I)


def parse_time(body: str) -> str | None:
    """A clock time as ``HH:MM`` (24-hour) from ``3pm``, ``3:30 pm``, ``15:30``, ``noon``."""
    t = (body or "").strip().lower()
    if not t:
        return None
    if re.search(r"\bnoon|midday\b", t):
        return "12:00"
    if re.search(r"\bmidnight\b", t):
        return "00:00"
    m = _CLOCK.search(t)
    if not m:
        return None
    h, mins, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
    if not m.group(2) and not ampm:
        return None  # a bare number is not a time
    if mins > 59:
        return None
    if ampm:
        if not 1 <= h <= 12:
            return None
        h = (h % 12) + (12 if ampm == "pm" else 0)
    elif h > 23:
        return None
    return f"{h:02d}:{mins:02d}"


# --- The registry ------------------------------------------------------------------


@dataclass(frozen=True)
class NamedParser:
    """A reviewed parser a spec can name (``parser: <name>``)."""

    parse: Callable[[str], Any]
    description: str
    #: An answer it cannot read is recorded as "unknown" (voice and SMS) rather
    #: than re-asked forever.
    unknown_ok: bool = False
    #: Also keep the patient's own words under this answer key (the parsed value
    #: triages; the words are what a nurse wants to read).
    raw_field: str = ""


NAMED_PARSERS: dict[str, NamedParser] = {
    "trend": NamedParser(
        parse_trend,
        "better / same / worse, with synonyms (improving, unchanged, getting worse) and "
        'negation ("not worse" is not "worse")',
    ),
    "blood_sugar": NamedParser(
        parse_sugar,
        'high / low / in_range from meter readings in mg/dL ("120 to 250") or words '
        '("a bit high"); the most abnormal reading wins',
        unknown_ok=True,
        raw_field="blood_sugar_reading",
    ),
}

#: Types whose generic parser reads yes/no.
_YES_NO_TYPES = frozenset({"yes_no", "yes_no_unknown", "open_questions"})


def parser_for(q: Question) -> Callable[[str], Any]:
    """The parser a question's answers go through, on every channel."""
    if q.parser:
        return NAMED_PARSERS[q.parser].parse
    kind = q.kind
    if kind in _YES_NO_TYPES:
        return parse_yes_no
    if kind == "scale":
        lo, hi = q.scale_range()
        if (lo, hi) == (0, 10):
            return parse_scale
        return lambda body: parse_scale(body, lo, hi)
    if kind == "choice":
        choices = list(q.choices)
        return lambda body: parse_choice(body, choices)
    if kind == "number":
        return lambda body: parse_number(body, q.min, q.max)
    if kind == "date":
        return parse_date
    if kind == "time":
        return parse_time
    return parse_free_text


def parse(q: Question, body: str) -> Any:
    return parser_for(q)(body)


def has_parser(q: Question) -> bool:
    return (not q.parser) or q.parser in NAMED_PARSERS


def raw_field(q: Question) -> str:
    """Where the patient's own words are kept besides the parsed value ("" for none)."""
    return NAMED_PARSERS[q.parser].raw_field if q.parser in NAMED_PARSERS else ""


def unknown_ok(q: Question) -> bool:
    """Whether an unreadable answer may be recorded as "unknown" rather than re-asked
    (on voice: always for yes_no_unknown and named parsers that say so)."""
    if q.parser in NAMED_PARSERS:
        return NAMED_PARSERS[q.parser].unknown_ok
    return q.kind == "yes_no_unknown"


#: Free text is kept as said; typed answers must parse.
VoiceUnclear = Literal["reask", "unknown", "keep"]


def voice_on_unclear(q: Question) -> VoiceUnclear:
    """What record_answer does with an answer its parser cannot read.

    unknown: record "unknown" (yes_no_unknown, blood sugar); reask: tell the model
    to ask again (yes_no, scale, choice, number, date, time, open_questions);
    keep: record what the model passed (free text).
    """
    if unknown_ok(q):
        return "unknown"
    if q.kind == "text" and not q.parser:
        return "keep"
    return "reask"


def sms_unknownable(q: Question) -> bool:
    """By text, after one re-ask, an unreadable answer to a top-level question is
    recorded as "unknown" for yes/no types and unknown-capable parsers, "" otherwise.
    (Follow-ups are strict instead: see survey.strict_fields.)"""
    return q.kind in ("yes_no", "yes_no_unknown") or unknown_ok(q)
