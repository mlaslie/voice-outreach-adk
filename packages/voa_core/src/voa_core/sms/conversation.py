"""The conversation state machine — pure decision logic, no I/O.

:func:`decide` takes the current session and one inbound message and
returns what should happen. Every branch of the patient-facing behaviour is
therefore testable without Firestore, Twilio, or a clock, and the effectful
parts (send, book, dial) stay in ``main.py`` where they can be read in one
place.

No global configuration is read. :func:`decide` takes its two behaviour
flags as a :class:`ConversationPolicy`, the site's copy values as a
:class:`~voa_core.copy.SiteCopy` and the calling window as a
:class:`~voa_core.timeparse.SchedulingPolicy`.
"""

from __future__ import annotations

import dataclasses
import datetime
import re
from dataclasses import dataclass
from enum import StrEnum

from voa_core import copy as messages
from voa_core import dob as dobcheck
from voa_core import intent as intent_mod
from voa_core import patient_questions as pq
from voa_core import survey as survey_mod
from voa_core import timeparse
from voa_core.copy import SiteCopy
from voa_core.sms import states as store
from voa_core.timeparse import DEFAULT_SCHEDULING, SchedulingPolicy


@dataclass(frozen=True)
class ConversationPolicy:
    """The two behaviour flags :func:`decide` needs.

    ``survey_enabled`` gates option 3 (the by-text questionnaire). It is also
    what the menu copy offers: :func:`decide` overrides ``SiteCopy.survey_enabled``
    with it, because offering an option the state machine will not honour
    strands whoever picks it.

    ``reply_to_compliance_keywords`` stays off while the carrier's own
    opt-out handling (e.g. Twilio Advanced Opt-Out) auto-replies to
    STOP/HELP; replying ourselves would deliver two messages for one keyword.
    """

    survey_enabled: bool = False
    reply_to_compliance_keywords: bool = False


DEFAULT_POLICY = ConversationPolicy()


class Effect(StrEnum):
    NONE = "none"
    OPT_OUT = "opt_out"
    OPT_IN = "opt_in"
    DISPATCH_RECALL = "dispatch_recall"
    BOOK = "book"
    REBOOK = "rebook"
    CANCEL_BOOKING = "cancel_booking"
    CANCEL_ALL = "cancel_all"  # drop every queued call for this handset
    START_SURVEY = "start_survey"  # begin the by-text questionnaire
    SUBMIT_SURVEY = "submit_survey"  # queue the report for filing
    FILE_EMERGENCY = "file_emergency"  # red flag -> file what we have


@dataclass(frozen=True)
class Decision:
    """What to do about one inbound message."""

    reply: str | None = None
    state: str | None = None  # None -> leave the session state alone
    effect: Effect = Effect.NONE
    when: datetime.datetime | None = None  # for BOOK / REBOOK
    retry: int | None = None  # None -> leave the retry counter alone
    note: str = ""  # for logs; never patient content
    # The questionnaire has to persist an answer and a cursor on every
    # turn, and Decision had nowhere to put them. Returning a patch keeps
    # decide() pure -- it describes the write, main.py performs it.
    session_patch: dict | None = None
    # Extra messages sent after `reply`, in order. The survey needs this
    # exactly once: the verified line and question 1 arrive together.
    extra_replies: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Ctx:
    """Everything one decide() call needs besides the session and the text."""

    now: datetime.datetime
    policy: ConversationPolicy
    site: SiteCopy
    scheduling: SchedulingPolicy


def _book(resolved: timeparse.Resolved, ctx: _Ctx, *, rebook: bool) -> Decision:
    return Decision(
        reply=messages.booked(
            timeparse.humanize(resolved.when, ctx.now), resolved.adjusted, ctx.site
        ),
        state=store.STATE_SCHEDULED,
        effect=Effect.REBOOK if rebook else Effect.BOOK,
        when=resolved.when,
        retry=0,
        note="rebooked" if rebook else "booked",
    )


