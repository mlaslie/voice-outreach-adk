"""Date-of-birth parsing: unparseable is never a mismatch."""

import datetime

import pytest
from voa_core.dob import DobCheck, DobResult, check_dob, matches, parse_dob, speakable

TODAY = datetime.date(2026, 8, 17)


@pytest.mark.parametrize(
    "spoken",
    [
        "March 15th, 1950",
        "march 15 1950",
        "3/15/1950",
        "1950-03-15",
        "15 March 1950",
        "it's 3/15/1950",  # a voice transcript, with words around the date
        "3 15 1950",
        "Mar 15 1950.",
    ],
)
def test_spoken_forms_match(spoken):
    assert check_dob(spoken, "1950-03-15").result is DobResult.MATCH


@pytest.mark.parametrize(
    "text",
    [
        "06/15/1960",
        "6/15/1960",
        "06-15-1960",
        "1960-06-15",
        "06151960",
        "June 15 1960",
        "15 June 1960",
        "6.15.1960",
    ],
)
def test_typed_formats(text):
    assert parse_dob(text, TODAY) == datetime.date(1960, 6, 15)


def test_two_digit_year_expands_to_a_plausible_adult():
    assert parse_dob("06/15/60", TODAY) == datetime.date(1960, 6, 15)
    assert parse_dob("061560", TODAY) == datetime.date(1960, 6, 15)


def test_two_digit_year_can_be_this_century():
    assert parse_dob("06/15/10", TODAY) == datetime.date(2010, 6, 15)


@pytest.mark.parametrize("text", ["1", "2", "call me", "who is this", "", "tomorrow", None])
def test_non_dates_are_unparseable_not_wrong(text):
    """The distinction that stops a patient becoming an identity-fraud case."""
    assert parse_dob(text, TODAY) is None


@pytest.mark.parametrize("text", ["March 15th", "June 15", "08/20", "Aug 20"])
def test_a_birthday_without_a_year_is_unparseable(text):
    # Re-ask; never count it as a failed attempt.
    assert parse_dob(text, TODAY) is None
    assert check_dob(text, "1950-03-15").result is DobResult.UNPARSEABLE


def test_day_first_input_is_not_swapped():
    # Month-first only, as the source module did. "15/03/1950" has no month 15.
    assert parse_dob("15/03/1950", TODAY) is None


def test_impossible_dates_are_unparseable():
    assert parse_dob("February 30 1950", TODAY) is None
    assert parse_dob("13/01/1950", TODAY) is None


def test_a_future_date_is_not_a_birthdate():
    assert parse_dob("06/15/2030", TODAY) is None


def test_an_impossibly_old_date_is_not_a_birthdate():
    assert parse_dob("06/15/1890", TODAY) is None


def test_a_huge_digit_string_does_not_raise():
    # Review S-10: int() on >4300 digits raises ValueError -> a 500 the carrier retries.
    assert parse_dob("9" * 5000, TODAY) is None
    assert parse_dob("march " + "9" * 5000 + " 1950", TODAY) is None


def test_match_and_mismatch():
    d = datetime.date(1960, 6, 15)
    assert matches(d, "1960-06-15")
    assert matches(d, "1960-06-15T00:00:00")
    assert not matches(d, "1961-06-15")
    assert not matches(d, "")  # cannot verify => must not claim to
    assert not matches(d, None)
    assert not matches(d, "not a date")


def test_check_dob_results():
    assert check_dob("June 2nd 1961", "1950-03-15") == DobCheck(
        DobResult.MISMATCH, datetime.date(1961, 6, 2)
    )
    assert check_dob("gibberish", "1950-03-15") == DobCheck(DobResult.UNPARSEABLE, None)
    # A malformed date on file can never verify.
    assert check_dob("3/15/1950", "").result is DobResult.MISMATCH


def test_result_values_are_stable_strings():
    assert [r.value for r in DobResult] == ["match", "mismatch", "unparseable"]


def test_speakable():
    assert speakable(datetime.date(1950, 3, 15)) == "March 15, 1950"
    assert speakable(datetime.date(1961, 6, 2)) == "June 2, 1961"
