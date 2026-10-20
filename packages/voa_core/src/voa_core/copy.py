"""Every word the SMS follow-up sends to a patient.

Kept in one file so the copy can be reviewed as copy - the wording carries
most of the privacy and safety properties of the feature, and it is easier
to get wrong than the code around it.

Two rules govern everything here:

* **The invite carries no clinical content.** No "procedure", "surgery",
  "post-op", or "discharge". Whoever is holding that handset may not be the
  patient, and a text sits on a lock screen. A first name and the hospital
  name is the least that still makes sense to the right person.
* **Compliance text appears once**, on the invite. Repeating "Reply STOP"
  on every message is how these programmes become a nuisance.

No global configuration is read for the copy in this file's first half:
everything site-specific comes in as a :class:`SiteCopy`, and only the
factories that need it take one. The questionnaire wording (second half) is
read from ``config/campaigns/post_op.yaml``; see the note there.

Every string here must stay GSM-7 (no smart quotes, dashes or ellipses): one
non-GSM character switches the whole message to UCS-2 and cuts each segment
from 160 characters to 70.
"""

from __future__ import annotations

import string as _string
from dataclasses import dataclass
from functools import cache as _cache


@dataclass(frozen=True)
class SiteCopy:
    """The site-specific values patient copy needs.

    ``org_name`` must match the A2P 10DLC brand registration exactly, so it
    has no default: a fallback name would put the wrong sender on texts.
    """

    org_name: str
    support_email: str
    voice_number: str = ""  # display only: the caller ID patients will see
    survey_enabled: bool = False  # offer "3 = answer by text"


def first_name(full_name: str | None) -> str:
    """'Jordan Avery' -> 'Jordan'. Falls back to a neutral address.

    Never returns a surname: a first name identifies the intended reader to
    the right person while telling a wrong recipient almost nothing.
    """
    name = (full_name or "").strip()
    if not name:
        return "you"
    return name.split()[0]


def _menu_options(site: SiteCopy) -> str:
    """Inline form, for the re-ask where a full menu block would be shouty."""
    opts = ["1 = call me now", "2 = pick a time"]
    if site.survey_enabled:
        opts.append("3 = answer by text")
    return ", ".join(opts)


def _menu_lines(site: SiteCopy) -> list[str]:
    """One option per line. Scannable on a lock screen, which a comma-run
    of three options is not."""
    lines = ["1 = call me now", "2 = pick a time"]
    if site.survey_enabled:
        lines.append("3 = answer by text")
    return lines


def invite(patient_name: str | None, site: SiteCopy) -> str:
    """The one unsolicited message. Sent at most once per patient per day.

    Laid out over several lines rather than one paragraph: this arrives on a
    lock screen and its whole job is to make three choices scannable at a
    glance. Newlines are GSM-7 characters like any other, so the layout
    costs one segment boundary at most and nothing in deliverability.
    """
    who = first_name(patient_name)
    blocks = [
        f"{site.org_name} has a follow-up message for {who}.",
        "Reply:\n" + "\n".join(_menu_lines(site)),
    ]
    if site.voice_number:
        blocks.append(f"Calls come from {site.voice_number}.")
    blocks.append("Reply STOP to opt out.")
    return "\n\n".join(blocks)


def unclear_choice(site: SiteCopy) -> str:
    return f"Sorry, I didn't catch that. Reply {_menu_options(site)}."


def gave_up_choice() -> str:
    """After one failed re-ask. Ends on a step the patient controls.

    Deliberately promises nothing. An earlier draft said "we'll try you
    again another time", which is only true if another call happens to go
    unanswered later - the patient would have been left waiting for
    something this service never scheduled.
    """
    return (
        "No problem. If you'd like to talk to someone, contact your care team. "
        "For an emergency, call 911."
    )


def calling_now(site: SiteCopy) -> str:
    """ "Shortly", not "in about a minute".

    The dial is queued for the next ticker pass, so the true delay is a
    minute plus however long that takes. Naming a number we cannot hold to
    is a small lie repeated on every single call-now.
    """
    if site.voice_number:
        return f"Thanks - we'll call you shortly from {site.voice_number}."
    return "Thanks - we'll call you shortly."


def cannot_call_back() -> str:
    """When we know the dial will be refused, say so instead of promising."""
    return (
        "Sorry - we're not able to place that call automatically. Please contact "
        "your care team and they can help."
    )


def ask_for_time() -> str:
    return 'What day and time works? Reply like "tomorrow 3pm" or "Thursday morning".'


def unclear_time() -> str:
    return 'Sorry, I didn\'t catch a time. Try something like "tomorrow 3pm" or "Thu 10am".'


