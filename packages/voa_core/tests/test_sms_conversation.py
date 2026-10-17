"""The SMS conversation state machine: pure decisions, no I/O."""

import datetime
from zoneinfo import ZoneInfo

import pytest
from voa_core import survey
from voa_core.copy import SiteCopy
from voa_core.sms import conversation
from voa_core.sms import states as st
from voa_core.sms.conversation import ConversationPolicy, Effect

SITE = SiteCopy(
    org_name="Acme Health",
    support_email="support@example.org",
    voice_number="+12025550142",
)
SURVEY_ON = ConversationPolicy(survey_enabled=True)
PHONE = "+12025550123"


@pytest.fixture
def now():
    """A fixed Wednesday afternoon in clinic time: 2026-08-12 14:00."""
    return datetime.datetime(2026, 8, 12, 14, 0, tzinfo=ZoneInfo("America/New_York"))


def decide(session, body, now, **kw):
    return conversation.decide(session, body, now, site=SITE, **kw)


def session(state=st.STATE_AWAITING_CHOICE, **over):
    return {
        "phone": PHONE,
        "state": state,
        "retry_count": 0,
        "message_count": 1,
        "patient_name": "Jordan Avery",
        "patient_context": {
            "patient_name": "Jordan Avery",
            "patient_id": "P1",
            "patient_dob": "1968-07-04",
        },
        "call_sid": "CA1",
        **over,
    }


class TestCompliance:
    def test_stop_opts_out_from_any_state(self, now):
        for state in (st.STATE_AWAITING_CHOICE, st.STATE_SCHEDULED, st.STATE_AWAITING_TIME):
            d = decide(session(state), "STOP", now)
            assert d.effect is Effect.OPT_OUT
            assert d.state == st.STATE_OPTED_OUT

    def test_stop_is_silent_by_default(self, now):
        # The carrier's opt-out handling already auto-replies.
        assert decide(session(), "STOP", now).reply is None

    def test_replies_to_keywords_only_when_the_policy_says_so(self, now):
        policy = ConversationPolicy(reply_to_compliance_keywords=True)
        assert "unsubscribed" in decide(session(), "STOP", now, policy=policy).reply
        assert SITE.support_email in decide(session(), "HELP", now, policy=policy).reply
        assert decide(session(), "HELP", now).reply is None

    def test_start_opts_back_in(self, now):
        assert decide(session(st.STATE_OPTED_OUT), "START", now).effect is Effect.OPT_IN

    def test_stop_works_even_with_no_session(self, now):
        assert decide(None, "STOP", now).effect is Effect.OPT_OUT


class TestRedFlags:
    def test_beat_everything_else(self, now):
        d = decide(session(st.STATE_AWAITING_TIME), "chest pain since 3pm", now)
        assert "911" in d.reply
        assert d.state == st.STATE_CLOSED

    def test_say_plainly_that_nobody_is_watching(self, now):
        assert "not monitored" in decide(session(), "I can't breathe", now).reply.lower()

    def test_a_red_flag_never_books_a_callback(self, now):
        assert decide(session(), "chest pain, call me tomorrow at 3", now).when is None

    def test_a_red_flag_cancels_calls_already_queued(self, now):
        d = decide(session(st.STATE_SCHEDULED), "I can't breathe", now)
        assert d.effect is Effect.CANCEL_ALL

    def test_a_denial_does_not_cancel_a_booking(self, now):
        # Review C-6: "no chest pain" used to CANCEL_ALL a booked callback.
        d = decide(session(st.STATE_SCHEDULED), "no chest pain, feeling fine", now)
        assert d.effect is not Effect.CANCEL_ALL
        assert "911" not in (d.reply or "")


class TestUnknownSender:
    def test_silence(self, now):
        d = decide(None, "hello?", now)
        assert d.reply is None and d.effect is Effect.NONE


