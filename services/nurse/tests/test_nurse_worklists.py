"""The nurse's worklists against fixture datasets, in process (no network, no
model): every card schema-valid, every bucket right."""

from datetime import datetime, timedelta

import pytest
from nurse_fixtures import FakeBackend, check, configure_cards, make_turn
from voa_nurse import actions, cards, turn
from voa_nurse import worklists as wl


@pytest.fixture(autouse=True)
def _cards():
    configure_cards()


@pytest.fixture
def t():
    with turn.active(make_turn(backend=FakeBackend("nurse_day"))) as current:
        yield current


def _by_name(t, name):
    return next(c for c in t.backend.state["calls"] if c["patient_name"] == name)


def _entry(t, name):
    return next(e for e in t.backend.state["entries"] if e["patient_name"] == name)


async def test_home_counts_match_the_lists(t):
    card, counts = await wl.view_home()
    check(card)
    assert counts["now"] == 3  # Dolores (emergent), Marcus, Priya (urgent)
    assert counts["next_day"] == 2  # Harold, Lucia
    assert counts["cleared"] == 2  # Gregory (phone), Angela (text)
    text = card.as_text()
    assert "[Needs you now (3)]" in text and "[Next business day (2)]" in text
    assert "[Cleared (2)]" in text and "Emergency reported: Dolores Whitfield" in text
    assert card.is_menu


async def test_a_voicemail_report_with_none_is_not_cleared(t):
    _, data = await wl.view_cleared()
    assert "Samuel Reyes" not in [p["patient"] for p in data["patients"]]


async def test_needs_now_has_reasons_and_due_times(t):
    card, data = await wl.view_needs("now")
    check(card)
    assert [p["patient"] for p in data["patients"]] == [
        "Dolores Whitfield",
        "Marcus Ellery",
        "Priya Raman",
    ]
    assert data["patients"][0]["due"] == "now"
    assert "Chest tightness" in card.as_text() and "[Change tier]" in card.as_text()


async def test_next_business_day(t):
    card, data = await wl.view_needs("next_day")
    check(card)
    assert {p["patient"] for p in data["patients"]} == {"Harold Okafor", "Lucia Fernández"}


async def test_cleared_says_why(t):
    card, data = await wl.view_cleared()
    check(card)
    why = {p["patient"]: p["why"] for p in data["patients"]}
    assert why["Gregory Tan"].startswith("Denies pain, fever and incision concerns")
    assert "(by text)" in why["Angela Brooks"]


async def test_queue_shows_future_runs_and_text_bookings_never_the_past(t):
    card, data = await wl.view_queue()
    check(card)
    names = [r["patient"] for r in data["rows"]]
    assert names[0] == "Jamal Whitaker" and data["rows"][0]["calling"]
    assert "Odette Marsh" not in names and "Desmond Hale" not in names
    assert "Samuel Reyes" in names
    now = datetime.now(cards.TZ).isoformat()
    assert all(r["calling"] or r["when"] >= now[:16] for r in data["rows"])
    assert "[Call now]" in card.as_text() and "[Cancel call]" in card.as_text()


async def test_called_today_groups_by_what_to_do(t):
    card, data = await wl.view_called()
    check(card)
    text = card.as_text()
    for head in (
        "Needs you now",
        "Next business day",
        "Cleared, no follow-up",
        "Not reached",
        "Asked to be called another time",
        "Cancelled",
    ):
        assert head in text, head
    then = {x["patient"]: x["then"] for x in data["not_reached"]}
    assert then["Samuel Reyes"].startswith("callback booked by text")
    assert then["Naomi Castillo"].startswith("follow-up text sent, no reply yet")
    assert "auto-recall" in then["Naomi Castillo"]
    assert then["Walter Kim"].startswith("no follow-up text")
    assert "Readmitted overnight" in text
    assert data["needs_you_now_called_today"] == ["Dolores Whitfield", "Marcus Ellery"]
    assert data["still_open_from_earlier_days"] == 1


async def test_cancel_needs_a_reason_then_confirms(t):
    eid = _entry(t, "Eleanor Vance")["entry_id"]
    card, data = await wl.propose_cancel(eid, "  ")
    assert data["status"] == "invalid" and await t.proposals.pending() == []
    check(card)
    card, data = await wl.propose_cancel(eid, "Readmitted; hold calls.")
    assert data["status"] == "awaiting_user_confirmation" and t.backend.writes == []
    (p,) = await t.proposals.pending()
    out = await actions.execute(p.id)
    assert "Cancelled Eleanor Vance's call" in out.as_text()
    assert t.backend.writes == [("cancel", [eid], "Readmitted; hold calls.")]


async def test_change_tier_needs_a_different_tier_and_a_reason(t):
    sid = _by_name(t, "Marcus Ellery")["doc_id"]
    for tier, reason in (
        ("urgent_same_day", "already urgent"),
        ("none", ""),
        ("bogus", "a reason here"),
    ):
        card, data = await wl.propose_tier(sid, [tier], reason)
        assert data["status"] == "invalid" and await t.proposals.pending() == []
        check(card)
    card, data = await wl.propose_tier(
        sid, ["routine_next_business_day"], "Called back; improving."
    )
    assert data["status"] == "awaiting_user_confirmation"
    (p,) = await t.proposals.pending()
    assert "Routine" in (await actions.execute(p.id)).as_text()
    assert t.backend.writes == [
        ("tier", sid, "routine_next_business_day", "Called back; improving.")
    ]


