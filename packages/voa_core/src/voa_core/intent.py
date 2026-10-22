"""Read a patient's SMS reply.

Three layers, cheapest first:

1. **Compliance keywords** (STOP/HELP/...) -- matched against the *whole*
   message, the way carriers and Twilio do it, so "stop the pain" is not an
   opt-out.
2. **Red flags** -- screened on every inbound message in every state, before
   the text is treated as an answer to anything.
3. **Deterministic** -- menu numbers and short words.

Nothing here guesses. An unreadable reply returns ``UNCLEAR`` and the
caller asks once more.

Other languages (docs/LANGUAGES-PLAN.md §3.3, step 3): per-language STOP/HELP
words and red-flag phrases are loaded ADDITIVELY from
``config/languages/keywords.yaml``. The English lists below always work and are
unchanged; a text that matches nothing in the extra lists behaves exactly as
before. The extra lists are drafts awaiting translator review, used anyway
because stricter is the safe direction (an extra opt-out or 911 reply).
"""

from __future__ import annotations

import datetime
import functools
import logging
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from voa_core.timeparse import has_explicit_time_signal, parse_when


class Intent(StrEnum):
    STOP = "stop"
    START = "start"  # UNSTOP / START -- re-subscribe
    HELP = "help"
    EMERGENCY = "emergency"
    CALL_NOW = "call_now"
    PICK_TIME = "pick_time"  # pressed 2 -- wants to schedule, hasn't said when
    NAMED_TIME = "named_time"  # skipped the menu and gave a time outright
    BY_TEXT = "by_text"
    CANCEL = "cancel"
    UNCLEAR = "unclear"


# Carrier-standard opt-out / opt-in / help keywords. Matched whole-message.
STOP_WORDS = frozenset(
    {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "optout", "opt out"}
)
START_WORDS = frozenset({"start", "unstop", "yes join", "resume"})
HELP_WORDS = frozenset({"help", "info"})

# Conservative: each of these describes an emergency on its own. Anything
# vaguer is left to the nurse rather than triggering a 911 message that
# would train patients to ignore it.
_RED_FLAGS = (
    r"chest pain",
    r"chest pains",
    r"pain in my chest",
    r"crushing chest",
    r"chest (is |feels )?(tightness|pressure|squeezing|tight|heavy|heaviness)",
    r"tightness in my chest",
    r"pressure in my chest",
    r"can'?t breathe",
    r"cannot breathe",
    r"trouble breathing",
    r"short(ness)? of breath",
    r"struggling to breathe",
    r"not breathing",
    r"heart attack",
    r"\bstrokes?\b",
    r"bleeding (a lot|heavily|badly|everywhere)",
    r"heavy bleeding",
    r"won'?t stop bleeding",
    r"can'?t stop the bleeding",
    r"hemorrhag",
    r"passed out",
    r"fainted",
    r"unconscious",
    r"unresponsive",
    r"slurred speech",
    r"face is drooping",
    r"can'?t move my",
    r"call(ing)? (an )?ambulance",
    r"going to the (er|emergency)",
    r"\b911\b",
    # The by-text questionnaire invites free-form clinical prose in a way the
    # two-option menu never did, so the SMS list has to match the voice
    # safety rules rather than being a subset of them.
    r"worst headache",
    r"thunderclap headache",
    r"vomiting blood",
    r"throwing up blood",
    r"coughing up blood",
    r"blood in my (stool|vomit|urine)",
    r"bloody stool",
    r"black stool",
    r"calf (is |feels )?(pain|painful|swelling|swollen)",
    r"swollen calf",
    r"(leg|calf) is (swollen|swelling)",
    r"clot in my (leg|calf)",
    r"soaking through",
    r"soaked through",
    r"bleeding through (the )?(gauze|bandage|dressing)",
    r"sudden(ly)? confus",
    r"very confused",
    r"arm (is )?(weak|numb)",
    r"weakness in my arm",
    r"can'?t feel my (arm|leg|face)",
    r"kill myself",
    r"end my life",
    r"suicid",
    r"hurt myself",
    r"want to die",
)
_RED_FLAG_RE = re.compile("|".join(_RED_FLAGS), re.IGNORECASE)