class TestChoice:
    def test_one_dispatches_a_recall(self, now):
        d = decide(session(), "1", now)
        assert d.effect is Effect.DISPATCH_RECALL
        assert d.state == st.STATE_RECALL_DISPATCHED
        assert SITE.voice_number in d.reply

    def test_two_asks_for_a_time(self, now):
        d = decide(session(), "2", now)
        assert d.state == st.STATE_AWAITING_TIME
        assert d.effect is Effect.NONE

    def test_a_time_instead_of_a_number_books_immediately(self, now):
        d = decide(session(), "tomorrow 3pm", now)
        assert d.effect is Effect.BOOK
        assert "Thu Aug 13, 3:00 PM" in d.reply

    def test_a_glued_meridiem_is_a_time_not_a_menu_option(self, now):
        # "3pm" was read as option 3 because the time-signal regex needed a
        # word boundary between the digit and "pm".
        d = decide(session(), "3pm", now)
        assert d.effect is Effect.BOOK
        assert "3:00 PM" in d.reply

    def test_unclear_asks_once_then_lets_go(self, now):
        first = decide(session(retry_count=0), "huh?", now)
        assert first.retry == 1 and first.state is None
        second = decide(session(retry_count=1), "huh?", now)
        assert second.state == st.STATE_CLOSED

    def test_the_reask_offers_only_what_decide_honours(self, now):
        assert "3 =" not in decide(session(), "huh?", now).reply
        assert "3 =" in decide(session(), "huh?", now, policy=SURVEY_ON).reply
        # Even if the site copy claims the survey is on, the policy decides.
        site = SiteCopy("Acme Health", "support@example.org", survey_enabled=True)
        d = conversation.decide(session(), "huh?", now, site=site)
        assert "3 =" not in d.reply

    def test_option_three_is_unclear_when_the_survey_is_off(self, now):
        d = decide(session(), "3", now)
        assert d.state is None and d.retry == 1


class TestScheduling:
    def test_a_readable_time_books_and_reads_it_back(self, now):
        d = decide(session(st.STATE_AWAITING_TIME), "thursday at 3", now)
        assert d.effect is Effect.BOOK
        assert d.state == st.STATE_SCHEDULED
        assert "Thu Aug 13, 3:00 PM" in d.reply
        assert "Reply C to cancel" in d.reply

    def test_a_clamped_time_says_so(self, now):
        assert (
            "closest we can do"
            in decide(session(st.STATE_AWAITING_TIME), "tomorrow 6am", now).reply
        )

    def test_unreadable_asks_once_then_lets_go(self, now):
        first = decide(session(st.STATE_AWAITING_TIME, retry_count=0), "whenever", now)
        assert first.retry == 1
        second = decide(session(st.STATE_AWAITING_TIME, retry_count=1), "whenever", now)
        assert second.state == st.STATE_CLOSED
        assert second.effect is Effect.NONE

    def test_c_stops_scheduling(self, now):
        d = decide(session(st.STATE_AWAITING_TIME), "C", now)
        assert d.state == st.STATE_CLOSED and d.effect is Effect.NONE

    @pytest.mark.parametrize("body", ["2", "2 thanks", "1", "5", "at 3"])
    def test_a_bare_digit_is_not_booked_as_a_clock_time(self, body, now):
        # Review C-7 pin: a redelivered "2" (the menu reply that moved the
        # patient here) booked tomorrow 2:00 PM and confirmed it to them.
        d = decide(session(st.STATE_AWAITING_TIME), body, now)
        assert d.effect is Effect.NONE
        assert d.when is None
        assert d.retry == 1

    @pytest.mark.parametrize(
        "body,hour", [("2pm", 14), ("tomorrow at 2", 14), ("at 2 pm", 14), ("in an hour", 15)]
    )
    def test_a_digit_with_a_time_signal_still_books(self, body, hour, now):
        d = decide(session(st.STATE_AWAITING_TIME), body, now)
        assert d.effect is Effect.BOOK and d.when.hour == hour

    def test_the_scheduling_policy_is_injected(self, now):
        from voa_core.timeparse import SchedulingPolicy

        d = decide(
            session(st.STATE_AWAITING_TIME),
            "tomorrow 9am",
            now,
            scheduling=SchedulingPolicy(quiet_start=10),
        )
        assert d.when.hour == 10 and "closest we can do" in d.reply