def gave_up_time() -> str:
    """Same rule as gave_up_choice: no promise we do not keep.

    This used to say "we'll try you again tomorrow morning". Nothing in the
    code scheduled that call.
    """
    return (
        "No problem. If you'd like to talk to someone, contact your care team. "
        "For an emergency, call 911."
    )


def booked(when_text: str, adjusted: bool, site: SiteCopy) -> str:
    """Read the resolved time back. This is the safeguard on time parsing."""
    lead = f"The closest we can do is {when_text}." if adjusted else f"You're set for {when_text}."
    tail = f" We'll call from {site.voice_number}." if site.voice_number else ""
    return f"{lead}{tail} Reply C to cancel."


def already_booked(when_text: str) -> str:
    return f"You're already set for {when_text}. Reply C to cancel, or send a new day and time."


def booking_canceled(site: SiteCopy) -> str:
    return f"Cancelled - we won't call. To reschedule, email {site.support_email}."


def nothing_to_cancel() -> str:
    return "There's no scheduled call to cancel."


def emergency() -> str:
    """No paging, no on-call: say so, and point at 911.

    The last sentence is deliberate. Implying someone is watching this line
    would be worse than saying plainly that nobody is.
    """
    return (
        "If this is a medical emergency, call 911 now - tap: tel:911 - or go to "
        "your nearest emergency room. This text line is not monitored for "
        "emergencies."
    )


def opted_out(site: SiteCopy) -> str:
    """Only sent if Twilio's Advanced Opt-Out is off; it replies for us otherwise."""
    return f"You've been unsubscribed from {site.org_name} follow-up messages."


def help_message(site: SiteCopy) -> str:
    """Deliberately says "follow-up messages", not "post-visit follow-up".

    Reachable when Twilio's Advanced Opt-Out is off, so it can land on the
    lock screen of a handset we have not identified. The same rule as the
    invite applies: name the sender, not the reason.
    """
    return (
        f"{site.org_name} follow-up. Reply STOP to opt out. For help, email "
        f"{site.support_email}. For an emergency, call 911. "
        "Msg & data rates may apply."
    )


def session_closed(site: SiteCopy) -> str:
    """Reply to a message that arrives after a conversation has finished."""
    return (
        "Thanks for your reply. This follow-up is closed. If you need us, email "
        f"{site.support_email}. For an emergency, call 911."
    )


# =============================================================================
# The questionnaire by text.
#
# Two rules on top of the ones at the top of this file:
#
# * NOTHING CLINICAL BEFORE VERIFICATION. Everything from ask_dob() up to the
#   preamble (sms_scripts.preamble in the campaign) must be readable by a
#   stranger holding the handset without disclosing anything. The questions
#   themselves are clinical, which is the entire reason the DOB gate exists.
# * NEVER NARRATE A FAILED MATCH. <safety_first_rules> forbids confirming that
#   a record exists or explaining why verification failed. "That's not the
#   date we have" tells an unknown reader they have found a real patient.
# =============================================================================


def ask_dob(patient_name: str | None) -> str:
    """The gate. A first name only -- same disclosure as the invite."""
    return (
        f"Before we start, please confirm the date of birth for "
        f"{first_name(patient_name)}. Reply in the format MM/DD/YYYY."
    )


def dob_unreadable() -> str:
    """Could not read a date. NOT a failed match -- costs no attempt."""
    return "Sorry, I couldn't read that as a date. Please reply in the format MM/DD/YYYY."


def dob_failed() -> str:
    """Final answer after two genuine mismatches.

    Says nothing about what we hold, whether a record exists, or how close
    they were. Leaves a route that does not depend on us.

    A plain hyphen, not an em dash: the em dash is not GSM-7 and turned this
    into a three-segment UCS-2 message (review finding S-10).
    """
    return (
        "Thanks - we're not able to continue by text. "
        "If you need to reach the care team, please call the number on your paperwork."
    )


def survey_stopped() -> str:
    """They replied C. Not an opt-out -- they can still be called.

    Stays in code, not in the campaign: it is also sent at the DOB gate, i.e.
    BEFORE verification, so it falls under the no-clinical-content rule above.
    """
    return (
        "No problem, we've stopped the questions. Your care team can follow up with you directly."
    )


# =============================================================================
# The questionnaire wording: read from config/campaigns/post_op.yaml.
#
# The questions (`sms`, `sms_if_*`) and the questionnaire's own lines
# (`sms_scripts`) are campaign data so a non-developer can reword them in one
# file for both channels (docs/EDITING-QUESTIONS.md). Everything ABOVE this
# line stays in code on purpose: the invite, menu, scheduling replies, DOB gate
# and STOP/HELP are sent before identity is verified or are compliance copy
# registered with the carrier (A2P 10DLC), so changing them is a privacy and
# compliance review, not a rewording.
#
# A2P NOTE: SMS is in dry-run. Changed questionnaire copy may still need the
# A2P 10DLC campaign's sample messages re-registered before sending goes live.
# =============================================================================

