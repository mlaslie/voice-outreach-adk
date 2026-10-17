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
"""

from __future__ import annotations

import datetime
import re
from enum import StrEnum

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


def is_red_flag(body: str) -> bool:
    text = (body or "").lower()
    for m in _RED_FLAG_RE.finditer(text):
        clause = _RED_FLAG_CLAUSE_SPLIT.split(text[: m.start()])[-1]
        before = re.findall(r"[a-z']+", clause)[-_RED_FLAG_REACH:]
        if not any(w in _RED_FLAG_NEGATORS for w in before):
            return True
    return False


def classify_compliance(body: str) -> Intent | None:
    """STOP / START / HELP, matched against the whole message. None if not one."""
    text = normalize(body)
    if text in STOP_WORDS:
        return Intent.STOP
    if text in START_WORDS:
        return Intent.START
    if text in HELP_WORDS:
        return Intent.HELP
    return None


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
