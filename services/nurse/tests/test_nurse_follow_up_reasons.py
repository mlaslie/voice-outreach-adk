"""Every reason a call needs a nurse: shown with its own tier on the call card,
summarised in worklist rows, returned to the model, and set by a nurse (several at
once) through a confirmation card. Fictional data, no network, no model."""

import json

import httpx
import pytest
from nurse_fixtures import FakeBackend, check, configure_cards, make_turn
from voa_nurse import actions, cards, tools, triage, turn
from voa_nurse import worklists as wl
from voa_nurse.backend import Backend, IdTokenSource

RED, ORANGE, YELLOW, BLUE = "\U0001f534", "\U0001f7e0", "\U0001f7e1", "\U0001f535"


@pytest.fixture(autouse=True)
def _cards():
    configure_cards()


@pytest.fixture
def t():
    with turn.active(make_turn(backend=FakeBackend("reasons_day"))) as current:
        yield current


def _call(t, name):
    return next(c for c in t.backend.state["calls"] if c["patient_name"] == name)


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines()]


# --- triage ---------------------------------------------------------------------------


def test_reasons_are_sorted_most_urgent_first_and_old_calls_fall_back(t):
    rosa = triage.follow_up_reasons(_call(t, "Rosa Delgado"))
    assert [(r["tier"], r["text"]) for r in rosa] == [
        ("urgent_same_day", "Patient reports question asked (medication)"),
        ("informational", "Not sleeping well"),
    ]
    old = triage.follow_up_reasons(_call(t, "Edith Park"))
    assert [(r["tier"], r["text"], r["source"]) for r in old] == [
        ("routine_next_business_day", "Constipation since discharge.", "agent")
    ]
    assert triage.agent_original_reasons(_call(t, "Edith Park")) == []


def test_an_old_single_reason_keeps_the_agents_tier_after_a_nurse_note():
    c = {
        "follow_up": "none",
        "agent_follow_up": "urgent_same_day",
        "follow_up_reason": "Fever reported.",
        "follow_up_override": {"set_by": "rn@example.org", "reason": "Spoke with patient."},
    }
    (r,) = triage.follow_up_reasons(c)
    assert r["tier"] == "urgent_same_day" and r["text"] == "Fever reported."


def test_summary_is_the_top_reason_plus_a_count(t):
    assert (
        triage.reason_summary(_call(t, "Rosa Delgado"))
        == "Patient reports question asked (medication) · +1 more"
    )
    assert triage.reason_summary(_call(t, "Edith Park")) == "Constipation since discharge."
    assert triage.reason_summary({}) == ""


# --- the call card ----------------------------------------------------------------------


def test_detail_card_lists_every_reason_with_its_tier(t):
    card = cards.call_detail_card(_call(t, "Rosa Delgado"))
    check(card)
    lines = _lines(card.as_text())
    i = lines.index("Reasons for follow-up, most urgent first")
    assert lines[i + 1 : i + 5] == [
        f"{ORANGE}  Patient reports question asked (medication)",
        "RN callback today",
        f"{BLUE}  Not sleeping well",
        "FYI, no callback",
    ]
    # The combined sentence is not repeated under the headline.
    assert "Also noted" not in card.as_text()


def test_detail_card_says_which_nurse_set_the_reasons_and_keeps_the_agents(t):
    card = cards.call_detail_card(_call(t, "Felix Moreau"))
    check(card)
    lines = _lines(card.as_text())
    i = lines.index("Reasons for follow-up, most urgent first")
    assert lines[i + 1].startswith("Set by rn.okoro@example.org ")
    assert lines[i + 2 : i + 6] == [
        f"{YELLOW}  Refill sent to pharmacy",
        "RN callback next business day",
        f"{YELLOW}  Wound check at clinic Friday",
        "RN callback next business day",
    ]
    j = lines.index("Automated assessment's original reasons")
    assert j > i + 5
    assert lines[j + 1 : j + 5] == [
        f"{ORANGE}  Out of pain medication",
        "RN callback today",
        f"{YELLOW}  Incision redness",
        "RN callback next business day",
    ]
    # The note equals the reasons, so it is not repeated as "Tier set by".
    assert "Tier set by" not in card.as_text()


