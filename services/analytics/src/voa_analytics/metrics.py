"""Phase-1 analytics numbers from the fact rows. Pure functions only.

Input rows are dicts shaped like ``schema.FACT_*`` (``day`` as an ISO date string
or a ``date``; timestamps as ISO strings or datetimes). The BigQuery row source
and the tests both feed this, so every number here can be checked by hand.

Definitions (each is a choice; the UI and docs should say the same):

* A **patient** is ``patient_key``; a call with none falls back to its call-list
  entry, then to the call itself.
* **Reached**: the patient has any call (voice or text) with outcome
  ``questionnaire_completed`` in the range.
* **Attempts per reached patient**: the patient's calls up to and including the
  first completed one, averaged over reached patients.
* **Voice funnel** steps are nested: dialled (every call except SMS-simulator
  ones) -> connected (answered) -> human (answered_by not a machine/fax) ->
  verified (DOB verified on the voice call) -> completed (questionnaire completed
  by voice). A call finished by text leaves the voice funnel at "verified" and
  counts in the text funnel instead.
* **Override rate**: overridden / nurse-reviewed calls (calls with a tier that a
  nurse re-tiered or signed off).
* **Percentiles**: nearest rank (p90 of n values is the ceil(0.9 n)-th smallest).
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from typing import Any

from voa_core import patient_questions as pq
from voa_core.reports import ACTION_TIERS, FOLLOW_UP_TIERS
from voa_core.review import CLOSED_STATUSES

Row = Mapping[str, Any]

COMPLETED = "questionnaire_completed"
CHANNELS = ("all", "voice", "sms")
MAX_UNREACHED = 200
TOP_REASONS = 15
_NOT_DIALLED = frozenset({"sms_simulator"})


def _day(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10] if value else None


def _ts(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _patient(row: Row) -> str:
    if row.get("patient_key"):
        return "p:" + str(row["patient_key"])
    if row.get("entry_id"):
        return "e:" + str(row["entry_id"])
    return "c:" + str(row.get("call_id"))


def _rate(num: int | float, den: int | float, places: int = 4) -> float | None:
    return round(num / den, places) if den else None


def _is_machine(answered_by: Any) -> bool:
    v = str(answered_by or "").lower()
    return v.startswith("machine") or v == "fax"


def nearest_rank(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(pct * len(s)) - 1)]


def _business_days_after(d: date, n: int) -> date:
    out = d
    while n:
        out += timedelta(days=1)
        if out.weekday() < 5:
            n -= 1
    return out


def _overdue(tier: str, day: date, today: date) -> bool:
    """Emergency and same-day follow-ups are overdue once their day has passed;
    routine ones once the next business day after the call has passed."""
    if tier in ("emergency", "urgent_same_day"):
        return day < today
    return _business_days_after(day, 1) < today


# --------------------------------------------------------------------------- sections


def _outreach(calls: list[Row]) -> dict[str, Any]:
    by_patient: dict[str, list[Row]] = defaultdict(list)
    for r in calls:
        by_patient[_patient(r)].append(r)

    reached: dict[str, int] = {}  # patient -> attempts up to the first completion
    unreached: list[dict[str, Any]] = []
    for p, rows in by_patient.items():
        rows.sort(key=lambda r: (_ts(r.get("initiated_at")), str(r.get("call_id"))))
        first_done = next((i for i, r in enumerate(rows) if r.get("outcome") == COMPLETED), None)
        if first_done is not None:
            reached[p] = first_done + 1
        else:
            last = rows[-1]
            unreached.append(
                {
                    "patient_key": last.get("patient_key"),
                    "entry_id": last.get("entry_id"),
                    "attempts": len(rows),
                    "last_call_id": last.get("call_id"),
                    "last_at": _ts(last.get("initiated_at")) or None,
                }
            )
    unreached.sort(key=lambda u: (u["last_at"] or "", u["last_call_id"] or ""), reverse=True)

    dialled = [r for r in calls if r.get("placed_via") not in _NOT_DIALLED]
    connected = [r for r in dialled if r.get("answered")]
    human = [r for r in connected if not _is_machine(r.get("answered_by"))]
    verified = [r for r in human if r.get("channel") != "sms" and r.get("dob_result") == "verified"]
    completed = [r for r in verified if r.get("outcome") == COMPLETED]

    attempted = len(by_patient)
    return {
        "patients_attempted": attempted,
        "patients_reached": len(reached),
        "reach_rate": _rate(len(reached), attempted),
        "voice_funnel": {
            "dialled": len(dialled),
            "connected": len(connected),
            "human": len(human),
            "verified": len(verified),
            "completed": len(completed),
        },
        "text_funnel": {
            "invites_sent": sum(1 for r in calls if r.get("sms_follow_up_status") == "sent"),
            "completed_by_text": sum(
                1 for r in calls if r.get("channel") == "sms" and r.get("outcome") == COMPLETED
            ),
        },
        "attempts_per_reached": (round(statistics.fmean(reached.values()), 2) if reached else None),
        "unreached": unreached[:MAX_UNREACHED],
    }


def _clinical(
    calls: list[Row], reasons: list[Row], questions: list[Row], days: list[str]
) -> dict[str, Any]:
    per_day: dict[str, Counter] = {d: Counter() for d in days}
    for r in calls:
        if r.get("follow_up") in FOLLOW_UP_TIERS:
            per_day.setdefault(_day(r.get("day")) or "", Counter())[r["follow_up"]] += 1
    tiers_by_day = [
        {"day": d, **{t: per_day[d][t] for t in FOLLOW_UP_TIERS}} for d in sorted(per_day) if d
    ]

    rc = Counter((str(r.get("text")), str(r.get("tier"))) for r in reasons if r.get("text"))
    top = sorted(rc.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))[:TOP_REASONS]

    by_cat = Counter(str(q.get("category") or pq.CAT_OTHER) for q in questions)
    by_pri = Counter(str(q["priority"]) for q in questions if q.get("priority"))

    done = [r for r in calls if r.get("outcome") == COMPLETED]

    def n(pred) -> int:
        return sum(1 for r in done if pred(r))

    def pain_in(lo: int, hi: int):
        return lambda r: isinstance(r.get("pain_score"), int) and lo <= r["pain_score"] <= hi

    symptoms = {
        "fever_yes": n(lambda r: r.get("fever") == "yes"),
        "incision_yes": n(lambda r: r.get("incision") == "yes"),
        "swelling_yes": n(lambda r: r.get("swelling") == "yes"),
        "swelling_urgent": n(lambda r: bool(r.get("swelling_urgent"))),
        "poor_sleep": n(lambda r: r.get("sleeping_well") == "no"),
        "sugar_high": n(lambda r: r.get("sugar_status") == "high"),
        "sugar_low": n(lambda r: r.get("sugar_status") == "low"),
        "pain_0": n(pain_in(0, 0)),
        "pain_1_3": n(pain_in(1, 3)),
        "pain_4_7": n(pain_in(4, 7)),
        "pain_8_10": n(pain_in(8, 10)),
    }
    emergencies = [
        r["call_id"]
        for r in sorted(calls, key=lambda r: _ts(r.get("initiated_at")), reverse=True)
        if r.get("follow_up") == "emergency"
    ]
    return {
        "tiers_by_day": tiers_by_day,
        "top_reasons": [{"text": t, "tier": tier, "count": c} for (t, tier), c in top],
        "questions": {"by_category": dict(by_cat), "by_priority": dict(by_pri)},
        "symptoms": symptoms,
        "emergencies": emergencies,
    }


def _workload(calls: list[Row], today: date) -> dict[str, Any]:
    open_by_tier = dict.fromkeys(ACTION_TIERS, 0)
    overdue_by_tier = dict.fromkeys(ACTION_TIERS, 0)
    minutes: dict[str, list[float]] = {t: [] for t in ACTION_TIERS}
    up = down = overridden = reviewed = 0
    for r in calls:
        tier = r.get("follow_up")
        if tier in ACTION_TIERS and r.get("review_status") not in CLOSED_STATUSES:
            open_by_tier[tier] += 1
            d = _day(r.get("day"))
            if d and _overdue(tier, date.fromisoformat(d), today):
                overdue_by_tier[tier] += 1
        if tier in ACTION_TIERS and r.get("minutes_to_signoff") is not None:
            minutes[tier].append(float(r["minutes_to_signoff"]))
        if tier in FOLLOW_UP_TIERS and (r.get("overridden") or r.get("signed_off_at")):
            reviewed += 1
            if r.get("overridden"):
                overridden += 1
                up += r.get("override_direction") == "up"
                down += r.get("override_direction") == "down"
    return {
        "open_by_tier": open_by_tier,
        "overdue_by_tier": overdue_by_tier,
        "signoff_minutes": {
            t: {
                "median": round(statistics.median(v), 1) if v else None,
                "p90": round(nearest_rank(v, 0.9), 1) if v else None,
                "n": len(v),
            }
            for t, v in minutes.items()
        },
        "override": {"rate": _rate(overridden, reviewed), "up": up, "down": down, "n": reviewed},
    }


# --------------------------------------------------------------------------- entry point


def compute(
    calls: Iterable[Row],
    reasons: Iterable[Row],
    questions: Iterable[Row],
    *,
    start_day: date,
    end_day: date,
    channel: str = "all",
    today: date | None = None,
) -> dict[str, Any]:
    """The summary for ``start_day``..``end_day`` (inclusive, clinic-local days)."""
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {', '.join(CHANNELS)}")
    if end_day < start_day:
        raise ValueError("end_day is before start_day")
    today = today or date.today()
    lo, hi = start_day.isoformat(), end_day.isoformat()

    def in_range(r: Row) -> bool:
        d = _day(r.get("day"))
        return d is not None and lo <= d <= hi

    picked = [r for r in calls if in_range(r) and (channel == "all" or r.get("channel") == channel)]
    ids = {r.get("call_id") for r in picked}
    rs = [r for r in reasons if r.get("call_id") in ids and in_range(r)]
    qs = [q for q in questions if q.get("call_id") in ids and in_range(q)]
    days = [
        (start_day + timedelta(days=i)).isoformat() for i in range((end_day - start_day).days + 1)
    ]
    return {
        "range": {"start_day": lo, "end_day": hi, "channel": channel, "calls": len(picked)},
        "outreach": _outreach(picked),
        "clinical": _clinical(picked, rs, qs, days),
        "workload": _workload(picked, today),
    }
