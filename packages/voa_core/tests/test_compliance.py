from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from voa_core.compliance import (
    E164_RE,
    Allowlist,
    AllowlistConfigError,
    AllowlistMode,
    DialDecision,
    RecallSettings,
    can_claim_entry,
    check_destination,
    claim_entry,
    clean_runtime_allowlist,
    coerce_bool,
    entry_status_after_call,
    in_calling_window,
    is_due,
    next_recall_slot,
    parse_allowlist,
    parse_when,
    reconcile_entry_patch,
    window_text,
)

NY = "America/New_York"
A = "+12025550101"
B = "+12025550102"
C = "+12025550142"


# --- coerce_bool -------------------------------------------------------------


def test_coerce_bool_real_booleans():
    assert coerce_bool(True) is True
    assert coerce_bool(False) is False


def test_coerce_bool_strings():
    assert coerce_bool("true") is True
    assert coerce_bool("True") is True
    assert coerce_bool("  TRUE  ") is True
    assert coerce_bool("false") is False  # the bug: "false" is truthy in Python
    assert coerce_bool("no") is False
    assert coerce_bool("yes") is False
    assert coerce_bool("") is False


def test_coerce_bool_numerics_and_junk():
    assert coerce_bool(1) is True
    assert coerce_bool(0) is False
    assert coerce_bool(0.0) is False
    assert coerce_bool(None) is False
    assert coerce_bool({"nonsense": True}) is False


# --- allowlist parsing ----------------------------------------------------------


def test_unset_allowlist_is_fail_closed():
    assert parse_allowlist("") == Allowlist(AllowlistMode.UNSET)
    assert parse_allowlist("   ") == Allowlist(AllowlistMode.UNSET)
    assert parse_allowlist(None).mode is AllowlistMode.UNSET


def test_star_is_the_open_escape_hatch():
    al = parse_allowlist("*")
    assert al.mode is AllowlistMode.OPEN
    assert not al.exact and not al.prefixes


def test_exact_numbers_parse():
    al = parse_allowlist(f" {A} , {B} ")
    assert al.mode is AllowlistMode.LIST
    assert al.exact == frozenset({A, B})
    assert al.prefixes == ()


def test_long_prefix_is_accepted():
    al = parse_allowlist("+12025550*")  # exactly 8 digits
    assert al.mode is AllowlistMode.LIST
    assert al.prefixes == ("+12025550",)


@pytest.mark.parametrize(
    "bad",
    [
        "+*",  # every E.164
        "+1*",  # a whole numbering plan
        "+1202*",  # still far too broad
        "+1202555*",  # 7 digits: one short of the floor
        f"*,{A}",  # "*" mixed with real entries
        "12025550101",  # missing +
        "+0123456789",  # leading zero
        "not-a-number",
        "+1202abcd*",
        "1202555010*",  # prefix without +
        "+1202555010٧*",  # Unicode digit in a prefix
        "+1202555010٧",  # Unicode digit in an exact entry
    ],
)
def test_overbroad_or_malformed_allowlist_refuses_to_boot(bad):
    with pytest.raises(AllowlistConfigError):
        parse_allowlist(bad)


def test_e164_regex_rejects_unicode_digits():
    assert E164_RE.match("+1202555010٧") is None
    assert E164_RE.match(A) is not None


# --- allowlist matching ---------------------------------------------------------------


@pytest.mark.parametrize(
    "exact,prefixes,phone,expected",
    [
        (frozenset({A}), (), A, True),
        (frozenset({A}), (), B, False),
        (frozenset(), ("+1202555010",), A, True),
        (frozenset(), ("+1202555010",), C, False),
        # exact-match must not behave like a prefix
        (frozenset({"+12025550"}), (), A, False),
    ],
)
def test_static_allowlist_matching(exact, prefixes, phone, expected):
    assert Allowlist(AllowlistMode.LIST, exact, prefixes).matches(phone) is expected


def _static(*exact):
    return Allowlist(AllowlistMode.LIST, frozenset(exact))


def test_static_only_still_authorises():
    assert check_destination(A, _static(A)) is DialDecision.ALLOWED


def test_runtime_entry_authorises_without_a_redeploy():
    assert check_destination(A, Allowlist(), runtime={A}) is DialDecision.ALLOWED


def test_union_not_intersection():
    assert check_destination(A, _static(B), runtime={A}) is DialDecision.ALLOWED
    assert check_destination(B, _static(B), runtime={A}) is DialDecision.ALLOWED


