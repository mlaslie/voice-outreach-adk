"""Analytics numbers against a hand-computed fictional dataset (see analytics_fixtures)."""

from __future__ import annotations

from datetime import date

import pytest
from analytics_fixtures import CALLS, END, QUESTIONS, REASONS, START, TODAY
from voa_analytics import metrics


def summary(channel="all", calls=CALLS):
    return metrics.compute(
        calls, REASONS, QUESTIONS, start_day=START, end_day=END, channel=channel, today=TODAY
    )


def test_range():
    assert summary()["range"] == {
        "start_day": "2026-10-12",
        "end_day": "2026-10-14",
        "channel": "all",
        "calls": 7,
    }


def test_outreach():
    o = summary()["outreach"]
    assert o["patients_attempted"] == 5  # A, B, C, D, entry e7
    assert o["patients_reached"] == 3  # A, B, D
    assert o["reach_rate"] == 0.6
    assert o["voice_funnel"] == {
        "dialled": 7,
        "connected": 5,  # c2 c3 c4 c5 c6
        "human": 4,  # minus the machine c3
        "verified": 2,  # c2 c6 (c4 was finished by text, c5 mismatch)
        "completed": 2,
    }
    assert o["text_funnel"] == {"invites_sent": 1, "completed_by_text": 1}
    assert o["attempts_per_reached"] == round((2 + 2 + 1) / 3, 2)
    assert o["unreached"] == [
        {
            "patient_key": None,
            "entry_id": "e7",
            "attempts": 1,
            "last_call_id": "c7",
            "last_at": "2026-10-14T14:00:00+00:00",
        },
        {
            "patient_key": "kC",
            "entry_id": "eC",
            "attempts": 1,
            "last_call_id": "c5",
            "last_at": "2026-10-13T14:00:00+00:00",
        },
    ]


def test_clinical():
    c = summary()["clinical"]
    assert c["tiers_by_day"] == [
        {
            "day": "2026-10-12",
            "emergency": 0,
            "urgent_same_day": 0,
            "routine_next_business_day": 1,
            "informational": 0,
            "none": 1,
        },
        {
            "day": "2026-10-13",
            "emergency": 0,
            "urgent_same_day": 2,
            "routine_next_business_day": 0,
            "informational": 0,
            "none": 0,
        },
        {
            "day": "2026-10-14",
            "emergency": 1,
            "urgent_same_day": 0,
            "routine_next_business_day": 0,
            "informational": 0,
            "none": 0,
        },
    ]
    assert c["top_reasons"] == [
        {"text": "fever reported", "tier": "urgent_same_day", "count": 2},  # c8 out of range
        {"text": "not sleeping well", "tier": "informational", "count": 1},
        {"text": "pain 5/10", "tier": "routine_next_business_day", "count": 1},
        {"text": "pain 8/10", "tier": "urgent_same_day", "count": 1},
    ]
    assert c["questions"] == {
        "by_category": {"medication": 2, "appointment": 1},
        "by_priority": {"urgent_same_day": 2, "routine_next_business_day": 1},
    }
    # completed: c2, c4, c6
    assert c["symptoms"] == {
        "fever_yes": 1,
        "incision_yes": 1,
        "swelling_yes": 2,
        "swelling_urgent": 1,
        "poor_sleep": 1,
        "sugar_high": 1,
        "sugar_low": 1,
        "pain_0": 1,
        "pain_1_3": 0,
        "pain_4_7": 1,
        "pain_8_10": 1,
    }
    assert c["emergencies"] == ["c6"]


def test_workload():
    w = summary()["workload"]
    # open: c4 routine (not reviewed), c6 emergency; c2 and c5 signed off
    assert w["open_by_tier"] == {
        "emergency": 1,
        "urgent_same_day": 0,
        "routine_next_business_day": 1,
    }
    # c4 routine from Mon 10-12: next business day Tue 10-13 < Thu -> overdue.
    # c6 emergency from Wed 10-14 < Thu -> overdue.
    assert w["overdue_by_tier"] == {
        "emergency": 1,
        "urgent_same_day": 0,
        "routine_next_business_day": 1,
    }
    assert w["signoff_minutes"] == {
        "emergency": {"median": None, "p90": None, "n": 0},
        "urgent_same_day": {"median": 20.0, "p90": 30.0, "n": 2},
        "routine_next_business_day": {"median": None, "p90": None, "n": 0},
    }
    # reviewed: c2 (overridden, signed), c5 (signed), c6 (overridden) -> 2/3
    assert w["override"] == {"rate": 0.6667, "up": 1, "down": 1, "n": 3}


def test_channel_filter_sms():
    s = summary("sms")
    assert s["range"]["calls"] == 1
    assert s["outreach"]["patients_reached"] == 1
    assert s["outreach"]["reach_rate"] == 1.0
    assert s["clinical"]["questions"]["by_category"] == {"appointment": 1}
    assert s["clinical"]["top_reasons"] == [
        {"text": "pain 5/10", "tier": "routine_next_business_day", "count": 1}
    ]


def test_empty_range_has_null_rates():
    s = summary(calls=[])
    o, w = s["outreach"], s["workload"]
    assert o["patients_attempted"] == 0 and o["reach_rate"] is None
    assert o["attempts_per_reached"] is None and o["unreached"] == []
    assert w["override"] == {"rate": None, "up": 0, "down": 0, "n": 0}
    assert len(s["clinical"]["tiers_by_day"]) == 3  # zero-filled days


def test_dates_as_date_objects():
    rows = [{**c, "day": date.fromisoformat(c["day"])} for c in CALLS]
    assert summary(calls=rows)["range"]["calls"] == 7


def test_nearest_rank():
    v = list(range(1, 11))
    assert metrics.nearest_rank(v, 0.9) == 9
    assert metrics.nearest_rank([5.0], 0.9) == 5.0
    assert metrics.nearest_rank([], 0.9) is None


def test_bad_arguments():
    with pytest.raises(ValueError):
        summary("fax")
    with pytest.raises(ValueError):
        metrics.compute([], [], [], start_day=END, end_day=START)