class TestBooked:
    def test_c_cancels(self, now):
        d = decide(session(st.STATE_SCHEDULED), "C", now)
        assert d.effect is Effect.CANCEL_BOOKING
        assert d.state == st.STATE_CLOSED
        assert SITE.support_email in d.reply

    def test_a_new_time_rebooks(self, now):
        d = decide(session(st.STATE_SCHEDULED), "make it friday 2pm", now)
        assert d.effect is Effect.REBOOK
        assert "Fri Aug 14, 2:00 PM" in d.reply

    def test_a_stray_menu_digit_does_not_rebook(self, now):
        d = decide(session(st.STATE_SCHEDULED, scheduled_for_text="Thu Aug 13, 3:00 PM"), "1", now)
        assert d.effect is Effect.NONE

    def test_chatter_repeats_the_booking_without_rebooking(self, now):
        d = decide(
            session(st.STATE_SCHEDULED, scheduled_for_text="Thu Aug 13, 3:00 PM"),
            "ok thanks",
            now,
        )
        assert d.effect is Effect.NONE
        assert "Thu Aug 13, 3:00 PM" in d.reply


class TestClosedSessions:
    @pytest.mark.parametrize("state", [st.STATE_CLOSED, st.STATE_EXPIRED])
    def test_polite_dead_end(self, state, now):
        d = decide(session(state), "hello", now)
        assert d.effect is Effect.NONE
        assert "911" in d.reply

    def test_cancel_still_works_after_the_session_expires(self, now):
        # A 48h session TTL against a 14-day booking horizon: "C" on day three
        # must still cancel, or the call goes out anyway.
        d = decide(session(st.STATE_EXPIRED, callback_id="cb1"), "C", now)
        assert d.effect is Effect.CANCEL_BOOKING

    def test_opted_out_sessions_stay_silent(self, now):
        assert decide(session(st.STATE_OPTED_OUT), "hello", now).reply is None

    def test_a_dial_in_flight_stays_silent(self, now):
        assert decide(session(st.STATE_DIALING), "hello", now).reply is None

    def test_a_recall_in_flight_does_not_double_confirm(self, now):
        assert decide(session(st.STATE_RECALL_DISPATCHED), "thanks", now).reply is None

    def test_but_a_new_time_still_books(self, now):
        d = decide(session(st.STATE_RECALL_DISPATCHED), "actually friday 2pm", now)
        assert d.effect is Effect.BOOK

    def test_an_unknown_state_is_not_a_crash(self, now):
        assert decide(session("mystery"), "hi", now).note == "unhandled-state:mystery"


# ---------------------------------------------------------------------------
# The questionnaire by text
# ---------------------------------------------------------------------------

NOW = datetime.datetime(2026, 8, 17, 10, 0, tzinfo=datetime.UTC)
DOB = "1960-06-15"


def _ctx(**over):
    ctx = {
        "patient_name": "Jordan Avery",
        "patient_id": "PT-1",
        "patient_dob": DOB,
        "is_diabetic": False,
    }
    ctx.update(over)
    return ctx


def _session(state, **over):
    s = {"state": state, "patient_context": _ctx(), "call_sid": "CA1", "retry_count": 0}
    s.update(over)
    return s


def _decide(s, body, **kw):
    return decide(s, body, NOW, **kw)


def _survey_session(plan=None, index=0, pending=None, answers=None, **over):
    return _session(
        st.STATE_SURVEY,
        survey_plan=plan or survey.build_plan(_ctx()),
        survey_index=index,
        survey_pending=pending or [],
        survey_answers=answers or {},
        **over,
    )


def test_option_three_asks_for_the_dob_first():
    d = _decide(_session(st.STATE_AWAITING_CHOICE), "3", policy=SURVEY_ON)
    assert d.state == st.STATE_AWAITING_DOB
    for word in ("pain", "fever", "incision", "procedure", "surgery"):
        assert word not in d.reply.lower()


@pytest.mark.parametrize("missing", ["patient_dob", "patient_id"])
def test_option_three_falls_back_when_we_cannot_finish(missing):
    s = _session(st.STATE_AWAITING_CHOICE)
    s["patient_context"] = _ctx(**{missing: ""})
    assert _decide(s, "3", policy=SURVEY_ON).state == st.STATE_AWAITING_TIME