def _time_or_none(body: str, ctx: _Ctx) -> timeparse.Resolved | None:
    """Read a time from free text, except from a lone menu digit.

    Only the ``awaiting_time`` state — where we explicitly asked "what day
    and time?" — should read "3" as three o'clock. Everywhere else a bare
    digit is far more likely to be someone still answering the menu, and
    silently rebooking them would be worse than not understanding.
    """
    if intent_mod.is_bare_menu_digit(body):
        return None
    return timeparse.resolve(body, ctx.now, ctx.scheduling)


def _unclear(session: dict, *, first: str, final: str, closing_state: str) -> Decision:
    """One re-ask, then a clean exit. Never a third attempt.

    A loop of "sorry, I didn't understand" is the fastest way to make a
    patient hate a service, and it burns the send budget.
    """
    if int(session.get("retry_count") or 0) < 1:
        return Decision(reply=first, retry=1, note="reask")
    return Decision(reply=final, state=closing_state, retry=0, note="gave-up")


# --- The questionnaire -----------------------------------------------------------


def _next_prompt(
    plan: list[str], pending: list[str], index: int, answers: dict | None = None
) -> tuple[str, bool]:
    """The text for the next thing to ask, and whether it is numbered.

    Pending follow-ups always come first: they belong to the question the
    patient just answered, and they are unnumbered because the patient was
    already told how many questions there are.
    """
    if pending:
        if pending[0] == survey_mod.F_PATIENT_QUESTIONS and _questions_so_far(answers or {}):
            return messages.ANOTHER_QUESTION, False
        return messages.FOLLOWUP_TEXT[pending[0]], False
    field = plan[index]
    return messages.question(index + 1, len(plan), messages.QUESTION_TEXT[field]), True


def _advance(
    session: dict,
    answers: dict,
    plan: list[str],
    pending: list[str],
    index: int,
    *,
    said: str | None = None,
) -> Decision:
    """Emit the next prompt, or finish.

    ``said`` replaces the default prompt (the questions loop acknowledges each
    question in the same message that asks for the next one). On completion it
    is sent as-is: it is an acknowledgement, not the closing.
    """
    if not pending and index >= len(plan):
        t = survey_mod.triage(answers)
        return Decision(
            # The closing is NOT sent here: it goes out only after the proxy
            # confirms the report was filed. Telling a patient their care
            # team has their answers and then failing to file them is the
            # worst outcome available in this feature.
            reply=said,
            state=store.STATE_SURVEY,
            effect=Effect.SUBMIT_SURVEY,
            retry=0,
            session_patch={
                "survey_answers": answers,
                "survey_pending": [],
                "survey_index": index,
                "survey_complete": True,
                "survey_follow_up": t.follow_up,
            },
            note="survey-complete",
        )
    text = said or _next_prompt(plan, pending, index, answers)[0]
    return Decision(
        reply=text,
        state=store.STATE_SURVEY,
        retry=0,
        session_patch={
            "survey_answers": answers,
            "survey_pending": pending,
            "survey_index": index,
        },
        note="survey-next",
    )


# At the DOB prompt a CALENDAR signal is not enough -- "August 20" is a
# birthday with the year left off. Only an explicit clock ("3pm", "14:30",
# "noon") means they are asking to be rung rather than answering us.
_CLOCK_AT_DOB_GATE = re.compile(
    r"\b(\d{1,2}\s*[:.]\d{2}|\d{1,2}\s*(am|pm)|noon|midday|midnight|o'?clock)\b",
    re.IGNORECASE,
)


