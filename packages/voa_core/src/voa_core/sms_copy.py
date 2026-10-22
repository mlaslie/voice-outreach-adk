"""The shared SMS copy in a patient's language (docs/LANGUAGES-PLAN.md §3.5, step 5).

``voa_core.copy`` holds the English texts that are not part of a use case: the
invite and its 1/2/3 menu, the scheduling replies, the DOB gate, STOP/HELP and
the emergency text. English is sent from there, unchanged (the parity goldens
pin it). For another language the same texts come from the language pack's
``sms_copy:`` section (``config/campaigns/i18n/<campaign>.<lang>.yaml``), with
exactly the keys and ``{placeholders}`` of :data:`ENGLISH`.

:data:`ENGLISH` is the English wording cut into those keys. It is the reference a
translator works from and the lint checks against; ``tests/test_sms_copy.py``
proves that :class:`PackCopy` built from it reproduces ``voa_core.copy`` byte
for byte, so the keys cannot drift from what English patients receive.

Two objects with the same methods:

* :data:`ENGLISH_COPY` (:class:`EnglishCopy`): calls ``voa_core.copy``.
* :class:`PackCopy`: the pack's wording, put together the same way.

Which one a conversation uses is decided once, by ``conversation.session_copy``.

Also here: :func:`segments`, the encoding (GSM-7 or UCS-2) and segment count of
a text, used by the pack lint and the send budget.
"""

from __future__ import annotations

import datetime
import math
from collections.abc import Mapping
from dataclasses import dataclass

from . import copy as en
from . import timeparse
from .copy import GSM7, SiteCopy

#: The shared SMS copy, by key, in English. Placeholders a key may use are the
#: ones in its English text; a pack must keep exactly those.
ENGLISH: dict[str, str] = {
    # first_name()'s fallback when the record has no name.
    "you": "you",
    # The invite: intro, "Reply:" with the menu, the caller ID line, the opt-out.
    "invite_intro": "{org_name} has a follow-up message for {name}.",
    "reply_header": "Reply:",
    "menu_call_now": "1 = call me now",
    "menu_pick_time": "2 = pick a time",
    "menu_by_text": "3 = answer by text",
    "calls_come_from": "Calls come from {voice_number}.",
    "opt_out_line": "Reply STOP to opt out.",
    # {options}: the menu items joined with ", ".
    "unclear_choice": "Sorry, I didn't catch that. Reply {options}.",
    "gave_up_choice": (
        "No problem. If you'd like to talk to someone, contact your care team. "
        "For an emergency, call 911."
    ),
    "calling_now": "Thanks - we'll call you shortly.",
    "calling_now_from": "Thanks - we'll call you shortly from {voice_number}.",
    "cannot_call_back": (
        "Sorry - we're not able to place that call automatically. Please contact "
        "your care team and they can help."
    ),
    "ask_for_time": 'What day and time works? Reply like "tomorrow 3pm" or "Thursday morning".',
    "unclear_time": (
        'Sorry, I didn\'t catch a time. Try something like "tomorrow 3pm" or "Thu 10am".'
    ),
    "gave_up_time": (
        "No problem. If you'd like to talk to someone, contact your care team. "
        "For an emergency, call 911."
    ),
    # A booking: the lead, then (with a caller ID) calls_from, then "Reply C".
    "booked": "You're set for {when}.",
    "booked_adjusted": "The closest we can do is {when}.",
    "booked_calls_from": "We'll call from {voice_number}.",
    "booked_cancel": "Reply C to cancel.",
    "already_booked": (
        "You're already set for {when}. Reply C to cancel, or send a new day and time."
    ),
    # {when} in already_booked when the booked time was not kept.
    "your_callback": "your callback",
    "booking_canceled": "Cancelled - we won't call. To reschedule, email {support_email}.",
    "nothing_to_cancel": "There's no scheduled call to cancel.",
    "emergency": (
        "If this is a medical emergency, call 911 now - tap: tel:911 - or go to "
        "your nearest emergency room. This text line is not monitored for "
        "emergencies."
    ),
    "opted_out": "You've been unsubscribed from {org_name} follow-up messages.",
    "help": (
        "{org_name} follow-up. Reply STOP to opt out. For help, email "
        "{support_email}. For an emergency, call 911. "
        "Msg & data rates may apply."
    ),
    "session_closed": (
        "Thanks for your reply. This follow-up is closed. If you need us, email "
        "{support_email}. For an emergency, call 911."
    ),
    # The DOB gate. A non-English patient's date is read day first, so a pack's
    # prompt must ask for DD/MM (the lint checks it).
    "ask_dob": (
        "Before we start, please confirm the date of birth for {name}. "
        "Reply in the format MM/DD/YYYY."
    ),
    "dob_unreadable": (
        "Sorry, I couldn't read that as a date. Please reply in the format MM/DD/YYYY."
    ),
    "dob_failed": (
        "Thanks - we're not able to continue by text. "
        "If you need to reach the care team, please call the number on your paperwork."
    ),
    "survey_stopped": (
        "No problem, we've stopped the questions. Your care team can follow up with you directly."
    ),
    # The time interpreter's read-back (voa_agents.sms): the lead, then the reply line.
    "confirm_time": "Just to check - did you mean {when}?",
    "confirm_time_adjusted": "The closest we can do is {when}.",
    "confirm_time_reply": "Reply YES to book it, or send another day and time.",
    # How a booked time is written ("Thu Aug 13, 3:00 PM"). Seven weekday and
    # twelve month abbreviations, space separated, Monday and January first.
    # {year} is " 2027" when it is not this year, else nothing; {time} is 3:00 PM.
    "weekdays": "Mon Tue Wed Thu Fri Sat Sun",
    "months": "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec",
    "when_format": "{weekday} {month} {day}{year}, {time}",
}
KEYS: tuple[str, ...] = tuple(ENGLISH)