def test_verified_dob_sends_the_preamble_and_question_one():
    d = _decide(_session(st.STATE_AWAITING_DOB), "06/15/1960")
    assert d.state == st.STATE_SURVEY
    assert d.effect is Effect.START_SURVEY
    assert d.session_patch["survey_plan"][0] == "overall_feeling"
    assert d.session_patch["dob_verified"] is True
    assert d.extra_replies and d.extra_replies[0].startswith("(1/4)")


def test_a_wrong_date_never_says_it_was_wrong():
    d = _decide(_session(st.STATE_AWAITING_DOB), "01/01/1970")
    assert d.session_patch["dob_attempts"] == 1
    for leak in ("match", "not the date", "incorrect", "wrong"):
        assert leak not in d.reply.lower()


def test_two_wrong_dates_close_as_wrong_person_not_urgent():
    d = _decide(_session(st.STATE_AWAITING_DOB, dob_attempts=1), "01/01/1970")
    assert d.state == st.STATE_CLOSED
    assert d.effect is Effect.SUBMIT_SURVEY
    assert d.session_patch["survey_outcome"] == "unable_to_reach_wrong_person"
    assert d.session_patch["survey_follow_up"] == survey.FOLLOW_UP_NONE


@pytest.mark.parametrize(
    "body,expected_state",
    [("1", st.STATE_RECALL_DISPATCHED), ("2", st.STATE_AWAITING_TIME)],
)
def test_changing_your_mind_at_the_gate_is_not_an_identity_failure(body, expected_state):
    d = _decide(_session(st.STATE_AWAITING_DOB), body)
    assert d.state == expected_state
    assert not (d.session_patch or {}).get("dob_attempts")


def test_c_at_the_gate_declines_the_survey():
    d = _decide(_session(st.STATE_AWAITING_DOB), "C")
    assert d.state == st.STATE_CLOSED and d.effect is Effect.NONE


@pytest.mark.parametrize("text", ["June 15", "08/20", "Aug 20"])
def test_a_yearless_birthday_is_not_read_as_a_callback_time(text):
    d = _decide(_session(st.STATE_AWAITING_DOB), text)
    assert d.state != st.STATE_SCHEDULED
    assert d.effect is not Effect.BOOK


def test_an_explicit_time_at_the_gate_still_books():
    assert _decide(_session(st.STATE_AWAITING_DOB), "tomorrow at 3pm").effect is Effect.BOOK


def test_unreadable_dob_does_not_loop_forever():
    d = _decide(_session(st.STATE_AWAITING_DOB, dob_unreadable=2), "why do you need this?")
    assert d.state == st.STATE_CLOSED
    assert (d.session_patch or {}).get("survey_outcome") is None


def test_an_unreadable_dob_counts_separately_from_a_mismatch():
    d = _decide(_session(st.STATE_AWAITING_DOB), "why do you need this?")
    assert d.session_patch == {"dob_unreadable": 1}


def test_answers_advance_and_are_recorded():
    d = _decide(_survey_session(), "pretty good actually")
    assert d.session_patch["survey_answers"]["overall_feeling"] == "pretty good actually"
    assert d.session_patch["survey_index"] == 1
    assert d.reply.startswith("(2/4)")


def test_pain_yes_queues_three_unnumbered_followups():
    d = _decide(_survey_session(index=1), "yes")
    assert d.session_patch["survey_pending"] == ["pain_severity", "pain_location", "pain_trend"]
    assert not d.reply.startswith("(")


def test_not_sure_on_the_pain_question_is_reasked_not_a_yes():
    # Review C-6 pin, end to end: "not sure" used to open three follow-ups.
    d = _decide(_survey_session(index=1), "not sure")
    assert d.retry == 1 and d.session_patch is None


def test_completion_does_not_send_the_closing_itself():
    d = _decide(
        _survey_session(
            index=3,
            answers={"overall_feeling": "fine", "pain_present": "no", "fever_reported": "no"},
        ),
        "no",
    )
    assert d.effect is Effect.SUBMIT_SURVEY
    assert d.reply is None
    assert d.session_patch["survey_complete"] is True
    assert d.session_patch["survey_follow_up"] == survey.FOLLOW_UP_NONE


