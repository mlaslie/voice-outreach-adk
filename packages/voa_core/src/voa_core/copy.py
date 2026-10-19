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

No global configuration is read: everything site-specific comes in as a
:class:`SiteCopy`, and only the factories that need it take one.

Every string here must stay GSM-7 (no smart quotes, dashes or ellipses): one
non-GSM character switches the whole message to UCS-2 and cuts each segment
from 160 characters to 70.
"""

from __future__ import annotations

from dataclasses import dataclass


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
#   verified() line must be readable by a stranger holding the handset without
#   disclosing anything. The questions themselves are clinical, which is the
#   entire reason the DOB gate exists.
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


def survey_preamble(total: int) -> str:
    """Sent with question 1, immediately after a successful match."""
    return (
        f"Thank you, that's verified. I'll send {total} short questions, one at a time. "
        "Just reply to each one. Reply C at any point to stop."
    )


def question(index: int, total: int, text: str) -> str:
    """A numbered question. Follow-ups are deliberately NOT numbered."""
    return f"({index}/{total}) {text}"


# The numbered questions. Wording tracks the voice agent's <wellness_check>
# (config/campaigns/post_op.yaml) so a by-text report says the same thing a
# call would.
#
# A2P NOTE: SMS is in dry-run. Patient-facing SMS copy changed on 19 Oct 2026
# (new questions, the questions loop, the medication-list reminder in the
# closings). Changed copy may need the A2P 10DLC campaign's sample messages
# re-registered before this goes live -- check before turning sending on.
QUESTION_TEXT = {
    "overall_feeling": "How are you feeling overall since your procedure?",
    "pain_present": "Are you having any pain right now? Reply YES or NO.",
    "fever_reported": (
        "Have you had a fever, or a temperature above 100.4F, since you were "
        "discharged? Reply YES or NO."
    ),
    "incision_concerns": (
        "Is there anything unusual about your incision or bandage - spreading "
        "redness, drainage, warmth, opening, or a bad smell? Reply YES or NO."
    ),
    "legs_swollen": "Are your legs or feet swollen? Reply YES or NO.",
    "sleeping_well": "Are you sleeping well? Reply YES or NO.",
    "blood_sugar_status": (
        "What is the range of your blood sugars? Reply with your numbers, "
        'like "110 to 160", or say if they have been high or low.'
    ),
    "has_questions": (
        "Do you have any questions for the care team? Reply NO, or type your question."
    ),
}

# Unnumbered follow-ups. Every one of these is REQUIRED by the proxy once its
# parent answer is "yes", so none of them is optional. ("patient_questions" is
# the questions loop: asked after a bare YES to has_questions.)
FOLLOWUP_TEXT = {
    "pain_severity": (
        "On a scale of 0 to 10, where 10 is the worst pain imaginable, what "
        "number would you give it right now?"
    ),
    "pain_location": "Please describe the pain - where is it, and what does it feel like?",
    "pain_trend": "Is it getting better, staying the same, or getting worse?",
    "incision_description": "Can you describe what you're seeing?",
    "swelling_description": "Is it one leg or both, and is it getting worse?",
    "patient_questions": "What is your question? Please type it in one message.",
}

# The questions loop. The care team answers the questions -- never this line --
# so the acknowledgement only echoes back what was recorded.
_ECHO_MAX = 60
ANOTHER_QUESTION = "Any other questions? Reply with your question, or NO if that's all."


def _echo(question_text: str) -> str:
    t = " ".join((question_text or "").split())
    return t if len(t) <= _ECHO_MAX else t[: _ECHO_MAX - 3].rstrip() + "..."


def question_noted(question_text: str) -> str:
    """Acknowledge one question and offer another."""
    return f'Got it: "{_echo(question_text)}". {ANOTHER_QUESTION}'


def question_noted_last(question_text: str) -> str:
    """Acknowledge the last question we can take (the cap). The closing follows."""
    return (
        f'Got it: "{_echo(question_text)}". That\'s as many as we can take by text - '
        "your care team can help with anything else."
    )


def unclear_answer(retry_text: str) -> str:
    return f"Sorry, I didn't catch that. {retry_text}"


def survey_stopped() -> str:
    """They replied C. Not an opt-out -- they can still be called."""
    return (
        "No problem, we've stopped the questions. Your care team can follow up with you directly."
    )


def survey_cut_short() -> str:
    """We stopped asking. Their answers so far are still filed."""
    return (
        "Thanks - we'll stop there. Your care team has what you've told us "
        "and can follow up with you directly. In an emergency, call 911."
    )


#: Every completed survey ends with this (clinic request, 19 Oct 2026).
MEDICATION_LIST_REMINDER = (
    "Please bring a list of your current medications to your post-op follow-up clinic visit."
)
QUESTIONS_FOLLOW_UP = "Your care team will follow up to get your questions answered."


def _closing(lead: str, tail: str, asked_questions: bool) -> str:
    parts = [lead]
    if asked_questions:
        parts.append(QUESTIONS_FOLLOW_UP)
    parts += [MEDICATION_LIST_REMINDER, tail]
    return " ".join(parts)


def survey_done_routine(asked_questions: bool = False) -> str:
    return _closing(
        "That's everything - thank you. Your care team has your answers.",
        "If anything changes or gets worse, call your care team, or 911 in an emergency.",
        asked_questions,
    )


def survey_done_urgent(asked_questions: bool = False) -> str:
    """Two-tier closing: an urgent patient must be told someone is coming.

    Telling a patient who just reported 9/10 pain that "that's everything"
    and nothing more is the kind of ending that reads as being dismissed.
    """
    return _closing(
        "Thank you - that's everything. Based on your answers someone from your "
        "care team will reach out to you.",
        "If things get worse before then, call your care team, or 911 in an emergency.",
        asked_questions,
    )


def survey_file_failed() -> str:
    """The submission failed permanently. Sent once, never repeated."""
    return (
        "Thank you for your answers. We had a problem recording them - "
        "please call the number on your paperwork so your care team has them."
    )
