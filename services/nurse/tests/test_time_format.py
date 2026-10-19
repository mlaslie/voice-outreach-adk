"""Nurse-facing times follow the clinic's format (dashboard Settings)."""

import asyncio
from datetime import datetime

import pytest
from voa_nurse import cards, worklists
from voa_nurse.backend import BackendError


@pytest.fixture(autouse=True)
def _reset():
    cards.set_time_format("24h")
    worklists._format_checked_at = -worklists._FORMAT_TTL_S
    yield
    cards.set_time_format("24h")
    worklists._format_checked_at = -worklists._FORMAT_TTL_S


def test_card_times_follow_the_format():
    dt = datetime(2026, 3, 3, 14, 5, tzinfo=cards.TZ)
    assert cards.when(dt.isoformat(), with_date=False) == "14:05"
    cards.set_time_format("12h")
    assert cards.when(dt.isoformat(), with_date=False) == "2:05 PM"


def test_calling_hours_text_follows_the_format():
    hours = {"window_start": "09:00", "window_end": "17:00", "days": [0, 1]}
    assert worklists.hours_text(hours) == "Mon, Tue, 09:00–17:00"
    cards.set_time_format("12h")
    assert worklists.hours_text(hours) == "Mon, Tue, 9:00 AM–5:00 PM"


class _Api:
    def __init__(self, result):
        self.result, self.calls = result, 0

    async def get_call_settings(self):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_refresh_follows_settings_at_most_once_a_minute():
    api = _Api({"display": {"time_format": "12h"}})
    asyncio.run(worklists.refresh_time_format(api))
    assert cards.TIME_FORMAT == "12h"
    api.result = {"display": {"time_format": "24h"}}
    asyncio.run(worklists.refresh_time_format(api))
    assert api.calls == 1 and cards.TIME_FORMAT == "12h"  # cached


def test_refresh_keeps_the_last_format_on_errors_and_bad_values():
    cards.set_time_format("12h")
    asyncio.run(worklists.refresh_time_format(_Api(BackendError("down"))))
    assert cards.TIME_FORMAT == "12h"
    worklists._format_checked_at = -worklists._FORMAT_TTL_S
    asyncio.run(worklists.refresh_time_format(_Api({"display": {"time_format": "x"}})))
    assert cards.TIME_FORMAT == "12h"