def test_a_strict_field_stays_on_the_question():
    s = _survey_session(
        index=2, pending=["pain_severity"], retry_count=1, answers={"pain_present": "yes"}
    )
    d = _decide(s, "no idea honestly")
    assert d.session_patch is None
    assert "0 to 10" in d.reply


def test_a_strict_field_does_not_loop_forever():
    s = _survey_session(
        index=2,
        pending=["pain_severity"],
        retry_count=survey.MAX_STRICT_RETRIES,
        answers={"pain_present": "yes"},
    )
    d = _decide(s, "I really can't say")
    assert d.state == st.STATE_CLOSED
    assert d.effect is Effect.SUBMIT_SURVEY
    assert d.session_patch["survey_complete"] is False


def test_an_unknownable_field_records_unknown_and_moves_on():
    s = _survey_session(
        index=2, retry_count=1, answers={"overall_feeling": "ok", "pain_present": "no"}
    )
    d = _decide(s, "what?")
    assert d.session_patch["survey_answers"]["fever_reported"] == "unknown"


def test_c_stops_without_opting_out():
    d = _decide(_survey_session(index=2, answers={"pain_present": "no"}), "C")
    assert d.state == st.STATE_CLOSED
    assert d.effect is not Effect.OPT_OUT


def test_c_still_files_when_something_urgent_is_already_known():
    d = _decide(_survey_session(index=3, answers={"fever_reported": "yes"}), "C")
    assert d.effect is Effect.SUBMIT_SURVEY


def test_c_files_nothing_when_nothing_is_concerning():
    d = _decide(_survey_session(index=2, answers={"pain_present": "no"}), "C")
    assert d.effect is Effect.NONE


@pytest.mark.parametrize("state", [st.STATE_AWAITING_DOB, st.STATE_SURVEY])
def test_a_red_flag_mid_survey_is_filed_not_just_answered(state):
    s = (
        _survey_session(answers={"pain_present": "yes"})
        if state == st.STATE_SURVEY
        else _session(st.STATE_AWAITING_DOB)
    )
    d = _decide(s, "I'm having chest pain")
    assert d.effect is Effect.FILE_EMERGENCY
    assert "911" in d.reply
    assert d.state == st.STATE_CLOSED


@pytest.mark.parametrize("state", [st.STATE_AWAITING_DOB, st.STATE_SURVEY])
def test_stop_still_works_from_every_survey_state(state):
    assert _decide(_session(state), "STOP").effect is Effect.OPT_OUT


def test_a_missing_plan_does_not_go_silent():
    d = _decide(_session(st.STATE_SURVEY, survey_plan=[]), "hello?")
    assert d.reply
    assert d.state == st.STATE_CLOSED


def test_a_finished_survey_does_not_crash_on_the_next_text():
    plan = survey.build_plan(_ctx())
    s = _survey_session(
        plan=plan, index=len(plan), survey_complete=True, answers={"pain_present": "no"}
    )
    for text in ("thanks", "done", "ok", "great"):
        assert _decide(s, text).reply is None


def test_a_red_flag_after_completion_still_reaches_911():
    plan = survey.build_plan(_ctx())
    s = _survey_session(plan=plan, index=len(plan), survey_complete=True)
    d = _decide(s, "I have chest pain now")
    assert "911" in d.reply
    assert d.effect is Effect.FILE_EMERGENCY


def test_survey_states_are_live_so_they_expire():
    for s in (st.STATE_AWAITING_DOB, st.STATE_SURVEY):
        assert s in st.LIVE_STATES
        assert s in st.SURVEY_STATES


# --- The submission contract -------------------------------------------------


