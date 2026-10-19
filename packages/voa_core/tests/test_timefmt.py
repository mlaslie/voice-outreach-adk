"""Patients always get 12-hour times; nurses get the clinic's format."""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest
from voa_core import compliance, timefmt, timeparse


@pytest.mark.parametrize(
    ("t", "h12", "h24"),
    [
        (time(0, 5), "12:05 AM", "00:05"),
        (time(9, 0), "9:00 AM", "09:00"),
        (time(12, 0), "12:00 PM", "12:00"),
        (time(14, 30), "2:30 PM", "14:30"),
        (time(23, 59), "11:59 PM", "23:59"),
    ],
)
def test_clock_in_both_formats(t, h12, h24):
    assert timefmt.clock(t, "12h") == h12
    assert timefmt.clock(t, "24h") == h24
    assert timefmt.patient_clock(t) == h12


def test_hhmm_formats_a_stored_window_bound_and_passes_garbage_through():
    assert timefmt.hhmm("17:00", "12h") == "5:00 PM"
    assert timefmt.hhmm("17:00", "24h") == "17:00"
    assert timefmt.hhmm("late", "12h") == "late"


def test_coerce_falls_back_to_the_default():
    assert timefmt.coerce("12h", "24h") == "12h"
    assert timefmt.coerce("military", "24h") == "24h"
    assert timefmt.coerce(None, "12h") == "12h"


def test_patient_readback_is_12_hour_whatever_the_nurse_format():
    when = datetime(2026, 10, 20, 14, 30, tzinfo=ZoneInfo("America/New_York"))
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    assert timeparse.humanize(when, now) == "Tue Oct 20, 2:30 PM"


def test_window_text_follows_the_nurse_format():
    cfg = compliance.RecallSettings(window_start="09:00", window_end="17:30", days=[0, 1])
    assert compliance.window_text(cfg, "America/New_York") == (
        "Mon, Tue, 09:00–17:30 (America/New_York)"
    )
    assert compliance.window_text(cfg, "America/New_York", "12h") == (
        "Mon, Tue, 9:00 AM–5:30 PM (America/New_York)"
    )
