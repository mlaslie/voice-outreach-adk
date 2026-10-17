"""Callback-time parsing: a time is never guessed, and every booking sits in the window."""

import datetime
from zoneinfo import ZoneInfo

import pytest
from voa_core import timeparse
from voa_core.timeparse import SchedulingPolicy

TZ = ZoneInfo("America/New_York")
POLICY = SchedulingPolicy()


@pytest.fixture
def now():
    """A fixed Wednesday afternoon in clinic time: 2026-08-12 14:00."""
    return datetime.datetime(2026, 8, 12, 14, 0, tzinfo=TZ)


def r(text, now):
    return timeparse.resolve(text, now)


class TestParsing:
    def test_tomorrow_with_pm(self, now):
        got = r("tomorrow 3pm", now)
        assert (got.when.month, got.when.day, got.when.hour) == (8, 13, 15)
        assert not got.adjusted

    def test_bare_hour_reads_as_afternoon(self, now):
        # "tomorrow at 3" from someone arranging a clinic callback means 3pm.
        assert r("tomorrow at 3", now).when.hour == 15

    def test_bare_morning_hour_stays_morning(self, now):
        assert r("tomorrow at 9", now).when.hour == 9

    def test_weekday_picks_the_next_one(self, now):
        got = r("thursday morning", now)  # now is a Wednesday
        assert (got.when.day, got.when.hour) == (13, 9)

    def test_weekday_today_rolls_a_week(self, now):
        got = r("wednesday 10am", now)  # today IS Wednesday
        assert got.when.day == 19

    def test_next_weekday_is_a_further_week(self, now):
        assert r("next thursday 10am", now).when.day == 20

    def test_named_dayparts(self, now):
        assert r("tomorrow afternoon", now).when.hour == 14
        assert r("tomorrow evening", now).when.hour == 18
        assert r("tomorrow at noon", now).when.hour == 12

    def test_numeric_date(self, now):
        got = r("8/14 at 11am", now)
        assert (got.when.month, got.when.day, got.when.hour) == (8, 14, 11)

    def test_month_name_date(self, now):
        got = r("aug 15 2pm", now)
        assert (got.when.month, got.when.day, got.when.hour) == (8, 15, 14)

    def test_relative_hours(self, now):
        assert r("in 2 hours", now).when.hour == 16

    def test_relative_an_hour(self, now):
        assert r("in an hour", now).when.hour == 15

    def test_time_only_today_if_still_ahead(self, now):
        got = r("5pm", now)
        assert (got.when.day, got.when.hour) == (12, 17)

    def test_time_only_rolls_to_tomorrow_when_past(self, now):
        got = r("10am", now)  # it is already 2pm
        assert (got.when.day, got.when.hour) == (13, 10)

    def test_day_without_a_time_gets_a_sensible_default(self, now):
        # Safe because the resolved time is read back before it is booked.
        got = r("friday", now)
        assert (got.when.day, got.when.hour) == (14, 10)

    @pytest.mark.parametrize("text", ["", "sure", "ok", "who is this", "yes"])
    def test_unreadable_returns_none(self, text, now):
        assert r(text, now) is None

    def test_a_time_is_never_invented(self, now):
        assert timeparse.parse_when("call me whenever", now) is None

    def test_a_huge_digit_string_does_not_raise(self, now):
        # Review S-10: unbounded \d+ fed to int() raises past ~4300 digits.
        assert r("9" * 5000, now) is None


class TestClamping:
    def test_before_the_window_moves_to_opening(self, now):
        got = r("tomorrow 6am", now)
        assert got.when.hour == 8 and got.adjusted

    def test_after_the_window_moves_to_the_next_morning(self, now):
        got = r("tomorrow 11pm", now)
        assert (got.when.day, got.when.hour) == (14, 8)
        assert got.adjusted

    def test_too_far_out_is_refused(self, now):
        assert r("9/30 2pm", now) is None

    def test_a_past_date_is_refused_not_rolled(self, now):
        assert r("8/1 2pm", now) is None

    def test_earlier_today_is_pushed_forward_not_backward(self, now):
        got = timeparse.clamp(now - datetime.timedelta(hours=3), now)
        assert got.when > now and got.adjusted

    def test_the_last_hour_of_the_window_is_bookable(self, now):
        # quiet_end is the last bookable start time. Treating it as exclusive
        # silently bounced every "8pm" to 8 the next morning.
        got = r("tomorrow 8pm", now)
        assert got.when.hour == POLICY.quiet_end
        assert not got.adjusted

    def test_one_minute_past_it_is_not(self, now):
        got = r("tomorrow 8:30pm", now)
        assert got.adjusted and got.when.hour == POLICY.quiet_start