def test_detail_card_for_an_old_call_shows_its_one_reason(t):
    card = cards.call_detail_card(_call(t, "Edith Park"))
    check(card)
    lines = _lines(card.as_text())
    i = lines.index("Reasons for follow-up")
    assert lines[i + 1 : i + 3] == [
        f"{YELLOW}  Constipation since discharge.",
        "RN callback next business day",
    ]
    assert "original reasons" not in card.as_text()


def test_unreached_call_shows_no_reasons_block():
    vm = {
        "doc_id": "CA" + "9" * 32,
        "to": "+12025550109",
        "call_status": "left_vmail",
        "outcome": "voicemail_left",
        "follow_up": "none",
        "follow_up_reason": "Voicemail left; no patient contact.",
    }
    text = cards.call_detail_card(vm).as_text()
    assert "Reasons for follow-up" not in text and "Voicemail left" in text


# --- worklist rows -------------------------------------------------------------------


async def test_worklist_rows_show_the_top_reason_and_how_many_more(t):
    card, data = await wl.view_needs("now")
    check(card)
    assert "Patient reports question asked (medication) · +1 more" in card.as_text()
    rosa = next(p for p in data["patients"] if p["patient"] == "Rosa Delgado")
    assert rosa["all_reasons"] == [
        "Urgent: RN callback today: Patient reports question asked (medication)",
        "FYI: no callback needed: Not sleeping well",
    ]
    card, _ = await wl.view_needs("next_day")
    text = card.as_text()
    assert "Refill sent to pharmacy · +1 more" in text
    assert "Constipation since discharge." in text
    assert "· +" not in text.split("Constipation since discharge.")[1].splitlines()[0]
    q = cards.follow_up_card(t.backend.state["calls"]).as_text()
    assert "Patient reports question asked (medication) · +1 more" in q


async def test_nurse_day_priya_has_four_reasons_in_tier_order():
    with turn.active(make_turn(backend=FakeBackend("nurse_day"))) as t:
        priya = _call(t, "Priya Raman")
        tiers = [r["tier"] for r in triage.follow_up_reasons(priya)]
        assert tiers == [
            "urgent_same_day",
            "urgent_same_day",
            "routine_next_business_day",
            "informational",
        ]
        card, _ = await wl.view_needs("now")
        assert "Patient reports question asked (medication) · +3 more" in card.as_text()


# --- get_call data for the model ----------------------------------------------------


async def test_get_call_returns_every_reason_to_the_model(t):
    _, data = await actions.view_call(call_sid=_call(t, "Rosa Delgado")["doc_id"])
    assert data["follow_up_reasons"] == [
        {
            "tier": "urgent_same_day",
            "tier_label": "Urgent: RN callback today",
            "text": "Patient reports question asked (medication)",
            "source": "triage",
        },
        {
            "tier": "informational",
            "tier_label": "FYI: no callback needed",
            "text": "Not sleeping well",
            "source": "triage",
        },
    ]
    assert "agent_original_reasons" not in data
    json.dumps(data)  # goes back to the model as JSON


async def test_get_call_for_a_nurse_change_has_who_and_the_agents_reasons(t):
    _, data = await actions.view_call(call_sid=_call(t, "Felix Moreau")["doc_id"])
    assert [(r["text"], r["source"], r["set_by"]) for r in data["follow_up_reasons"]] == [
        ("Refill sent to pharmacy", "nurse", "rn.okoro@example.org"),
        ("Wound check at clinic Friday", "nurse", "rn.okoro@example.org"),
    ]
    assert [r["text"] for r in data["agent_original_reasons"]] == [
        "Out of pain medication",
        "Incision redness",
    ]


async def test_get_call_for_an_old_call_falls_back_to_the_sentence(t):
    _, data = await actions.view_call(call_sid=_call(t, "Edith Park")["doc_id"])
    assert data["follow_up_reasons"] == [
        {
            "tier": "routine_next_business_day",
            "tier_label": "Routine: RN callback next business day",
            "text": "Constipation since discharge.",
            "source": "automated caller",
        }
    ]


# --- several reasons through the proposal -------------------------------------------


