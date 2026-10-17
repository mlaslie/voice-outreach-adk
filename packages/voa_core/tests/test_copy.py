"""The copy carries most of the privacy properties. Guard it like code."""

import dataclasses

import pytest
from voa_core import copy as messages
from voa_core.copy import SiteCopy

SITE = SiteCopy(
    org_name="Acme Health",
    support_email="support@example.org",
    voice_number="+12025550142",
    survey_enabled=False,
)
SURVEY_SITE = dataclasses.replace(SITE, survey_enabled=True)
BARE_SITE = SiteCopy(org_name="Acme Health", support_email="support@example.org")

# Words that would tell whoever holds the handset that the recipient had a
# medical procedure. A text has no identity gate, and sits on a lock screen.
CLINICAL_WORDS = (
    "procedure",
    "surgery",
    "surgical",
    "post-op",
    "postop",
    "operation",
    "discharge",
    "incision",
    "wound",
    "pain",
    "fever",
    "diabet",
    "recovery",
)

# The GSM 03.38 basic character set (plus the extension table). Anything else
# switches the whole message to UCS-2 and cuts a segment from 160 chars to 70.
GSM7 = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
    "^{}\\[~]|€"
)

# Every factory, with representative arguments. Kept exhaustive on purpose:
# review finding S-10 was that the length/punctuation tests covered 4 of ~25.
ALL_COPY = {
    "invite": lambda: messages.invite("Jordan Avery", SURVEY_SITE),
    "invite_bare": lambda: messages.invite(None, BARE_SITE),
    "unclear_choice": lambda: messages.unclear_choice(SURVEY_SITE),
    "gave_up_choice": messages.gave_up_choice,
    "calling_now": lambda: messages.calling_now(SITE),
    "calling_now_bare": lambda: messages.calling_now(BARE_SITE),
    "cannot_call_back": messages.cannot_call_back,
    "ask_for_time": messages.ask_for_time,
    "unclear_time": messages.unclear_time,
    "gave_up_time": messages.gave_up_time,
    "booked": lambda: messages.booked("Thu Aug 13, 3:00 PM", False, SITE),
    "booked_adjusted": lambda: messages.booked("Thu Aug 13 2027, 3:00 PM", True, SITE),
    "already_booked": lambda: messages.already_booked("Thu Aug 13, 3:00 PM"),
    "booking_canceled": lambda: messages.booking_canceled(SITE),
    "nothing_to_cancel": messages.nothing_to_cancel,
    "emergency": messages.emergency,
    "opted_out": lambda: messages.opted_out(SITE),
    "help_message": lambda: messages.help_message(SITE),
    "session_closed": lambda: messages.session_closed(SITE),
    "ask_dob": lambda: messages.ask_dob("Jordan Avery"),
    "dob_unreadable": messages.dob_unreadable,
    "dob_failed": messages.dob_failed,
    "survey_preamble": lambda: messages.survey_preamble(5),
    "unclear_answer": lambda: messages.unclear_answer(messages.FOLLOWUP_TEXT["pain_severity"]),
    "survey_stopped": messages.survey_stopped,
    "survey_cut_short": messages.survey_cut_short,
    "survey_done_routine": messages.survey_done_routine,
    "survey_done_urgent": messages.survey_done_urgent,
    "survey_file_failed": messages.survey_file_failed,
    **{
        f"question:{k}": (lambda k=k: messages.question(5, 5, messages.QUESTION_TEXT[k]))
        for k in messages.QUESTION_TEXT
    },
    **{f"followup:{k}": (lambda k=k: messages.FOLLOWUP_TEXT[k]) for k in messages.FOLLOWUP_TEXT},
}

# Sent before identity is verified, or to an unverified handset.
PRE_VERIFICATION = (
    "invite",
    "invite_bare",
    "unclear_choice",
    "gave_up_choice",
    "calling_now",
    "calling_now_bare",
    "cannot_call_back",
    "ask_for_time",
    "unclear_time",
    "gave_up_time",
    "booked",
    "booked_adjusted",
    "already_booked",
    "booking_canceled",
    "nothing_to_cancel",
    "opted_out",
    "help_message",
    "session_closed",
    "ask_dob",
    "dob_unreadable",
    "dob_failed",
)