def _filing_would_accept(survey_responses: dict) -> tuple[bool, str]:
    """Mirror of the filing endpoint's independent validation of a completed
    questionnaire. Kept as an explicit mirror so this fails loudly if the two
    ever disagree."""
    for f in ("overall_feeling", "pain_present", "fever_reported", "incision_concerns"):
        if f not in survey_responses:
            return False, f"missing {f}"
    if survey_responses.get("pain_present") == "yes":
        for f in ("pain_severity", "pain_location", "pain_trend"):
            if f not in survey_responses:
                return False, f"missing {f}"
        sev = survey_responses.get("pain_severity")
        if not isinstance(sev, int) or not (0 <= sev <= 10):
            return False, "pain_severity must be int 0-10"
    if survey_responses.get("incision_concerns") == "yes":
        if not survey_responses.get("incision_description"):
            return False, "missing incision_description"
    return True, ""


def _run_survey(replies, ctx=None):
    """Drive a whole questionnaire and return the session and final decision."""
    ctx = ctx or _ctx()
    plan = survey.build_plan(ctx)
    s = _session(
        st.STATE_SURVEY, survey_plan=plan, survey_index=0, survey_pending=[], survey_answers={}
    )
    s["patient_context"] = ctx
    for body in replies:
        d = _decide(s, body)
        if d.session_patch:
            s.update(d.session_patch)
        if d.retry is not None:
            s["retry_count"] = d.retry
        if d.effect is Effect.SUBMIT_SURVEY:
            return s, d
    return s, None


def test_a_clean_run_produces_a_fileable_report():
    s, d = _run_survey(["doing ok", "no", "no", "no"])
    assert d is not None
    ok, why = _filing_would_accept(s["survey_answers"])
    assert ok, why


def test_a_positive_pain_run_produces_a_fileable_report():
    s, _ = _run_survey(["sore", "yes", "8", "left knee", "worse", "no", "no"])
    answers = s["survey_answers"]
    ok, why = _filing_would_accept(answers)
    assert ok, why
    assert answers["pain_severity"] == 8 and isinstance(answers["pain_severity"], int)
    assert answers["pain_location"] == "left knee"
    assert answers["pain_trend"] == "worse"
    assert s["survey_follow_up"] == survey.FOLLOW_UP_URGENT


def test_an_incision_yes_run_collects_the_required_description():
    s, _ = _run_survey(["ok", "no", "no", "yes", "red and weeping"])
    ok, why = _filing_would_accept(s["survey_answers"])
    assert ok, why


def test_a_diabetic_run_is_fileable():
    s, _ = _run_survey(["ok", "no", "no", "no", "in range"], ctx=_ctx(is_diabetic=True))
    assert s["survey_answers"]["blood_sugar_status"] == "in_range"
    ok, why = _filing_would_accept(s["survey_answers"])
    assert ok, why


def test_a_diabetic_typing_a_reading_is_triaged():
    # Review S-10 pin, end to end: "250" used to be re-asked, then "unknown".
    s, _ = _run_survey(["ok", "no", "no", "no", "250"], ctx=_ctx(is_diabetic=True))
    assert s["survey_answers"]["blood_sugar_status"] == "high"
    assert s["survey_follow_up"] == survey.FOLLOW_UP_URGENT


def test_escalation_prose_survives_a_yes_no_parser():
    s, _ = _run_survey(
        ["ok", "no", "no", "No, but I'm really scared, please have a doctor call me"]
    )
    assert survey.triage(s["survey_answers"]).follow_up == survey.FOLLOW_UP_URGENT


def test_raw_answers_are_keyed_by_field():
    s, _ = _run_survey(["doing ok", "no", "yes", "no"])
    raw = survey.raw_answers(s["survey_answers"])
    assert raw["overall_feeling"] == "doing ok"
    assert raw["fever_reported"] == "yes"


def test_a_huge_digit_string_does_not_raise_in_any_state(now):
    # Review S-10: an exception here is a 500 the carrier retries in a loop.
    body = "9" * 5000
    for state in (
        st.STATE_AWAITING_CHOICE,
        st.STATE_AWAITING_TIME,
        st.STATE_SCHEDULED,
        st.STATE_RECALL_DISPATCHED,
        st.STATE_AWAITING_DOB,
    ):
        decide(session(state), body, now)
    _decide(_survey_session(index=1, pending=["pain_severity"]), body)
    _decide(_survey_session(plan=survey.build_plan(_ctx(is_diabetic=True)), index=4), body)
