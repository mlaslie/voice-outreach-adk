"""compliance.stranded_entry_fix: the pure rule behind the stranded-row recovery."""

from datetime import UTC, datetime, timedelta

import pytest
from voa_core.compliance import STRANDED_REQUEUED, stranded_entry_fix
from voa_core.lifecycle import TERMINAL_STATUSES

NOW = datetime(2026, 3, 3, 15, 0, tzinfo=UTC)
TTL, GRACE = 1020, 300


def ago(s: float) -> str:
    return (NOW - timedelta(seconds=s)).isoformat()


def fix(entry, call):
    return stranded_entry_fix(
        entry,
        call,
        now=NOW,
        in_flight_ttl_s=TTL,
        terminal_grace_s=GRACE,
        terminal_statuses=TERMINAL_STATUSES,
    )


def calling(claimed_s_ago: float = 5000) -> dict:
    return {"list_status": "calling", "attempts": 2, "last_attempt_at": ago(claimed_s_ago)}


@pytest.mark.parametrize("status", ["pending", "completed", "canceled", "blocked", None])
def test_only_calling_rows(status):
    assert fix({"list_status": status, "attempts": 1}, None) is None
    assert fix(None, None) is None


def test_no_call_doc_waits_for_the_ttl_then_requeues():
    assert fix(calling(TTL - 1), None) is None
    f = fix(calling(TTL + 1), None)
    assert f.reason == "no_call" and f.call_status is None
    assert f.patch == {
        "list_status": "pending",
        "last_call_status": "timeout",
        "last_error": STRANDED_REQUEUED,
    }


@pytest.mark.parametrize(
    "status,expected", [("success", "completed"), ("rna", "pending"), ("failed", "pending")]
)
def test_a_terminal_status_is_applied_after_the_grace(status, expected):
    call = {"call_status": status, "ended_at": ago(GRACE - 1), "initiated_at": ago(600)}
    assert fix(calling(), call) is None
    call["ended_at"] = ago(GRACE + 1)
    f = fix(calling(), call)
    assert f.reason == "terminal_unapplied" and f.call_status == status
    assert f.patch == {"list_status": expected, "last_call_status": status}


def test_a_live_call_is_left_alone_within_the_ttl_even_if_the_claim_is_old():
    call = {"call_status": "in_progress", "initiated_at": ago(TTL - 1)}
    assert fix(calling(5000), call) is None
    call["initiated_at"] = ago(TTL + 1)
    assert fix(calling(5000), call).reason == "call_never_finished"


def test_missing_times_count_as_old():
    assert fix({"list_status": "calling", "attempts": 1}, None).reason == "no_call"
    assert fix(calling(), {"call_status": "busy"}).patch["list_status"] == "pending"


def test_a_naive_now_is_refused():
    with pytest.raises(ValueError):
        stranded_entry_fix(
            calling(),
            None,
            now=datetime(2026, 3, 3, 15),
            in_flight_ttl_s=TTL,
            terminal_grace_s=GRACE,
            terminal_statuses=TERMINAL_STATUSES,
        )
