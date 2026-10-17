"""Reading a patient's SMS reply: compliance keywords, red flags, menu choice."""

import pytest
from voa_core.intent import (
    Intent,
    classify_choice,
    classify_compliance,
    is_bare_menu_digit,
    is_cancel,
    is_red_flag,
    normalize,
)


class TestCompliance:
    @pytest.mark.parametrize("text", ["STOP", "stop", " Stop. ", "UNSUBSCRIBE", "quit"])
    def test_opt_out_keywords(self, text):
        assert classify_compliance(text) is Intent.STOP

    def test_stop_must_be_the_whole_message(self):
        # Carriers match the whole body. "stop the pain" is a symptom report.
        assert classify_compliance("stop the pain please") is None

    def test_start_and_help(self):
        assert classify_compliance("START") is Intent.START
        assert classify_compliance("help") is Intent.HELP

    def test_ordinary_text_is_not_compliance(self):
        assert classify_compliance("tomorrow at 3") is None


class TestRedFlags:
    @pytest.mark.parametrize(
        "text",
        [
            "I'm having chest pain",
            "i cant breathe",
            "trouble breathing since this morning",
            "the incision won't stop bleeding",
            "I passed out earlier",
            "I think it's a heart attack",
        ],
    )
    def test_detected(self, text):
        assert is_red_flag(text)

    @pytest.mark.parametrize(
        "text", ["1", "tomorrow 3pm", "my pain is a 4", "a bit sore", "some redness"]
    )
    def test_not_over_eager(self, text):
        # A 911 message on every mild symptom would train patients to ignore it.
        assert not is_red_flag(text)

    @pytest.mark.parametrize(
        "text",
        [
            "no chest pain, feeling fine",  # review C-6 pin
            "no bleeding, no chest pain, no trouble breathing",
            "I don't have chest pain",
            "never had trouble breathing",
            "that was a masterstroke",  # review C-6 pin: "stroke" inside another word
        ],
    )
    def test_a_denial_is_not_a_red_flag(self, text):
        """In awaiting_choice a red flag is CANCEL_ALL: it deletes the
        patient's booked callback and closes the session with the 911 text."""
        assert not is_red_flag(text)

    @pytest.mark.parametrize(
        "text",
        [
            "no fever but I can't breathe",  # negation does not cross "but"
            "I did not pass out, but I have chest pain",  # nor a comma
            "not sure if it's chest pain",  # uncertainty still fires
            "I think I had a stroke",
        ],
    )
    def test_a_denial_elsewhere_does_not_mask_a_real_flag(self, text):
        assert is_red_flag(text)

    @pytest.mark.parametrize(
        "text",
        [
            "worst headache of my life",
            "I'm vomiting blood",
            "my calf is swollen",
            "I want to kill myself",
            "chest tightness",
            "pressure in my chest",
            "my chest feels tight",
            "soaking through the gauze",
            "suddenly confused",
            "my arm is weak",
        ],
    )
    def test_voice_emergencies_are_recognised_over_sms(self, text):
        assert is_red_flag(text)


class TestChoice:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("1", Intent.CALL_NOW),
            ("now", Intent.CALL_NOW),
            ("call me", Intent.CALL_NOW),
            ("1 please", Intent.CALL_NOW),
            ("2", Intent.PICK_TIME),
            ("2 thanks", Intent.PICK_TIME),
            ("later", Intent.PICK_TIME),
            ("3", Intent.BY_TEXT),
            ("text", Intent.BY_TEXT),
        ],
    )
    def test_menu(self, text, expected):
        assert classify_choice(text) is expected

    def test_a_bare_time_skips_the_menu(self):
        assert classify_choice("tomorrow at 3pm") is Intent.NAMED_TIME

    def test_a_menu_digit_is_never_read_as_a_clock_time(self):
        assert classify_choice("2") is Intent.PICK_TIME

    @pytest.mark.parametrize("text", ["1 pm", "3 tomorrow", "1pm", "3pm", "2:30"])
    def test_a_leading_digit_with_a_clock_word_is_a_time(self, text):
        # "1 pm" was read as menu option one and dialled immediately. The
        # glued forms "1pm"/"3pm" were missed by the old signal regex (no word
        # boundary between digit and meridiem) and read as menu options too.
        assert classify_choice(text) is Intent.NAMED_TIME

    @pytest.mark.parametrize("text", ["who is this?", "", "what", "ok thanks"])
    def test_unclear(self, text):
        assert classify_choice(text) is Intent.UNCLEAR


class TestCancel:
    def test_c_cancels_a_booking(self):
        assert is_cancel("C")

    def test_bare_cancel_is_left_to_the_opt_out_path(self):
        # "CANCEL" is a carrier opt-out keyword, which is why the
        # confirmation message asks for "C".
        assert not is_cancel("cancel")
        assert classify_compliance("cancel") is Intent.STOP


def test_bare_menu_digits():
    assert all(is_bare_menu_digit(d) for d in ("1", "2", " 3. "))
    assert not is_bare_menu_digit("4")
    assert not is_bare_menu_digit("1 pm")


def test_normalize_strips_punctuation_and_case():
    assert normalize("  Stop! ") == "stop"


def test_a_huge_digit_string_does_not_raise():
    # Review S-10: the webhook must not 500 on adversarial input.
    assert classify_choice("9" * 5000) is Intent.UNCLEAR
    assert not is_red_flag("9" * 5000)