class EnglishCopy:
    """English: every text from ``voa_core.copy`` (unchanged, parity-pinned)."""

    language: str | None = None
    approved = True

    def invite(self, patient_name: str | None, site: SiteCopy) -> str:
        return en.invite(patient_name, site)

    def unclear_choice(self, site: SiteCopy) -> str:
        return en.unclear_choice(site)

    def gave_up_choice(self) -> str:
        return en.gave_up_choice()

    def calling_now(self, site: SiteCopy) -> str:
        return en.calling_now(site)

    def cannot_call_back(self) -> str:
        return en.cannot_call_back()

    def ask_for_time(self) -> str:
        return en.ask_for_time()

    def unclear_time(self) -> str:
        return en.unclear_time()

    def gave_up_time(self) -> str:
        return en.gave_up_time()

    def booked(self, when_text: str, adjusted: bool, site: SiteCopy) -> str:
        return en.booked(when_text, adjusted, site)

    def already_booked(self, when_text: str) -> str:
        return en.already_booked(when_text)

    def your_callback(self) -> str:
        return "your callback"

    def booking_canceled(self, site: SiteCopy) -> str:
        return en.booking_canceled(site)

    def nothing_to_cancel(self) -> str:
        return en.nothing_to_cancel()

    def emergency(self) -> str:
        return en.emergency()

    def opted_out(self, site: SiteCopy) -> str:
        return en.opted_out(site)

    def help_message(self, site: SiteCopy) -> str:
        return en.help_message(site)

    def session_closed(self, site: SiteCopy) -> str:
        return en.session_closed(site)

    def ask_dob(self, patient_name: str | None) -> str:
        return en.ask_dob(patient_name)

    def dob_unreadable(self) -> str:
        return en.dob_unreadable()

    def dob_failed(self) -> str:
        return en.dob_failed()

    def survey_stopped(self) -> str:
        return en.survey_stopped()

    def confirm_time_prompt(self, when_text: str, adjusted: bool) -> str:
        return en.confirm_time_prompt(when_text, adjusted)

    def humanize(self, when: datetime.datetime, now: datetime.datetime | None = None) -> str:
        return timeparse.humanize(when, now)


ENGLISH_COPY = EnglishCopy()