def test_number_in_neither_source_is_blocked():
    d = check_destination(C, _static(B), runtime={A})
    assert d is DialDecision.BLOCKED and not d.allowed


def test_empty_union_is_not_configured_not_blocked():
    assert check_destination(A, Allowlist()) is DialDecision.NOT_CONFIGURED
    # A failed runtime read is passed as None and must fail closed.
    assert check_destination(A, Allowlist(), runtime=None) is DialDecision.NOT_CONFIGURED


def test_failed_runtime_read_still_honours_the_static_list():
    assert check_destination(A, _static(A), runtime=None) is DialDecision.ALLOWED
    assert check_destination(B, _static(A), runtime=None) is DialDecision.BLOCKED


def test_open_mode_bypasses_everything():
    d = check_destination(C, parse_allowlist("*"))
    assert d is DialDecision.ALLOWED_OPEN and d.allowed


def test_runtime_allowlist_ignores_malformed_rows():
    assert clean_runtime_allowlist([A, "garbage", None, 42, "12025550102"]) == {A}
    assert check_destination("garbage", Allowlist(), runtime=["garbage"]) is (
        DialDecision.NOT_CONFIGURED
    )


def test_a_non_e164_destination_is_never_allowed_by_prefix():
    al = Allowlist(AllowlistMode.LIST, prefixes=("+1202555010",))
    assert check_destination("+1202555010x", al) is DialDecision.BLOCKED


# --- recall settings -----------------------------------------------------------------


def test_recall_defaults():
    cfg = RecallSettings()
    assert cfg.enabled is True
    assert cfg.interval_minutes == 120
    assert (cfg.window_start, cfg.window_end) == ("09:00", "17:00")
    assert cfg.days == [0, 1, 2, 3, 4, 5]
    assert cfg.max_recalls == 2


@pytest.mark.parametrize(
    "bad",
    [
        {"window_start": "17:00", "window_end": "09:00"},
        {"window_start": "09:00", "window_end": "09:00"},
        {"window_start": "9am"},
        {"window_end": "24:00"},
        {"days": []},
        {"days": [7]},
        {"days": [-1]},
        {"interval_minutes": 1},
        {"interval_minutes": 7 * 24 * 60 + 1},
        {"max_recalls": 11},
    ],
)
def test_recall_settings_are_validated(bad):
    with pytest.raises(ValidationError):
        RecallSettings(**{**RecallSettings().model_dump(), **bad})


def test_recall_settings_normalise_days_and_times():
    cfg = RecallSettings(days=[4, 0, 4, 2], window_start=" 08:30 ")
    assert cfg.days == [0, 2, 4]
    assert cfg.window_start == "08:30"


def _ny(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(NY)).astimezone(UTC)


class TestRecallWindow:
    cfg = RecallSettings(window_start="09:00", window_end="17:00", days=[0, 1, 2, 3, 4, 5])

    def test_inside_the_window_is_unchanged(self):
        # Fri 16 Oct 2026, 15:50 New York
        assert next_recall_slot(_ny(2026, 10, 16, 15, 50), self.cfg, NY) == _ny(
            2026, 10, 16, 15, 50
        )

    def test_after_hours_moves_to_next_allowed_morning(self):
        assert next_recall_slot(_ny(2026, 10, 16, 18), self.cfg, NY) == _ny(2026, 10, 17, 9)

    def test_sunday_is_skipped(self):
        assert next_recall_slot(_ny(2026, 10, 17, 19), self.cfg, NY) == _ny(2026, 10, 19, 9)

    def test_before_hours_waits_for_the_start(self):
        assert next_recall_slot(_ny(2026, 10, 19, 6, 30), self.cfg, NY) == _ny(2026, 10, 19, 9)

    def test_the_end_of_the_window_is_exclusive(self):
        assert next_recall_slot(_ny(2026, 10, 16, 17), self.cfg, NY) == _ny(2026, 10, 17, 9)

    def test_seconds_round_up_to_the_minute(self):
        t = _ny(2026, 10, 16, 10) + timedelta(seconds=12)
        assert next_recall_slot(t, self.cfg, NY) == _ny(2026, 10, 16, 10, 1)

    def test_result_is_utc(self):
        assert next_recall_slot(_ny(2026, 10, 16, 18), self.cfg, NY).tzinfo is UTC

    def test_across_a_dst_change(self):
        # Sat 31 Oct 2026 after hours -> Mon 2 Nov 09:00 EST (clocks went back on Sunday).
        got = next_recall_slot(_ny(2026, 10, 31, 18), self.cfg, NY)
        assert got == datetime(2026, 11, 2, 14, 0, tzinfo=UTC)

    def test_the_window_follows_the_timezone(self):
        t = datetime(2026, 10, 16, 15, 0, tzinfo=UTC)  # 11:00 NY, 08:00 LA
        assert in_calling_window(t, self.cfg, NY)
        assert not in_calling_window(t, self.cfg, "America/Los_Angeles")