class TestInvite:
    def test_says_who_it_is_for_without_saying_why(self):
        text = messages.invite("Jordan Avery", SITE)
        assert "Jordan" in text
        assert "Avery" not in text  # a surname is a needless identifier
        for word in CLINICAL_WORDS:
            assert word not in text.lower(), f"invite leaks {word!r}"

    def test_carries_the_opt_out_notice(self):
        assert "Reply STOP to opt out." in messages.invite("Jordan", SITE)

    def test_names_the_sender(self):
        assert SITE.org_name in messages.invite("Jordan", SITE)

    def test_quotes_the_voice_number_so_caller_id_is_recognised(self):
        assert SITE.voice_number in messages.invite("Jordan", SITE)
        assert "Calls come from" not in messages.invite("Jordan", BARE_SITE)

    @pytest.mark.parametrize("site", [SITE, SURVEY_SITE])
    def test_offers_only_the_enabled_options(self, site):
        text = messages.invite("Jordan", site)
        assert "1 = call me now" in text and "2 = pick a time" in text
        assert ("3 =" in text) == site.survey_enabled
        assert ("3 =" in messages.unclear_choice(site)) == site.survey_enabled

    def test_is_laid_out_as_a_menu(self):
        lines = [ln for ln in messages.invite("Jordan", SURVEY_SITE).split("\n") if ln.strip()]
        assert "1 = call me now" in lines
        assert "2 = pick a time" in lines
        assert "3 = answer by text" in lines

    def test_survives_a_missing_name(self):
        assert messages.invite(None, SITE)
        assert messages.invite("", SITE)


class TestFirstName:
    @pytest.mark.parametrize(
        "full,expected",
        [
            ("Jordan Avery", "Jordan"),
            ("  Ana  Maria Cruz ", "Ana"),
            ("Sam", "Sam"),
            ("", "you"),
            (None, "you"),
        ],
    )
    def test_extracts_only_the_first_name(self, full, expected):
        assert messages.first_name(full) == expected


class TestEmergency:
    def test_points_at_911_and_is_tappable(self):
        text = messages.emergency()
        assert "911" in text and "tel:911" in text

    def test_does_not_imply_anyone_is_watching(self):
        assert "not monitored" in messages.emergency().lower()


class TestPromisesNothing:
    def test_the_give_up_messages_schedule_nothing(self):
        for text in (messages.gave_up_choice(), messages.gave_up_time()):
            assert "we'll try you" not in text.lower()
            assert "tomorrow" not in text.lower()

    def test_call_now_does_not_name_a_duration_it_cannot_hold(self):
        assert "minute" not in messages.calling_now(SITE)

    def test_a_failed_dob_never_says_why(self):
        for text in (messages.dob_unreadable(), messages.dob_failed()):
            for leak in ("match", "not the date", "incorrect", "wrong"):
                assert leak not in text.lower()


class TestNoPhiBeforeVerification:
    @pytest.mark.parametrize("name", PRE_VERIFICATION)
    def test_no_clinical_content(self, name):
        text = ALL_COPY[name]().lower()
        for word in CLINICAL_WORDS:
            assert word not in text, f"{word!r} leaked into {name}"


class TestEveryMessage:
    @pytest.mark.parametrize("name", sorted(ALL_COPY))
    def test_is_gsm7(self, name):
        bad = set(ALL_COPY[name]()) - GSM7
        assert not bad, f"{name} contains non-GSM-7 characters {sorted(bad)!r}"

    @pytest.mark.parametrize("name", sorted(ALL_COPY))
    def test_stays_inside_two_segments(self, name):
        # 306 chars is two concatenated GSM-7 segments.
        assert len(ALL_COPY[name]()) <= 306, name

    def test_dob_failed_has_no_em_dash(self):
        # Review S-10 pin: the em dash made this a 3-segment UCS-2 message.
        assert "—" not in messages.dob_failed()
        assert messages.dob_failed().startswith("Thanks - ")


def test_question_numbering():
    assert messages.question(2, 4, "Q?") == "(2/4) Q?"


def test_site_copy_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        SITE.org_name = "x"  # type: ignore[misc]