class PackCopy:
    """A language pack's ``sms_copy``, put together exactly like the English.

    ``texts`` must hold every key of :data:`ENGLISH` (``language_packs`` only
    builds one from a pack that passed the lint)."""

    def __init__(self, texts: Mapping[str, str], language: str | None, *, approved: bool):
        missing = [k for k in KEYS if not str(texts.get(k) or "").strip()]
        if missing:
            raise ValueError(f"sms_copy is missing {', '.join(missing)}")
        self.texts = {k: str(texts[k]) for k in KEYS}
        self.language = language
        self.approved = approved

    def _t(self, key: str, **values) -> str:
        return self.texts[key].format(**values)

    def first_name(self, full_name: str | None) -> str:
        name = (full_name or "").strip()
        return name.split()[0] if name else self.texts["you"]

    def _menu(self, site: SiteCopy) -> list[str]:
        lines = [self.texts["menu_call_now"], self.texts["menu_pick_time"]]
        if site.survey_enabled:
            lines.append(self.texts["menu_by_text"])
        return lines

    def invite(self, patient_name: str | None, site: SiteCopy) -> str:
        blocks = [
            self._t("invite_intro", org_name=site.org_name, name=self.first_name(patient_name)),
            self.texts["reply_header"] + "\n" + "\n".join(self._menu(site)),
        ]
        if site.voice_number:
            blocks.append(self._t("calls_come_from", voice_number=site.voice_number))
        blocks.append(self.texts["opt_out_line"])
        return "\n\n".join(blocks)

    def unclear_choice(self, site: SiteCopy) -> str:
        return self._t("unclear_choice", options=", ".join(self._menu(site)))

    def gave_up_choice(self) -> str:
        return self.texts["gave_up_choice"]

    def calling_now(self, site: SiteCopy) -> str:
        if site.voice_number:
            return self._t("calling_now_from", voice_number=site.voice_number)
        return self.texts["calling_now"]

    def cannot_call_back(self) -> str:
        return self.texts["cannot_call_back"]

    def ask_for_time(self) -> str:
        return self.texts["ask_for_time"]

    def unclear_time(self) -> str:
        return self.texts["unclear_time"]

    def gave_up_time(self) -> str:
        return self.texts["gave_up_time"]

    def booked(self, when_text: str, adjusted: bool, site: SiteCopy) -> str:
        lead = self._t("booked_adjusted" if adjusted else "booked", when=when_text)
        tail = (
            " " + self._t("booked_calls_from", voice_number=site.voice_number)
            if site.voice_number
            else ""
        )
        return f"{lead}{tail} {self.texts['booked_cancel']}"

    def already_booked(self, when_text: str) -> str:
        return self._t("already_booked", when=when_text)

    def your_callback(self) -> str:
        return self.texts["your_callback"]

    def booking_canceled(self, site: SiteCopy) -> str:
        return self._t("booking_canceled", support_email=site.support_email)

    def nothing_to_cancel(self) -> str:
        return self.texts["nothing_to_cancel"]

    def emergency(self) -> str:
        return self.texts["emergency"]

    def opted_out(self, site: SiteCopy) -> str:
        return self._t("opted_out", org_name=site.org_name)

    def help_message(self, site: SiteCopy) -> str:
        return self._t("help", org_name=site.org_name, support_email=site.support_email)

    def session_closed(self, site: SiteCopy) -> str:
        return self._t("session_closed", support_email=site.support_email)

    def ask_dob(self, patient_name: str | None) -> str:
        return self._t("ask_dob", name=self.first_name(patient_name))

    def dob_unreadable(self) -> str:
        return self.texts["dob_unreadable"]

    def dob_failed(self) -> str:
        return self.texts["dob_failed"]

    def survey_stopped(self) -> str:
        return self.texts["survey_stopped"]

    def confirm_time_prompt(self, when_text: str, adjusted: bool) -> str:
        lead = self._t("confirm_time_adjusted" if adjusted else "confirm_time", when=when_text)
        return f"{lead} {self.texts['confirm_time_reply']}"

    def humanize(self, when: datetime.datetime, now: datetime.datetime | None = None) -> str:
        """``timeparse.humanize`` in the pack's words: same fields, same rules."""
        from .timefmt import patient_clock

        current_year = (now or datetime.datetime.now(when.tzinfo)).year
        return self._t(
            "when_format",
            weekday=self.texts["weekdays"].split()[when.weekday()],
            month=self.texts["months"].split()[when.month - 1],
            day=when.day,
            year=f" {when.year}" if when.year != current_year else "",
            time=patient_clock(when),
        )


# --------------------------------------------------------------------------- encoding


#: GSM 03.38 extension table: each costs two septets (an escape plus the char).
GSM7_EXTENSION = frozenset("^{}\\[~]|€\f")
GSM7_SINGLE, GSM7_MULTI = 160, 153
UCS2_SINGLE, UCS2_MULTI = 70, 67


@dataclass(frozen=True)
class Segments:
    encoding: str  # "GSM-7" | "UCS-2"
    units: int  # septets (GSM-7) or UTF-16 code units (UCS-2)
    segments: int

    @property
    def unicode(self) -> bool:
        return self.encoding == "UCS-2"


def segments(text: str) -> Segments:
    """How a carrier sends ``text``: GSM-7 (160 a single segment, 153 a segment
    when concatenated) unless one character is outside GSM 03.38, which makes
    the whole message UCS-2 (70, or 67 concatenated). Emoji and other astral
    characters count two UTF-16 units. An extension character never splits
    across a segment boundary in practice; that edge is not modelled, so a
    message right at a boundary may be one segment more than counted."""
    text = text or ""
    if all(ch in GSM7 or ch == "\f" for ch in text):
        units = sum(2 if ch in GSM7_EXTENSION else 1 for ch in text)
        single, multi, enc = GSM7_SINGLE, GSM7_MULTI, "GSM-7"
    else:
        units = len(text.encode("utf-16-le")) // 2
        single, multi, enc = UCS2_SINGLE, UCS2_MULTI, "UCS-2"
    n = 1 if units <= single else math.ceil(units / multi)
    return Segments(enc, units, n)


#: The lint's limits for a pack's texts (docs/HANDOFF.md, step 5). Three
#: segments is the most a single text may cost (201 UCS-2 characters, 459
#: GSM-7); more than two gets a warning, because every extra segment is billed
#: and carriers deliver long concatenations less reliably.
WARN_ABOVE_SEGMENTS = 2
FAIL_ABOVE_SEGMENTS = 3