#: GSM 03.38 basic set plus the extension table. One character outside it
#: switches the whole message to UCS-2 (70 characters a segment, not 160).
GSM7 = frozenset(
    "@\u00a3$\u00a5\u00e8\u00e9\u00f9\u00ec\u00f2\u00c7\n\u00d8\u00f8\r\u00c5\u00e5"
    "\u0394_\u03a6\u0393\u039b\u03a9\u03a0\u03a8\u03a3\u0398\u039e\u00c6\u00e6\u00df\u00c9"
    " !\"#\u00a4%&'()*+,-./0123456789:;<=>?\u00a1ABCDEFGHIJKLMNOPQRSTUVWXYZ\u00c4\u00d6\u00d1"
    "\u00dc\u00a7\u00bfabcdefghijklmnopqrstuvwxyz\u00e4\u00f6\u00f1\u00fc\u00e0^{}\\[~]|\u20ac"
)

#: sms_scripts keys -> the placeholders each may use. All keys are required.
SMS_SCRIPT_PLACEHOLDERS: dict[str, frozenset[str]] = {
    "preamble": frozenset({"total"}),
    "numbered_question": frozenset({"number", "total", "question"}),
    "unclear_answer": frozenset({"question"}),
    "ask_question": frozenset(),
    "another_question": frozenset(),
    "question_noted": frozenset({"question", "another_question"}),
    "question_noted_last": frozenset({"question"}),
    "closing_routine": frozenset({"questions_follow_up", "medication_reminder"}),
    "closing_urgent": frozenset({"questions_follow_up", "medication_reminder"}),
    "questions_follow_up": frozenset(),
    "medication_reminder": frozenset(),
    "cut_short": frozenset(),
    "file_failed": frozenset(),
}
#: Placeholders a template must keep: without them the message loses its point.
SMS_SCRIPT_REQUIRED: dict[str, frozenset[str]] = {
    "numbered_question": frozenset({"question"}),
    "unclear_answer": frozenset({"question"}),
    # Clinic request (19 Oct 2026): every completed questionnaire ends with it.
    "closing_routine": frozenset({"questions_follow_up", "medication_reminder"}),
    "closing_urgent": frozenset({"questions_follow_up", "medication_reminder"}),
}
#: Per-question SMS variants: question id -> {field: placeholders}. The code
#: (sms/conversation.py) picks the variant; a variant on any other question
#: would never be sent, so it is rejected rather than silently ignored.
SMS_QUESTION_VARIANTS: dict[str, dict[str, frozenset[str]]] = {
    "pain_location": {"sms_if_known": frozenset({"site"})},
    "swelling_description": {
        "sms_if_side_known": frozenset(),
        "sms_if_trend_known": frozenset(),
    },
}
_VARIANT_FIELDS = ("sms_if_known", "sms_if_side_known", "sms_if_trend_known")


def placeholders(template: str) -> set[str]:
    """The {names} in a template. Raises ValueError on a stray brace."""
    return {name for _, name, _, _ in _string.Formatter().parse(template) if name is not None}


def sms_copy_problems(c) -> list[str]:
    """Why this campaign's SMS wording cannot be sent (empty if it can)."""
    out: list[str] = []

    def check(where: str, text: str, allowed: frozenset[str], required=frozenset()) -> None:
        try:
            names = placeholders(text)
        except ValueError as e:
            out.append(f"{where}: bad braces ({e}); use {{name}} placeholders only")
            return
        for n in sorted(names - allowed):
            ok = ", ".join(f"{{{a}}}" for a in sorted(allowed)) or "none"
            out.append(f"{where}: unknown placeholder {{{n}}} (allowed: {ok})")
        for n in sorted(required - names):
            out.append(f"{where}: must keep the {{{n}}} placeholder")
        bad = sorted(set(text) - GSM7)
        if bad:
            out.append(f"{where}: not plain GSM-7 text: {''.join(bad)!r}")

    for q in c.questions:
        if not q.sms.strip():
            out.append(f"{q.id}: missing `sms` (every question is asked by text too)")
        else:
            check(f"{q.id}.sms", q.sms, frozenset())
        variants = SMS_QUESTION_VARIANTS.get(q.id, {})
        for field in _VARIANT_FIELDS:
            text = getattr(q, field)
            if field in variants:
                if not text.strip():
                    out.append(f"{q.id}: missing `{field}`")
                else:
                    check(f"{q.id}.{field}", text, variants[field])
            elif text:
                out.append(f"{q.id}: `{field}` is not used for this question")
    for key in sorted(set(c.sms_scripts) - set(SMS_SCRIPT_PLACEHOLDERS)):
        out.append(f"sms_scripts.{key}: unknown line (nothing sends it)")
    for key, allowed in SMS_SCRIPT_PLACEHOLDERS.items():
        text = c.sms_scripts.get(key, "")
        if not text.strip():
            out.append(f"sms_scripts.{key}: missing")
        else:
            check(f"sms_scripts.{key}", text, allowed, SMS_SCRIPT_REQUIRED.get(key, frozenset()))
    return out