def test_in_calling_window_edges():
    cfg = RecallSettings()
    assert in_calling_window(_ny(2026, 10, 16, 9), cfg, NY)
    assert in_calling_window(_ny(2026, 10, 16, 16, 59), cfg, NY)
    assert not in_calling_window(_ny(2026, 10, 16, 17), cfg, NY)
    assert not in_calling_window(_ny(2026, 10, 16, 8, 59), cfg, NY)
    assert not in_calling_window(_ny(2026, 10, 18, 12), cfg, NY)  # Sunday


def test_unknown_timezone_and_naive_times_raise():
    cfg = RecallSettings()
    with pytest.raises(ValueError):
        in_calling_window(_ny(2026, 10, 16, 12), cfg, "Mars/Olympus_Mons")
    with pytest.raises(ValueError):
        next_recall_slot(datetime(2026, 10, 16, 12), cfg, NY)


def test_window_text():
    cfg = RecallSettings(days=[0, 2])
    assert window_text(cfg, NY) == "Mon, Wed, 09:00–17:00 (America/New_York)"


# --- is_due -------------------------------------------------------------------------

NOW = datetime(2026, 10, 16, 15, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "entry,expected",
    [
        ({}, False),  # no planned time: hand-dial only
        ({"scheduled_for": ""}, False),
        ({"scheduled_for": "tomorrow"}, False),  # unparseable: never auto-dialled
        ({"scheduled_for": "2026-10-16T15:01:00+00:00"}, False),  # future
        ({"scheduled_for": "2026-10-16T15:00:00+00:00"}, True),  # exactly now
        ({"scheduled_for": "2026-10-16T14:00:00Z"}, True),
        ({"scheduled_for": "2026-10-16T14:00:00"}, True),  # naive = UTC
        ({"scheduled_for": "2026-10-16T10:30:00-04:00"}, True),
        # attempted since the planned time: one automatic attempt per time
        (
            {"scheduled_for": "2026-10-16T14:00:00Z", "last_attempt_at": "2026-10-16T14:00:00Z"},
            False,
        ),
        (
            {"scheduled_for": "2026-10-16T14:00:00Z", "last_attempt_at": "2026-10-16T14:30:00Z"},
            False,
        ),
        # attempted before the planned time (an earlier slot): due again
        (
            {"scheduled_for": "2026-10-16T14:00:00Z", "last_attempt_at": "2026-10-15T14:00:00Z"},
            True,
        ),
        # an unparseable last attempt does not block
        ({"scheduled_for": "2026-10-16T14:00:00Z", "last_attempt_at": "junk"}, True),
    ],
)
def test_is_due(entry, expected):
    assert is_due(entry, NOW) is expected


def test_is_due_needs_an_aware_now():
    with pytest.raises(ValueError):
        is_due({"scheduled_for": "2026-10-16T14:00:00Z"}, datetime(2026, 10, 16, 15))


# --- call-list claim ----------------------------------------------------------------


def test_only_one_instance_can_claim_an_entry():
    store = {"list_status": "pending"}
    first = claim_entry(store, 1, now=NOW)
    assert first["list_status"] == "calling" and first["attempts"] == 1
    assert first["last_attempt_at"] == NOW.isoformat() and first["last_error"] == ""
    # The second instance read the same snapshot and also asks for attempts=1.
    assert claim_entry(first, 1, now=NOW) is None


def test_a_missing_entry_cannot_be_claimed():
    assert claim_entry(None, 1, now=NOW) is None


def test_a_stranded_calling_row_can_be_recovered():
    store = {"list_status": "calling", "attempts": 1}
    assert not can_claim_entry(store, 2)
    claimed = claim_entry(store, 2, now=NOW, recover_stranded=True)
    assert claimed["attempts"] == 2