def _handle_dob(session: dict, body: str, c: _Ctx) -> Decision:
    ctx = session.get("patient_context") or {}
    parsed = dobcheck.parse_dob(body, c.now.date())

    if parsed is None:
        # NOT a mismatch. Someone replying "1" or "call me instead" has
        # changed their mind, not failed an identity check -- counting it
        # would turn a patient into an urgent identity case in two messages.
        if intent_mod.is_cancel(body):
            return Decision(
                reply=messages.survey_stopped(),
                state=store.STATE_CLOSED,
                retry=0,
                note="survey-declined-at-dob",
            )
        choice = intent_mod.classify_choice(body, c.now)
        if choice is intent_mod.Intent.CALL_NOW:
            return Decision(
                reply=messages.calling_now(c.site),
                state=store.STATE_RECALL_DISPATCHED,
                effect=Effect.DISPATCH_RECALL,
                retry=0,
                note="call-now-from-dob",
            )
        if choice is intent_mod.Intent.NAMED_TIME:
            # We just asked for a BIRTHDATE, so a bare date is far more
            # likely to be a birthday with the year left off ("June 15",
            # "08/20") than a request for a callback. Without an explicit
            # clock word this booked a callback for anyone whose birthday
            # fell inside the scheduling window.
            if _CLOCK_AT_DOB_GATE.search(body or ""):
                resolved = _time_or_none(body, c)
                if resolved is not None:
                    return _book(resolved, c, rebook=False)
                return Decision(
                    reply=messages.ask_for_time(),
                    state=store.STATE_AWAITING_TIME,
                    retry=0,
                    note="ask-time-from-dob",
                )
        elif choice is intent_mod.Intent.PICK_TIME:
            return Decision(
                reply=messages.ask_for_time(),
                state=store.STATE_AWAITING_TIME,
                retry=0,
                note="ask-time-from-dob",
            )
        unreadable = int(session.get("dob_unreadable") or 0) + 1
        if unreadable >= 3:
            # Not an identity failure -- we simply cannot read what they are
            # sending. Close politely rather than looping to the message cap.
            return Decision(
                reply=messages.dob_failed(),
                state=store.STATE_CLOSED,
                retry=0,
                note="dob-unreadable-exhausted",
            )
        return Decision(
            reply=messages.dob_unreadable(),
            session_patch={"dob_unreadable": unreadable},
            note="dob-unreadable",
        )

    if dobcheck.matches(parsed, ctx.get("patient_dob")):
        plan = survey_mod.build_plan(ctx)
        first = messages.question(1, len(plan), messages.QUESTION_TEXT[plan[0]])
        return Decision(
            reply=messages.survey_preamble(len(plan)),
            extra_replies=(first,),
            state=store.STATE_SURVEY,
            effect=Effect.START_SURVEY,
            retry=0,
            session_patch={
                "survey_plan": plan,
                "survey_index": 0,
                "survey_pending": [],
                "survey_answers": {},
                "dob_verified": True,
            },
            note="dob-verified",
        )

    # A real date that is not the one on file.
    attempts = int(session.get("dob_attempts") or 0) + 1
    if attempts < 2:
        return Decision(
            # Never say it did not match: that confirms a record exists.
            reply=messages.dob_unreadable(),
            session_patch={"dob_attempts": attempts},
            note="dob-mismatch",
        )
    # Two genuine mismatches. This is the WRONG-PERSON case, not a failed
    # claim of identity: SMS never asked "are you {name}?". Filing it urgent
    # and promoting the origin call to success would close the real patient's
    # follow-up because someone else held the handset.
    return Decision(
        reply=messages.dob_failed(),
        state=store.STATE_CLOSED,
        effect=Effect.SUBMIT_SURVEY,
        retry=0,
        session_patch={
            "dob_attempts": attempts,
            "survey_answers": {},
            "survey_complete": False,
            "survey_outcome": "unable_to_reach_wrong_person",
            "survey_follow_up": survey_mod.FOLLOW_UP_NONE,
            "dob_verification_result": "mismatch",
        },
        note="dob-failed",
    )


# --- The questions loop ---------------------------------------------------------
#
# State: no new session fields. A bare YES to has_questions (or a second
# unreadable reply to it) queues the pseudo follow-up ``patient_questions`` in
# ``survey_pending``; while it is at the head of the queue, every reply is read
# by _handle_question_loop instead of a parser. Whether to say "What is your
# question?" or "Any other questions?" is derived from how many questions are
# already recorded in ``survey_answers["patient_questions"]``, so a redelivered
# or re-asked turn needs no extra cursor. The loop ends by dropping the pseudo
# field from the queue (NO, or the MAX_QUESTIONS cap), and _advance then files.
#
# The question text is stored ONLY in patient_questions -- never in _raw -- and
# nothing here answers it: the care team does.