async def test_tool_with_several_reasons_confirms_then_sends_them_all(t):
    sid = _call(t, "Rosa Delgado")["doc_id"]
    await tools.propose_change_follow_up(
        call_sid=sid,
        tier="routine_next_business_day",
        reasons=["Medication question answered by phone", " Not sleeping well ", ""],
    )
    (card,) = t.cards
    check(card)
    text = card.as_text()
    assert "Reasons to be saved (2):" in text
    assert "1. Medication question answered by phone" in text
    assert "2. Not sleeping well" in text
    assert t.backend.writes == []  # nothing happens before Confirm
    (p,) = await t.proposals.pending()
    out = await actions.execute(p.id)
    assert "Reason: Not sleeping well" in out.as_text()
    assert t.backend.writes == [
        (
            "tier",
            sid,
            {
                "follow_up": "routine_next_business_day",
                "reasons": ["Medication question answered by phone", "Not sleeping well"],
            },
        )
    ]


async def test_reasons_and_a_note_are_both_sent(t):
    sid = _call(t, "Rosa Delgado")["doc_id"]
    _, data = await wl.propose_tier(
        sid,
        "informational",
        "Spoke with patient and daughter.",
        reasons=["Refill approved", "Sleep hygiene advice given"],
    )
    assert data["status"] == "awaiting_user_confirmation"
    (p,) = await t.proposals.pending()
    await actions.execute(p.id)
    assert t.backend.writes[-1][2] == {
        "follow_up": "informational",
        "reasons": ["Refill approved", "Sleep hygiene advice given"],
        "reason": "Spoke with patient and daughter.",
    }


async def test_single_reason_still_works_as_a_note(t):
    sid = _call(t, "Rosa Delgado")["doc_id"]
    await tools.propose_change_follow_up(
        call_sid=sid, tier="none", reason="Called back; all resolved."
    )
    (p,) = await t.proposals.pending()
    await actions.execute(p.id)
    assert t.backend.writes == [
        ("tier", sid, {"follow_up": "none", "reason": "Called back; all resolved."})
    ]


async def test_bad_reasons_reshow_the_form(t):
    sid = _call(t, "Rosa Delgado")["doc_id"]
    for reasons in (["ok"], ["x" * 201], [f"reason number {i}" for i in range(11)]):
        card, data = await wl.propose_tier(sid, "none", reasons=reasons)
        assert data["status"] == "invalid", reasons
        check(card)
    assert await t.proposals.pending() == []


async def test_tool_without_reasons_opens_the_form_prefilled(t):
    sid = _call(t, "Rosa Delgado")["doc_id"]
    await tools.propose_change_follow_up(call_sid=sid, tier="none")
    (card,) = t.cards
    check(card)
    assert card.data_model["tier"] == {
        "choice": ["none"],
        "reason0": "",
        "reason1": "",
        "reason2": "",
    }
    assert "Current reasons: Patient reports question asked (medication) · +1 more" in (
        card.as_text()
    )


async def test_form_submit_sends_each_filled_line_as_a_reason(t):
    sid = _call(t, "Rosa Delgado")["doc_id"]
    card = await actions.handle_click(
        "wl_tier_submit",
        {
            "call_sid": sid,
            "choice": ["routine_next_business_day"],
            "reason0": "Refill approved",
            "reason1": "",
            "reason2": "Recheck sleep next week",
        },
    )
    check(card)
    assert "Reasons to be saved (2):" in card.as_text()
    (p,) = await t.proposals.pending()
    await actions.execute(p.id)
    assert t.backend.writes[-1][2]["reasons"] == ["Refill approved", "Recheck sleep next week"]


async def test_history_shows_the_reasons_of_a_change(t):
    card, _ = await wl.view_history(call_sid=_call(t, "Felix Moreau")["doc_id"])
    check(card)
    assert "(Refill sent to pharmacy; Wound check at clinic Friday)" in card.as_text()


async def test_real_client_posts_reasons_to_the_api():
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={})

    api = "https://voa-api.example.org"
    b = Backend(
        api,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        tokens=IdTokenSource("http://localhost:9"),
    )
    cid = "CA" + "2" * 32
    await b.set_follow_up(cid, "none", reasons=["Refill approved", "Sleeping better"])
    await b.set_follow_up(cid, "none", "Spoke with patient.")
    assert seen == [
        (
            "POST",
            f"/api/calls/{cid}/follow-up",
            {"follow_up": "none", "reasons": ["Refill approved", "Sleeping better"]},
        ),
        (
            "POST",
            f"/api/calls/{cid}/follow-up",
            {"follow_up": "none", "reason": "Spoke with patient."},
        ),
    ]
