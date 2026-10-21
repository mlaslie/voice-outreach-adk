"""Pin the nurse tests' clock to 1 pm clinic time today.

The fixtures build "today" relative to now ("a call that ended 3 hours ago"), and the
worklists decide "today" in the clinic's timezone. Run just after midnight, those
calls landed yesterday and the worklist tests failed (seen 21 Oct 2026, 00:07
America/New_York). Every module that reads the clock sees the same pinned time.
"""

from __future__ import annotations

from datetime import datetime

import nurse_fixtures
import pytest
from voa_nurse import actions, agent, cards, worklists


def _pinned() -> datetime:
    return datetime.now(cards.TZ).replace(hour=13, minute=0, second=0, microsecond=0)


class _Clock(datetime):
    PINNED: datetime | None = None

    @classmethod
    def now(cls, tz=None):
        p = cls.PINNED
        return p if tz is None else p.astimezone(tz)


@pytest.fixture(autouse=True)
def _pin_clock(monkeypatch):
    _Clock.PINNED = _pinned()
    for mod in (worklists, actions, cards, agent, nurse_fixtures):
        monkeypatch.setattr(mod, "datetime", _Clock)
    yield