# Something that reads as a question even without a "?" (SMS rarely has one).
_QUESTION_LIKE = re.compile(
    r"\?|\b(can|could|should|shall|would|will|when|what|whats|how|why|where|which|who|"
    r"is (it|there|this|that)|are (there|they|these)|do (i|you|we)|does|did|am i|"
    r"wonder(ing)?|question|need to know|want(ed)? to know|not sure (if|whether|about))\b",
    re.IGNORECASE,
)
# "I'm done" phrasings that are not a bare NO.
_NO_MORE = re.compile(
    r"\b(none|nothing( else)?|not (right )?now|not at (this|the moment)|no more|all set|"
    r"that'?s (all|it|everything)|i'?m (good|ok|okay|fine|all good)|all good|no questions?)\b",
    re.IGNORECASE,
)
# Sign-offs. After at least one question, these mean "that's all".
_SIGN_OFF = frozenset(
    {
        "ok",
        "okay",
        "k",
        "kk",
        "thanks",
        "thank you",
        "thanks so much",
        "thx",
        "ty",
        "great",
        "cool",
        "got it",
        "perfect",
        "ok thanks",
        "okay thanks",
        "ok thank you",
        "done",
    }
)
_LEAD_YES_NO = re.compile(
    r"^\s*(yes|yeah|yep|yup|ya|y|sure|no|nope|nah|n)\b[\s,.;:!-]*(but\b[\s,]*)?",
    re.IGNORECASE,
)
_BUT = re.compile(r"\bbut\b", re.IGNORECASE)


def _questions_so_far(answers: dict) -> list[dict]:
    qs = answers.get(survey_mod.F_PATIENT_QUESTIONS)
    return [q for q in qs if pq.valid_entry(q)] if isinstance(qs, list) else []


def asked_questions(answers: dict) -> bool:
    """Whether the patient recorded at least one question for the care team."""
    return bool(_questions_so_far(answers or {}))


def _words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", text or ""))


def _read_questions_reply(body: str) -> tuple[str | None, str]:
    """("no" | "yes" | "question" | None, question text) for has_questions and the loop.

    "yes" means a bare yes -- ask them to type it. Anything that reads as a
    question, or a sentence that is not a plain yes or no, IS the question:
    over-recording costs a nurse a minute, dropping one costs the patient an
    answer. "No, but ..." is never a no.
    """
    text = (body or "").strip()
    rest = _LEAD_YES_NO.sub("", text, count=1).strip()
    if _QUESTION_LIKE.search(text):
        return "question", rest or text
    yn = survey_mod.parse_yes_no(text)
    has_but = bool(_BUT.search(text))
    if not has_but:
        if yn == "no" and (_words(rest) < 3 or _NO_MORE.search(rest)):
            return "no", ""
        if yn != "yes" and _NO_MORE.search(text) and _words(text) <= 6:
            return "no", ""
    if yn == "yes" and _words(rest) < 3:
        return "yes", ""
    if _words(text) >= 3:
        return "question", rest or text
    return None, ""


def _record_question(
    session: dict, answers: dict, plan: list[str], pending: list[str], index: int, text: str
) -> Decision:
    qs = [*_questions_so_far(answers), pq.record(text)]
    answers[survey_mod.F_PATIENT_QUESTIONS] = qs
    if len(qs) >= pq.MAX_QUESTIONS:
        rest = [p for p in pending if p != survey_mod.F_PATIENT_QUESTIONS]
        return _advance(
            session, answers, plan, rest, index, said=messages.question_noted_last(text)
        )
    loop = (
        pending
        if pending[:1] == [survey_mod.F_PATIENT_QUESTIONS]
        else [
            survey_mod.F_PATIENT_QUESTIONS,
            *pending,
        ]
    )
    return _advance(session, answers, plan, loop, index, said=messages.question_noted(text))


def _handle_question_loop(
    session: dict, body: str, answers: dict, plan: list[str], pending: list[str], index: int
) -> Decision:
    kind, text = _read_questions_reply(body)
    have = _questions_so_far(answers)
    if kind is None and have and " ".join((body or "").lower().split()).strip(" .!") in _SIGN_OFF:
        kind = "no"
    if kind == "no":
        return _advance(session, answers, plan, pending[1:], index)
    if kind == "yes":
        return Decision(
            reply=messages.FOLLOWUP_TEXT[survey_mod.F_PATIENT_QUESTIONS],
            state=store.STATE_SURVEY,
            retry=0,
            note="survey-question-prompt",
        )
    # We asked them to type a question: a short reply that is not yes or no is
    # still the question ("driving", "my meds").
    text = text or (body or "").strip()
    if not text:
        return Decision(
            reply=_next_prompt(plan, pending, index, answers)[0],
            retry=0,
            note="survey-question-empty",
        )
    return _record_question(session, answers, plan, pending, index, text)