_NOW_WORDS = frozenset(
    {
        "1",
        "now",
        "call now",
        "call me now",
        "call me",
        "call",
        "right now",
        "call now please",
        "yes call me",
        "asap",
    }
)
_LATER_WORDS = frozenset(
    {
        "2",
        "later",
        "call later",
        "pick a time",
        "schedule",
        "another time",
        "call me later",
        "reschedule",
        "not now",
    }
)
_TEXT_WORDS = frozenset(
    {"3", "text", "by text", "texts", "sms", "questions", "survey", "answer by text", "text me"}
)
# Cancelling a *booking*. Deliberately does NOT include bare "cancel": that
# is a carrier-standard opt-out keyword and must keep meaning opt-out, which
# is why the confirmation message asks for "C" instead.
_CANCEL_WORDS = frozenset(
    {
        "c",
        "cancel it",
        "cancel callback",
        "cancel the call",
        "cancel my call",
        "nevermind",
        "never mind",
    }
)


def normalize(body: str) -> str:
    """Lowercase, collapse whitespace, drop surrounding punctuation."""
    text = (body or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .!,;:\"'")


# A red flag preceded by a negator in the same clause is the patient saying
# what they do NOT have (review finding C-6). "no chest pain, feeling fine"
# used to fire the 911 text and, in awaiting_choice, CANCEL_ALL -- deleting a
# callback the patient had booked. Over-triggering on a genuine flag is the
# chosen direction; over-triggering on an explicit denial that also destroys
# a booking is not.
_RED_FLAG_NEGATORS = frozenset(
    {
        "no",
        "not",
        "never",
        "without",
        "denies",
        "deny",
        "isn't",
        "isnt",
        "wasn't",
        "wasnt",
        "don't",
        "dont",
        "didn't",
        "didnt",
        "haven't",
        "havent",
        "hasn't",
        "hasnt",
        "aren't",
        "arent",
    }
)
_RED_FLAG_CLAUSE_SPLIT = re.compile(r"[,;.!?]|\bbut\b|\bhowever\b|\bthough\b|\bexcept\b")
_RED_FLAG_REACH = 3


def _english_red_flag(body: str) -> str | None:
    text = (body or "").lower()
    for m in _RED_FLAG_RE.finditer(text):
        clause = _RED_FLAG_CLAUSE_SPLIT.split(text[: m.start()])[-1]
        before = re.findall(r"[a-z']+", clause)[-_RED_FLAG_REACH:]
        if not any(w in _RED_FLAG_NEGATORS for w in before):
            return m.group(0)
    return None


# Phrasings a TRANSLATION into English produces for the same emergencies
# ("perdí el conocimiento" -> "I lost consciousness"; "quitarme la vida" ->
# "take my own life"). Only for text known to be a machine translation, so an
# English patient's own texts are read exactly as before; same negation rule.
_TRANSLATED_RED_FLAGS = (
    r"lost consciousness",
    r"loss of consciousness",
    r"blacked out",
    r"passing out",
    r"fainting",
    r"difficulty breathing",
    r"hard (time )?(to )?breath(e|ing)",
    r"can'?t catch my breath",
    r"out of breath",
    r"chest hurts",
    r"chest (has been |is |keeps |started )?hurting",
    r"pain in the chest",
    r"(severe|strong|sharp|intense) chest",
    r"bleeding (very )?(heavily|a lot|profusely|too much)",
    r"profuse bleeding",
    r"(severe|serious|uncontrolled|massive|major|abundant) bleeding",
    r"a lot of blood",
    r"face (is )?(drooping|droopy|crooked)",
    r"slurr(ed|ing)",
    r"take my (own )?life",
    r"taking my (own )?life",
    r"ending my life",
    r"don'?t want to live",
)
_TRANSLATED_RED_FLAG_RE = re.compile("|".join(_TRANSLATED_RED_FLAGS), re.IGNORECASE)