async def test_call_now_confirms_first(t):
    eid = _entry(t, "Ingrid Solberg")["entry_id"]
    _, data = await wl.propose_call_now(eid)
    assert data["status"] == "awaiting_user_confirmation" and t.backend.writes == []
    (p,) = await t.proposals.pending()
    await actions.execute(p.id)
    assert t.backend.writes == [("call_now", [eid])]


async def test_forms_are_schema_valid(t):
    check((await wl.cancel_form(t.backend.state["entries"][1]["entry_id"]))[0])
    check((await wl.tier_form(t.backend.state["calls"][0]["doc_id"]))[0])
    check(wl.settings_card())


def test_due_text():
    now = datetime.now(cards.TZ)
    assert wl.due_text("emergency", now) == "now"
    assert wl.due_text("urgent_same_day", now - timedelta(days=2)).startswith("overdue")
    assert wl.due_text("urgent_same_day", None) == ""


async def test_failed_data_still_renders(t):
    t.backend.fail = {"get_call_runs"}
    card, _ = await wl.view_home()
    check(card)
    assert "Unavailable right now: runs" in card.as_text()
    t.backend.fail = {
        "list_calls",
        "get_call_list",
        "get_callbacks",
        "get_sessions",
        "get_call_runs",
    }
    for view in (wl.view_queue(), wl.view_called(), wl.view_needs("now"), wl.view_cleared()):
        check((await view)[0])


async def test_texts_for_a_by_text_questionnaire(t):
    sid = _by_name(t, "Angela Brooks")["doc_id"]
    card, data = await wl.view_texts(sid)
    check(card)
    assert len(data["messages"]) == 4 and "2/10" in card.as_text()
    card2, _ = await actions.view_transcript(call_sid=sid)
    assert "Text messages: Angela Brooks" in card2.as_text()


async def test_patient_history_has_the_story_and_what_is_next(t):
    card, data = await wl.view_history(call_sid=_by_name(t, "Naomi Castillo")["doc_id"])
    check(card)
    assert data["next"].startswith("Auto-recall")
    assert any("Follow-up text sent" in e["what"] for e in data["events"])
    assert any(e["what"].startswith("Call: No answer") for e in data["events"])


async def test_detail_card_offers_change_tier_only_for_assessments(t):
    clear, vm = _by_name(t, "Gregory Tan"), _by_name(t, "Samuel Reyes")
    assert "[Change tier]" in cards.call_detail_card(clear).as_text()
    assert "[Change tier]" not in cards.call_detail_card(vm).as_text()
    for c in t.backend.state["calls"]:
        check(cards.call_detail_card(c))


async def test_an_old_follow_up_past_the_api_page_is_labelled_not_hidden(t):
    """Review A-1: the API serves at most 500 calls. The views say so in the card and
    in the model's data instead of implying nobody else needs a callback."""
    t.backend.use("many_calls")
    card, data = await wl.view_needs("now")
    assert data["may_be_incomplete"] is True
    assert "Based on the newest 500 calls" in card.as_text()
    _, home = await wl.view_home()
    assert home["may_be_incomplete"] is True


# --- reschedule a cancelled call -------------------------------------------------------------


def _local(days_ahead: int, hour: int, weekday: int | None = None) -> str:
    d = datetime.now(cards.TZ).date() + timedelta(days=days_ahead)
    if weekday is not None:
        d += timedelta(days=(weekday - d.weekday()) % 7 or 7)
    return f"{d.isoformat()}T{hour:02d}:00"


async def test_a_cancelled_call_offers_reschedule(t):
    card, _ = await wl.view_called()
    assert "[Reschedule]" in card.as_text()


async def test_reschedule_form_lists_only_in_hours_runs_and_the_next_opening(t):
    card, data = await wl.reschedule_form(_entry(t, "Desmond Hale")["entry_id"])
    check(card)
    assert data["calling_hours"] == "Mon, Tue, Wed, Thu, Fri, Sat, 09:00–17:00"
    assert any(o.startswith("Next opening") for o in data["options"])
    assert data["options"][-1] == "Another time (pick below)"


async def test_reschedule_outside_calling_hours_is_refused(t):
    eid = _entry(t, "Desmond Hale")["entry_id"]
    for at, why in (
        (_local(0, 10, weekday=6), "outside calling hours"),
        (_local(1, 20), "outside calling hours"),
        (_local(-3, 10), "has passed"),
        ("", "Pick a date"),
    ):
        card, data = await wl.propose_reschedule(eid, ["custom"], at)
        assert data["status"] == "invalid" and why in " ".join(data["problems"]), (at, data)
        check(card)
    assert await t.proposals.pending() == [] and not t.backend.writes


async def test_reschedule_into_the_next_opening_confirms_then_writes(t):
    eid = _entry(t, "Desmond Hale")["entry_id"]
    _, data = await wl.propose_reschedule(eid, ["next"])
    assert data["status"] == "awaiting_user_confirmation" and not t.backend.writes
    (p,) = await t.proposals.pending()
    when = datetime.fromisoformat(p.params["when"])
    assert wl.in_hours(when, wl.DEFAULT_HOURS) and when > datetime.now(cards.TZ)
    out = await actions.execute(p.id)
    check(out)
    assert "back on the queue" in out.as_text()
    assert t.backend.writes == [("reschedule", eid, None, p.params["when"])]