def _keep_raw(answers: dict, field: str, body: str) -> None:
    # Keep the sentence, not just the parsed value, and keyed by FIELD:
    # triage scans prose for requests for a human, expressed worry and severe
    # language, all of which a yes/no parser throws away -- and the dashboard
    # needs to know which message an escalation came from in order to mark it.
    raw = answers.get(survey_mod.RAW_KEY)
    raw = dict(raw) if isinstance(raw, dict) else {}
    raw[field] = (body or "").strip()[: survey_mod.MAX_FREE_TEXT]
    answers[survey_mod.RAW_KEY] = raw


def _handle_survey(session: dict, body: str) -> Decision:
    plan = list(session.get("survey_plan") or [])
    pending = list(session.get("survey_pending") or [])
    index = int(session.get("survey_index") or 0)
    answers = dict(session.get("survey_answers") or {})

    if not plan:
        # Should be unreachable: the plan is written before this state is
        # entered. Silence would leave the patient with no idea what happened.
        return Decision(
            reply=messages.survey_cut_short(),
            state=store.STATE_CLOSED,
            retry=0,
            note="survey-no-plan",
        )

    if session.get("survey_complete") or (not pending and index >= len(plan)):
        # Finished, waiting on the tick to file and send the closing. That is
        # a 0-60s window in which patients type "thanks" or "done" -- and
        # plan[index] with index == len(plan) is an IndexError, which FastAPI
        # turns into a 500 and Twilio then retries in a loop.
        return Decision(note="survey-already-complete")

    if intent_mod.is_cancel(body):
        # "C" stops the questions without opting them out of the programme.
        # The word CANCEL is a carrier STOP keyword and is handled far above
        # this line -- it must never be offered as a survey exit.
        return Decision(
            reply=messages.survey_stopped(),
            state=store.STATE_CLOSED,
            effect=Effect.SUBMIT_SURVEY if survey_mod.has_urgent_finding(answers) else Effect.NONE,
            retry=0,
            session_patch={"survey_answers": answers, "survey_complete": False},
            note="survey-stopped",
        )

    field = pending[0] if pending else plan[index]
    if field == survey_mod.F_PATIENT_QUESTIONS:
        return _handle_question_loop(session, body, answers, plan, pending, index)

    if field == survey_mod.Q_HAS_QUESTIONS:
        kind, text = _read_questions_reply(body)
        if kind is not None or int(session.get("retry_count") or 0) >= 1:
            # A second unreadable reply is taken as a yes: the loop then asks
            # them to type the question, and a bare "yes" with nothing
            # recorded still files as "has questions" for a nurse to call.
            answers[field] = "no" if kind == "no" else "yes"
            _keep_raw(answers, field, body)
            if kind == "question":
                return _record_question(session, answers, plan, [], index + 1, text)
            loop = [] if kind == "no" else [survey_mod.F_PATIENT_QUESTIONS]
            return _advance(session, answers, plan, loop, index + 1)

    value = survey_mod.parse_answer(field, body)

    if value is None:
        retries = int(session.get("retry_count") or 0)
        if retries < 1:
            text, _ = _next_prompt(plan, pending, index, answers)
            return Decision(
                reply=messages.unclear_answer(text),
                retry=1,
                note="survey-reask",
            )
        if field in survey_mod.STRICT_FIELDS:
            # The proxy requires these as typed values once their parent is
            # "yes", so "unknown and continue" is not available -- the report
            # would be unfileable. Stay on the question, but NOT forever:
            # after a few tries, stop and file what we have. A patient who
            # cannot give a pain number still told us they are in pain, and
            # looping until the message cap strands them mid-form.
            if retries < survey_mod.MAX_STRICT_RETRIES:
                text, _ = _next_prompt(plan, pending, index, answers)
                return Decision(
                    reply=messages.unclear_answer(text),
                    retry=retries + 1,
                    note="survey-strict-reask",
                )
            return Decision(
                reply=messages.survey_cut_short(),
                state=store.STATE_CLOSED,
                effect=Effect.SUBMIT_SURVEY,
                retry=0,
                session_patch={"survey_answers": answers, "survey_complete": False},
                note="survey-strict-exhausted",
            )
        value = "unknown" if field in survey_mod.UNKNOWNABLE else ""

    answers[field] = value
    _keep_raw(answers, field, body)
    if field == survey_mod.Q_SUGAR and (body or "").strip():
        # The parsed status triages; the patient's own numbers are what a
        # nurse wants to read ("120 to 250").
        answers[survey_mod.F_SUGAR_READING] = (body or "").strip()[: survey_mod.MAX_FREE_TEXT]
    if pending:
        pending = pending[1:]
    else:
        pending = list(survey_mod.followups_for(field, value))
        index += 1
    return _advance(session, answers, plan, pending, index)