def test_two_instances_cannot_recover_the_same_stranded_row():
    store = {"list_status": "calling", "attempts": 1}
    first = claim_entry(store, 2, now=NOW, recover_stranded=True)
    assert first is not None
    assert claim_entry(first, 2, now=NOW, recover_stranded=True) is None


def test_claim_rejects_a_row_that_changed_since_the_run_was_built():
    # A finished the row (attempts 1, completed); B still holds attempts=0.
    store = {"list_status": "completed", "attempts": 1}
    assert claim_entry(store, 1, now=NOW) is None


def test_call_again_on_a_finished_row_still_works():
    # A nurse naming the row ("Call again") is explicit.
    store = {"list_status": "completed", "attempts": 1}
    assert claim_entry(store, 2, now=NOW, explicit=True)["list_status"] == "calling"


@pytest.mark.parametrize("status", ["canceled", "completed", "blocked"])
def test_a_scheduled_run_never_claims_a_cancelled_finished_or_blocked_row(status):
    """B-2: a cancel lands after the run queued the dial and does not touch the
    attempts token, so the claim re-checks the status itself."""
    store = {"list_status": status, "attempts": 0}
    assert claim_entry(store, 1, now=NOW) is None
    assert claim_entry(store, 1, now=NOW, explicit=True)["list_status"] == "calling"


def test_parse_when_takes_a_naive_time_as_utc():
    assert parse_when("2026-10-18T09:00:00").tzinfo is not None
    assert parse_when("not a time") is None and parse_when(None) is None and parse_when("") is None


def test_a_late_result_keeps_a_cancel_and_records_the_outcome():
    assert reconcile_entry_patch({"list_status": "canceled"}, "left_vmail") == {
        "last_call_status": "left_vmail"
    }
    assert reconcile_entry_patch({"list_status": "blocked"}, "success") == {
        "last_call_status": "success"
    }


def test_our_own_result_is_recorded_but_an_older_attempt_does_not_reset_a_newer_one():
    assert reconcile_entry_patch(
        {"list_status": "calling", "attempts": 2}, "success", attempt=2
    ) == {
        "list_status": "completed",
        "last_call_status": "success",
    }
    assert reconcile_entry_patch({"list_status": "calling", "attempts": 3}, "rna", attempt=2) == {
        "last_call_status": "rna"
    }


def test_next_opening_across_the_fall_back_weekend():
    """1 Nov 2026 clocks go back. Missed at 17:30 EDT on Saturday 31 Oct,
    Mon-Sat 09:00-17:00: the next opening is Monday 09:00 EST (14:00Z)."""
    after = datetime(2026, 10, 31, 17, 30, tzinfo=ZoneInfo(NY))
    assert next_recall_slot(after, RecallSettings(), NY) == datetime(2026, 11, 2, 14, 0, tzinfo=UTC)
    every_day = RecallSettings(days=list(range(7)))
    assert next_recall_slot(after, every_day, NY) == datetime(2026, 11, 1, 14, 0, tzinfo=UTC)


def test_claim_tolerates_a_missing_or_junk_attempts_field():
    assert can_claim_entry({"list_status": "pending", "attempts": None}, 1)
    assert not can_claim_entry({"list_status": "pending", "attempts": "x"}, 1)


def test_claim_does_not_mutate_the_input():
    store = {"list_status": "pending", "attempts": 0, "phone": A}
    claimed = claim_entry(store, 1, now=NOW)
    assert store == {"list_status": "pending", "attempts": 0, "phone": A}
    assert claimed["phone"] == A


@pytest.mark.parametrize(
    "final,expected",
    [
        ("success", ("completed", "success")),
        ("rna", ("pending", "rna")),
        ("left_vmail", ("pending", "left_vmail")),
        ("busy", ("pending", "busy")),
        ("failed", ("pending", "failed")),
        ("interrupted", ("pending", "interrupted")),
        ("canceled", ("pending", "canceled")),
        (None, ("pending", "timeout")),
    ],
)
def test_only_success_clears_an_entry(final, expected):
    assert entry_status_after_call(final) == expected


def test_late_report_reconciliation():
    assert reconcile_entry_patch({"list_status": "pending"}, "success") == {
        "list_status": "completed",
        "last_call_status": "success",
    }
    # A promoted voicemail still means nobody was reached.
    assert reconcile_entry_patch({"list_status": "calling"}, "left_vmail") == {
        "list_status": "pending",
        "last_call_status": "left_vmail",
    }
    assert reconcile_entry_patch({"list_status": "completed"}, "left_vmail") is None