def _negated_before(text: str, start: int) -> bool:
    clause = _RED_FLAG_CLAUSE_SPLIT.split(text[:start])[-1]
    before = re.findall(r"[a-z']+", clause)[-_RED_FLAG_REACH:]
    return any(w in _RED_FLAG_NEGATORS for w in before)


def red_flag_match(body: str, *, translated: bool = False) -> str | None:
    """The red-flag phrase found in ``body`` (not negated), or None.

    English first (exactly :func:`is_red_flag`'s old rule), then, for a machine
    translation (``translated``), the phrasings translators use, then every
    per-language list (Spanish today) on the accent-folded text."""
    hit = _english_red_flag(body)
    if hit is not None:
        return hit
    if not body:
        return None
    if translated:
        text = body.lower()
        for m in _TRANSLATED_RED_FLAG_RE.finditer(text):
            if not _negated_before(text, m.start()):
                return m.group(0)
    folded = fold(body)
    for lst in _keywords().red_flags:
        hit = lst.match(folded)
        if hit is not None:
            return hit
    return None


def is_red_flag(body: str, *, translated: bool = False) -> bool:
    return red_flag_match(body, translated=translated) is not None


def classify_compliance(body: str) -> Intent | None:
    """STOP / START / HELP, matched against the whole message. None if not one.

    The English words first (unchanged), then the per-language STOP and HELP
    words of every language (a Spanish speaker on an English session still
    gets ALTO)."""
    text = normalize(body)
    if text in STOP_WORDS:
        return Intent.STOP
    if text in START_WORDS:
        return Intent.START
    if text in HELP_WORDS:
        return Intent.HELP
    if not text:
        return None
    kw = _keywords()
    folded = fold(text).strip(_EXTRA_PUNCT)
    if folded in kw.stop:
        return Intent.STOP
    if folded in kw.help:
        return Intent.HELP
    return None


# --------------------------------------------------------------------------- other languages

log = logging.getLogger(__name__)

KEYWORDS_FILE = Path("languages") / "keywords.yaml"
_EXTRA_PUNCT = " .!,;:\"'¡¿?。！？、،؟"


def fold(text: str) -> str:
    """Lower-cased, whitespace collapsed, Latin accents removed ("Arrêt" ->
    "arret"). Combining marks are only dropped after a Latin letter, so
    Devanagari, Thai, Tamil... keep their vowel signs."""
    out: list[str] = []
    for ch in unicodedata.normalize("NFD", (text or "").lower()):
        if unicodedata.combining(ch) and out and "a" <= out[-1] <= "z":
            continue
        out.append(ch)
    return " ".join(unicodedata.normalize("NFC", "".join(out)).split())


@dataclass(frozen=True)
class RedFlagList:
    """One language's red-flag phrases with its own negation rule."""

    language: str
    pattern: re.Pattern[str]
    negators: frozenset[str]
    clause_split: re.Pattern[str]
    reach: int

    def match(self, folded: str) -> str | None:
        for m in self.pattern.finditer(folded):
            clause = self.clause_split.split(folded[: m.start()])[-1]
            before = re.findall(r"[^\W\d_]+", clause)[-self.reach :]
            if not any(w in self.negators for w in before):
                return m.group(0)
        return None


@dataclass(frozen=True)
class Keywords:
    stop: frozenset[str] = frozenset()
    help: frozenset[str] = frozenset()
    red_flags: tuple[RedFlagList, ...] = ()
    #: language -> {"stop": [...], "help": [...]} (folded), for reports and tests
    by_language: Mapping[str, Mapping[str, tuple[str, ...]]] | None = None


def _words(v: Any) -> list[str]:
    return [fold(str(w)).strip(_EXTRA_PUNCT) for w in (v or []) if str(w).strip()]