def decide(
    session: dict | None,
    body: str,
    now: datetime.datetime | None = None,
    *,
    site: SiteCopy,
    policy: ConversationPolicy = DEFAULT_POLICY,
    scheduling: SchedulingPolicy = DEFAULT_SCHEDULING,
) -> Decision:
    """Interpret one inbound message. ``session`` None means unknown sender."""
    now = now or timeparse.now_local(scheduling)
    # The menu must offer exactly what this function will honour.
    site = dataclasses.replace(site, survey_enabled=policy.survey_enabled)
    c = _Ctx(now=now, policy=policy, site=site, scheduling=scheduling)

    # --- 1. Compliance keywords. Whole-message match, checked first, in every
    #        state, and never routed anywhere else.
    compliance = intent_mod.classify_compliance(body)
    if compliance is intent_mod.Intent.STOP:
        return Decision(
            reply=messages.opted_out(site) if policy.reply_to_compliance_keywords else None,
            state=store.STATE_OPTED_OUT,
            effect=Effect.OPT_OUT,
            note="stop",
        )
    if compliance is intent_mod.Intent.START:
        return Decision(effect=Effect.OPT_IN, note="start")
    if compliance is intent_mod.Intent.HELP:
        return Decision(
            reply=messages.help_message(site) if policy.reply_to_compliance_keywords else None,
            note="help",
        )

    # --- 2. Red flags, before the text is read as an answer to anything.
    if intent_mod.is_red_flag(body):
        # Cancel anything queued. Telling someone this line is unmonitored
        # and then ringing them for a routine wellness check two hours later
        # is the kind of contradiction that destroys trust in the whole
        # programme.
        # File it. A 911 text the care team never sees is the exact
        # failure this programme exists to prevent, and voice files
        # outcome="emergency_ended" for the same event. Mid-survey this
        # carries whatever answers were collected; outside a survey it files
        # nothing but still cancels.
        if (session or {}).get("state") in store.SURVEY_STATES:
            return Decision(
                reply=messages.emergency(),
                state=store.STATE_CLOSED,
                effect=Effect.FILE_EMERGENCY,
                note="red-flag-mid-survey",
            )
        return Decision(
            reply=messages.emergency(),
            state=store.STATE_CLOSED,
            effect=Effect.CANCEL_ALL,
            note="red-flag",
        )

    # --- 3. Unknown sender: stay silent. Answering anyone who texts the
    #        number would turn this into a service strangers can drive.
    if session is None:
        return Decision(note="no-session")

    state = session.get("state")

    if state in (store.STATE_CLOSED, store.STATE_EXPIRED):
        # A session can expire while its callback is still days away
        # (48h TTL vs 14-day booking horizon). "C" must still cancel, or we
        # tell the patient the follow-up is closed and then ring them anyway.
        if session.get("callback_id") and intent_mod.is_cancel(body):
            return Decision(
                reply=messages.booking_canceled(site),
                effect=Effect.CANCEL_BOOKING,
                note="cancel-after-expiry",
            )
        return Decision(reply=messages.session_closed(site), note="closed")
    if state == store.STATE_OPTED_OUT:
        return Decision(note="opted-out")

    # --- 4. Live states.
    if state == store.STATE_AWAITING_CHOICE:
        choice = intent_mod.classify_choice(body, now)

        if choice is intent_mod.Intent.CALL_NOW:
            return Decision(
                reply=messages.calling_now(site),
                state=store.STATE_RECALL_DISPATCHED,
                effect=Effect.DISPATCH_RECALL,
                retry=0,
                note="call-now",
            )

        if choice is intent_mod.Intent.NAMED_TIME:
            # They skipped the menu and gave a time. Book it rather than
            # asking a question they have already answered.
            resolved = timeparse.resolve(body, now, scheduling)
            if resolved is not None:
                return _book(resolved, c, rebook=False)
            return Decision(
                reply=messages.ask_for_time(),
                state=store.STATE_AWAITING_TIME,
                retry=0,
                note="ask-time",
            )

        if choice is intent_mod.Intent.PICK_TIME:
            return Decision(
                reply=messages.ask_for_time(),
                state=store.STATE_AWAITING_TIME,
                retry=0,
                note="ask-time",
            )

        if choice is intent_mod.Intent.BY_TEXT and policy.survey_enabled:
            ctx = session.get("patient_context") or {}
            # Without a DOB we cannot verify, and without a patient_id we
            # cannot file. Starting something that provably cannot finish is
            # worse than offering the path that works.
            if (
                not str(ctx.get("patient_dob") or "").strip()
                or not str(ctx.get("patient_id") or "").strip()
            ):
                return Decision(
                    reply=messages.ask_for_time(),
                    state=store.STATE_AWAITING_TIME,
                    retry=0,
                    note="by-text-no-context",
                )
            return Decision(
                reply=messages.ask_dob(ctx.get("patient_name")),
                state=store.STATE_AWAITING_DOB,
                retry=0,
                session_patch={"dob_attempts": 0},
                note="by-text-start",
            )

        return _unclear(
            session,
            first=messages.unclear_choice(site),
            final=messages.gave_up_choice(),
            closing_state=store.STATE_CLOSED,
        )

    if state == store.STATE_AWAITING_TIME:
        if intent_mod.is_cancel(body):
            return Decision(
                reply=messages.gave_up_time(),
                state=store.STATE_CLOSED,
                retry=0,
                note="cancel-during-scheduling",
            )
        # Review finding C-7: a digit with no clock or calendar word ("2",
        # "2 thanks", "at 3") is not read as a time here. _parse_clock maps a
        # bare 1-7 to the afternoon, so a carrier redelivery of the "2" that
        # moved the patient into this state booked 2:00 PM and confirmed it
        # to them. Asking again costs one message; a phantom booking costs a
        # call the patient never asked for.
        resolved = (
            None
            if timeparse.is_bare_number_without_time_signal(body)
            else timeparse.resolve(body, now, scheduling)
        )
        if resolved is not None:
            return _book(resolved, c, rebook=False)
        return _unclear(
            session,
            first=messages.unclear_time(),
            final=messages.gave_up_time(),
            closing_state=store.STATE_CLOSED,
        )

    if state == store.STATE_SCHEDULED:
        if intent_mod.is_cancel(body):
            return Decision(
                reply=messages.booking_canceled(site),
                state=store.STATE_CLOSED,
                effect=Effect.CANCEL_BOOKING,
                retry=0,
                note="cancel-booking",
            )
        resolved = _time_or_none(body, c)
        if resolved is not None:
            return _book(resolved, c, rebook=True)
        booked_for = session.get("scheduled_for_text") or "your callback"
        return Decision(reply=messages.already_booked(booked_for), note="already-booked")

    if state == store.STATE_AWAITING_DOB:
        return _handle_dob(session, body, c)

    if state == store.STATE_SURVEY:
        return _handle_survey(session, body)

    if state == store.STATE_DIALING:
        # The proxy has been asked to dial and may already be ringing. Saying
        # anything now would race the call itself.
        return Decision(note="dial-in-flight")

    if state == store.STATE_RECALL_DISPATCHED:
        # A call is already on its way. Only a new time is worth acting on;
        # anything else gets silence rather than a duplicate confirmation.
        resolved = _time_or_none(body, c)
        if resolved is not None:
            return _book(resolved, c, rebook=False)
        return Decision(note="recall-in-flight")

    return Decision(note=f"unhandled-state:{state}")