class TestPolicyIsInjected:
    def test_a_different_window_is_honoured(self, now):
        late = SchedulingPolicy(quiet_start=10, quiet_end=17)
        got = timeparse.resolve("tomorrow 9am", now, late)
        assert got.when.hour == 10 and got.adjusted
        got = timeparse.resolve("tomorrow 6pm", now, late)
        assert (got.when.day, got.when.hour) == (14, 10)

    def test_a_shorter_horizon_is_honoured(self, now):
        assert timeparse.resolve("8/20 2pm", now) is not None
        assert timeparse.resolve("8/20 2pm", now, SchedulingPolicy(max_days=3)) is None

    def test_the_timezone_comes_from_the_policy(self):
        assert timeparse.clinic_tz(SchedulingPolicy(tz="America/Chicago")).key == (
            "America/Chicago"
        )


class TestWeekdays:
    def test_next_weekday_means_the_following_calendar_week(self, now):
        # now is Wed Aug 12; Mon Aug 17 starts the following week.
        assert r("friday 10am", now).when.day == 14
        assert r("next friday 10am", now).when.day == 21

    def test_same_weekday_is_not_forced_two_weeks_out(self, now):
        assert r("next wednesday 10am", now).when.day == 19
        assert r("wednesday 10am", now).when.day == 19


class TestQuietHoursOnInvites:
    def test_an_out_of_hours_invite_waits_for_the_morning(self):
        late = datetime.datetime(2026, 8, 12, 23, 40, tzinfo=TZ)
        assert timeparse.next_send_window(late).hour == POLICY.quiet_start
        assert timeparse.next_send_window(late).day == 13

    def test_an_in_hours_invite_goes_straight_out(self, now):
        assert timeparse.next_send_window(now) == now


class TestYearBoundary:
    def test_january_said_in_december_means_next_year(self):
        december = datetime.datetime(2026, 12, 28, 10, 0, tzinfo=TZ)
        got = timeparse.resolve("jan 5 10am", december)
        assert got is not None
        assert (got.when.year, got.when.month, got.when.day) == (2027, 1, 5)

    def test_but_the_horizon_still_refuses_an_absurd_roll(self):
        september = datetime.datetime(2026, 9, 20, 10, 0, tzinfo=TZ)
        assert timeparse.resolve("aug 1 10am", september) is None

    def test_a_cross_year_booking_reads_its_year_back(self):
        when = datetime.datetime(2027, 1, 5, 10, 0, tzinfo=TZ)
        assert "2027" in timeparse.humanize(when, datetime.datetime(2026, 12, 28, tzinfo=TZ))
        assert "2027" not in timeparse.humanize(when, datetime.datetime(2027, 1, 2, tzinfo=TZ))


class TestHumanize:
    def test_reads_unambiguously(self, now):
        got = r("tomorrow 3pm", now)
        assert timeparse.humanize(got.when, now) == "Thu Aug 13, 3:00 PM"

    def test_noon_and_midnight_are_not_zero_oclock(self, now):
        noon = r("tomorrow at noon", now)
        assert "12:00 PM" in timeparse.humanize(noon.when, now)


class TestExplicitSignal:
    @pytest.mark.parametrize("text", ["2", "2 thanks", "at 3", "5"])
    def test_a_bare_number_has_no_time_signal(self, text):
        # Review C-7: in the time-asking state these must be re-asked, not booked.
        assert timeparse.is_bare_number_without_time_signal(text)

    @pytest.mark.parametrize(
        "text", ["3pm", "tomorrow at 3", "thursday 2", "8/14", "in 2 hours", "10:30", "later"]
    )
    def test_a_clock_or_calendar_word_is_a_signal(self, text):
        assert not timeparse.is_bare_number_without_time_signal(text)