def parse_keywords(doc: Mapping[str, Any]) -> Keywords:
    """The keyword file's contents (see config/languages/keywords.yaml)."""
    stop: set[str] = set()
    help_: set[str] = set()
    by_language: dict[str, dict[str, tuple[str, ...]]] = {}
    for lang, entry in (doc.get("stop_help") or {}).items():
        entry = entry or {}
        s, h = _words(entry.get("stop")), _words(entry.get("help"))
        stop.update(s)
        help_.update(h)
        by_language[str(lang)] = {"stop": tuple(s), "help": tuple(h)}
    # The English words keep their own meaning (START, HELP...).
    english = STOP_WORDS | START_WORDS | HELP_WORDS
    stop -= english
    help_ -= english | stop
    flags: list[RedFlagList] = []
    for lang, entry in (doc.get("red_flags") or {}).items():
        entry = entry or {}
        patterns = [str(p) for p in entry.get("patterns") or [] if str(p).strip()]
        if not patterns:
            continue
        breaks = [re.escape(fold(str(w))) for w in entry.get("clause_breaks") or []]
        split = r"[,;.!?¡¿]" + "".join(rf"|\b{w}\b" for w in breaks)
        flags.append(
            RedFlagList(
                language=str(lang),
                pattern=re.compile("|".join(f"(?:{p})" for p in patterns)),
                negators=frozenset(fold(str(w)) for w in entry.get("negators") or []),
                clause_split=re.compile(split),
                reach=int(entry.get("reach") or _RED_FLAG_REACH),
            )
        )
    return Keywords(frozenset(stop), frozenset(help_), tuple(flags), by_language)


@functools.cache
def _keywords() -> Keywords:
    """Loaded once per process. A missing or broken file leaves the English
    lists alone (logged loudly: the extra languages are then unprotected)."""
    import yaml

    try:
        from .campaign import default_config_dir

        path = default_config_dir() / KEYWORDS_FILE
        doc = yaml.safe_load(path.read_text()) or {}
        return parse_keywords(doc)
    except Exception:
        log.exception(
            "language keywords not loaded; English only",
            extra={"event": "intent.keywords_missing"},
        )
        return Keywords()


def keywords() -> Keywords:
    return _keywords()


def classify_choice(body: str, now: datetime.datetime | None = None) -> Intent:
    """Which menu branch the patient picked. UNCLEAR if it isn't clear.

    Menu selection is resolved **before** any attempt to read the message as
    a time. "2" is the second option, not two o'clock -- reading it as a
    clock time would book a callback for someone who was only asking to
    choose a time.

    A reply that skips the menu and simply names a time ("tomorrow 3pm")
    comes back as NAMED_TIME, so the patient is not asked something they
    have already answered. ``now`` only matters for calendar edge cases
    (e.g. "2/29" outside a leap year) and defaults to the wall clock.
    """
    text = normalize(body)
    if not text:
        return Intent.UNCLEAR
    if text in _NOW_WORDS:
        return Intent.CALL_NOW
    if text in _LATER_WORDS:
        return Intent.PICK_TIME
    if text in _TEXT_WORDS:
        return Intent.BY_TEXT

    # Leading menu digit with trailing words: "1 please", "2 - sounds good".
    # But "1 pm" and "3 tomorrow" are times, not menu choices -- a digit alone
    # is not enough to decide, so require an explicit clock or calendar word
    # before overriding the menu.
    m = re.match(r"^([123])\b", text)
    if m:
        if has_explicit_time_signal(text) and parse_when(text, now) is not None:
            return Intent.NAMED_TIME
        return {
            "1": Intent.CALL_NOW,
            "2": Intent.PICK_TIME,
            "3": Intent.BY_TEXT,
        }[m.group(1)]

    if parse_when(text, now) is not None:
        return Intent.NAMED_TIME

    return Intent.UNCLEAR


def is_cancel(body: str) -> bool:
    return normalize(body) in _CANCEL_WORDS


def is_bare_menu_digit(body: str) -> bool:
    """A lone 1, 2 or 3.

    Outside the state where a time was explicitly asked for, these must not
    be read as clock times: a patient replying "1" to a booking
    confirmation would otherwise be rebooked for one o'clock.
    """
    return normalize(body) in ("1", "2", "3")