@_cache
def _campaign():
    from . import survey

    c = survey.sms_campaign()
    problems = sms_copy_problems(c)
    if problems:
        raise ValueError(f"campaign {c.id!r} SMS copy is not sendable: {problems}")
    return c


def _q(field: str):
    return next(q for q in _campaign().questions if q.id == field)


def _script(key: str, **values) -> str:
    return _campaign().sms_scripts[key].format(**values)


@_cache
def _question_texts() -> dict[str, str]:
    return {q.id: q.sms for q in _campaign().questions if not q.when}


@_cache
def _followup_texts() -> dict[str, str]:
    out = {q.id: q.sms for q in _campaign().questions if q.when}
    # The questions loop's prompt: a pseudo follow-up queued after a bare YES
    # to has_questions (not a campaign question; voice records questions with
    # a tool instead).
    out["patient_questions"] = _script("ask_question")
    return out


def question_text(field: str) -> str:
    """A numbered question's SMS text (without the "(n/N)")."""
    return _question_texts()[field]


def followup_text(field: str) -> str:
    """An unnumbered follow-up's SMS text (the default wording)."""
    return _followup_texts()[field]


def __getattr__(name: str):
    # The old module constants, now read from the campaign on first use.
    lazy = {
        "QUESTION_TEXT": lambda: dict(_question_texts()),
        "FOLLOWUP_TEXT": lambda: dict(_followup_texts()),
        "ANOTHER_QUESTION": another_question,
        "MEDICATION_LIST_REMINDER": lambda: _script("medication_reminder"),
        "QUESTIONS_FOLLOW_UP": lambda: _script("questions_follow_up"),
        "SWELLING_TREND_ONLY": swelling_trend_only,
        "SWELLING_SIDE_ONLY": swelling_side_only,
    }
    if name in lazy:
        return lazy[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def survey_preamble(total: int) -> str:
    """Sent with question 1, immediately after a successful match."""
    return _script("preamble", total=total)


def question(index: int, total: int, text: str) -> str:
    """A numbered question. Follow-ups are deliberately NOT numbered."""
    return _script("numbered_question", number=index, total=total, question=text)


# The questions loop. The care team answers the questions -- never this line --
# so the acknowledgement only echoes back what was recorded.
_ECHO_MAX = 60


def another_question() -> str:
    return _script("another_question")


def _echo(question_text: str) -> str:
    t = " ".join((question_text or "").split())
    return t if len(t) <= _ECHO_MAX else t[: _ECHO_MAX - 3].rstrip() + "..."


def question_noted(question_text: str) -> str:
    """Acknowledge one question and offer another."""
    return _script(
        "question_noted", question=_echo(question_text), another_question=another_question()
    )


def question_noted_last(question_text: str) -> str:
    """Acknowledge the last question we can take (the cap). The closing follows."""
    return _script("question_noted_last", question=_echo(question_text))


def unclear_answer(retry_text: str) -> str:
    return _script("unclear_answer", question=retry_text)


def survey_cut_short() -> str:
    """We stopped asking. Their answers so far are still filed."""
    return _script("cut_short")


def _closing(key: str, asked_questions: bool) -> str:
    qfu = _script("questions_follow_up") + " " if asked_questions else ""
    return _script(key, questions_follow_up=qfu, medication_reminder=_script("medication_reminder"))


def survey_done_routine(asked_questions: bool = False) -> str:
    return _closing("closing_routine", asked_questions)


def survey_done_urgent(asked_questions: bool = False) -> str:
    """Two-tier closing: an urgent patient must be told someone is coming.

    Telling a patient who just reported 9/10 pain that "that's everything"
    and nothing more is the kind of ending that reads as being dismissed.
    """
    return _closing("closing_urgent", asked_questions)


def survey_file_failed() -> str:
    """The submission failed permanently. Sent once, never repeated."""
    return _script("file_failed")


# Follow-ups reworded from what the patient already said (survey.known_pain_site,
# survey.swelling_known): never ask "where?" right after they told us where.
def pain_elsewhere(site: str) -> str:
    return _q("pain_location").sms_if_known.format(site=site)


def swelling_trend_only() -> str:
    """The side is known: ask only whether it is getting worse."""
    return _q("swelling_description").sms_if_side_known


def swelling_side_only() -> str:
    """The trend is known: ask only which side."""
    return _q("swelling_description").sms_if_trend_known
