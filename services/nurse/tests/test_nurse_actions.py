"""Views and the propose -> confirm flow, against the fake API client. No network,
no model."""

import pytest
from nurse_fixtures import FakeBackend, check, configure_cards, make_turn
from voa_nurse import actions, turn

PATIENT = dict(
    phone="+12025550101",
    patient_name="Jordan Rivera",
    patient_id="P-1001",
    patient_dob="1950-03-15",
)


@pytest.fixture(autouse=True)
def _cards():
    configure_cards()


@pytest.fixture
def t():
    with turn.active(make_turn()) as current:
        current.backend.state["allowlist"] = [{"phone": "+12025550101"}]
        yield current


async def test_propose_does_not_dial_and_confirm_runs_once(t):
    card, data = await actions.propose_place_call(**PATIENT)
    assert data["status"] == "awaiting_user_confirmation" and t.backend.writes == []
    check(card)
    (p,) = await t.proposals.pending()
    assert "Follow-up call placed to Jordan Rivera" in (await actions.execute(p.id)).as_text()
    assert len(t.backend.writes) == 1
    assert "expired" in (await actions.execute(p.id)).as_text()  # single use
    assert len(t.backend.writes) == 1


async def test_unknown_proposal_id_does_nothing(t):
    await actions.propose_place_call(**PATIENT)
    out = await actions.handle_click("confirm", {"proposal_id": "forged"})
    assert "expired" in out.as_text() and t.backend.writes == []


async def test_proposals_are_per_conversation(t):
    await actions.propose_place_call(**PATIENT)
    (p,) = await t.proposals.pending()
    other = make_turn(backend=t.backend, store=t.proposals.store, conversation="conv-2")
    with turn.active(other):
        await actions.execute(p.id)
    assert t.backend.writes == []


async def test_invalid_patient_is_rejected_before_confirmation(t):
    card, data = await actions.propose_place_call(
        **{**PATIENT, "phone": "555-1234", "patient_dob": "3/15/50"}
    )
    assert card is None and len(data["problems"]) == 2 and await t.proposals.pending() == []


async def test_not_allowlisted_number_is_flagged(t):
    card, _ = await actions.propose_place_call(**{**PATIENT, "phone": "+12025550199"})
    assert "Not an approved number" in card.as_text()


async def test_prefix_allowlist_entries_count_as_approved(t):
    """Review A-7."""
    t.backend.state["allowlist"] = [{"phone": "+1202555*"}]
    assert await actions.allowlist_warning(["+12025550123"]) == []
    assert await actions.allowlist_warning(["+13125550123"])


def test_phone_normalisation():
    assert actions.normalize_phone("(202) 555-0123") == "+12025550123"
    assert actions.normalize_phone("1 202 555 0123") == "+12025550123"
    assert actions.normalize_phone("555-0123") == "555-0123"  # too short: left for validation


async def test_bad_form_submit_returns_the_form_with_problems(t):
    card, _ = await actions.submit_call_form(
        {
            "phone": "555",
            "patient_name": "Jordan Rivera",
            "patient_id": "",
            "patient_dob": "",
            "is_diabetic": True,
        }
    )
    text = card.as_text()
    assert text.startswith("New follow-up call") and "Check: phone" in text
    assert await t.proposals.pending() == []
    assert card.data_model["call"]["patient_name"] == "Jordan Rivera"


async def test_good_form_submit_confirms_without_dialing(t):
    card, data = await actions.submit_call_form(
        {
            **PATIENT,
            "phone": "(202) 555-0101",
            "is_diabetic": "true",
            "operation_name": " ",
            "operation_date": "",
        }
    )
    assert data["status"] == "awaiting_user_confirmation" and t.backend.writes == []
    (p,) = await t.proposals.pending()
    body = p.params["body"]
    assert body["phone"] == "+12025550101" and body["is_diabetic"] is True
    assert "operation_name" not in body


def test_csv_import_matches_dashboard_columns():
    raw = (
        b"phone,patient_name,patient_id,patient_dob,operation_name,operation_date,is_diabetic\n"
        b'+12025550103,"Rivera, Jordan",P-1003,1978-06-30,Rotator cuff repair,2026-07-25,true\n\n'
    )
    assert actions.parse_call_list_csv(raw) == [
        {
            "phone": "+12025550103",
            "patient_name": "Rivera, Jordan",
            "patient_id": "P-1003",
            "patient_dob": "1978-06-30",
            "operation_name": "Rotator cuff repair",
            "operation_date": "2026-07-25",
            "is_diabetic": True,
        }
    ]


def test_csv_too_large_is_refused():
    with pytest.raises(ValueError):
        actions.parse_call_list_csv(b"x" * (actions.CSV_MAX_BYTES + 1))


# --- patient matching -------------------------------------------------------------------

ANN = {
    "doc_id": "CA" + "a" * 32,
    "patient_id": "P-4001",
    "patient_name": "Ann Lee",
    "initiated_at": "2026-10-07T10:00:00+00:00",
    "to": "+12025550111",
}
JOANNE = {
    **ANN,
    "doc_id": "CA" + "b" * 32,
    "patient_id": "P-4003",
    "patient_name": "Joanne Lee",
    "initiated_at": "2026-10-07T12:00:00+00:00",
    "to": "+12025550113",
}
ANN_KIM = {
    **ANN,
    "doc_id": "CA" + "c" * 32,
    "patient_id": "P-4002",
    "patient_name": "Ann Kim",
    "initiated_at": "2026-10-07T11:00:00+00:00",
    "to": "+12025550112",
}


@pytest.mark.parametrize(
    "query,expected",
    [
        ("Ann", ["P-4002", "P-4001"]),
        ("ann lee", ["P-4001"]),
        ("Joanne", ["P-4003"]),
        ("lee", ["P-4003", "P-4001"]),
        ("P-4003", ["P-4003"]),
        ("(202) 555-0112", ["P-4002"]),
        ("Jo", ["P-4003"]),
    ],
)
def test_patient_matching_is_by_word_prefix(query, expected):
    got = actions.match_patients(query, [ANN, JOANNE, ANN_KIM])
    assert [c["patient_id"] for c in got] == expected


async def test_an_ambiguous_name_asks_which_patient():
    with turn.active(make_turn(backend=FakeBackend("two_anns"))):
        card, data = await actions.view_call(patient="Ann")
        assert data["status"] == "ambiguous" and len(data["candidates"]) == 2
        text = card.as_text()
        assert "Which patient?" in text and "Ann Lee" in text and "Joanne" not in text
        check(card)


async def test_a_unique_name_resolves_directly():
    with turn.active(make_turn(backend=FakeBackend("two_anns"))):
        _, data = await actions.view_call(patient="Joanne")
        assert data.get("status") != "ambiguous" and data["patient_id"] == "P-4003"


async def test_todays_calls_skip_a_call_without_a_timestamp(t):
    t.backend.state["calls"] = [{**ANN, "initiated_at": None}]
    _, data = await actions.view_calls(today=True)
    assert data["total"] == 0


async def test_past_calls_excludes_today(t):
    from datetime import datetime, timedelta

    from voa_nurse import cards

    now = datetime.now(cards.TZ)
    t.backend.state["calls"] = [
        {**ANN, "doc_id": "CAtoday", "initiated_at": now.isoformat()},
        {**ANN, "doc_id": "CAold", "initiated_at": (now - timedelta(days=2)).isoformat()},
    ]
    card, data = await actions.view_calls(before_today=True)
    assert [c["call_sid"] for c in data["calls"]] == ["CAold"]
    assert card.as_text().startswith("Previous calls")
    card = await actions.handle_click("list_calls", {"before_today": True})
    assert card.as_text().startswith("Previous calls")


# --- redial: the call record never holds the DOB ------------------------------------------


async def test_redial_takes_the_dob_from_the_worklist_entry():
    with turn.active(make_turn()) as t:
        naomi = next(c for c in t.backend.state["calls"] if c["patient_name"] == "Naomi Castillo")
        card, data = await actions.propose_redial(naomi["doc_id"])
        assert data["status"] == "awaiting_user_confirmation", data
        (p,) = await t.proposals.pending()
        assert p.params["body"]["patient_dob"] == "1958-04-12" and t.backend.writes == []


async def test_redial_without_a_dob_shows_the_form():
    with turn.active(make_turn()) as t:
        greg = next(c for c in t.backend.state["calls"] if c["patient_name"] == "Gregory Tan")
        card, data = await actions.propose_redial(greg["doc_id"])
        assert data["status"] == "form_shown" and await t.proposals.pending() == []
        assert card.data_model["call"]["patient_name"] == "Gregory Tan"
        check(card)


async def test_dismiss_discards_the_proposal(t):
    await actions.propose_place_call(**PATIENT)
    (p,) = await t.proposals.pending()
    await actions.handle_click("dismiss", {"proposal_id": p.id})
    assert await t.proposals.pending() == []


async def test_every_confirmable_kind_executes(t):
    """Each propose_* path round-trips through execute to exactly one API write."""
    await actions.propose_add_entries([PATIENT])
    await actions.propose_allow_add("+12025550150", "clinic test phone")
    await actions.propose_allow_remove("+12025550101")
    await actions.propose_cancel_callback("cb-1")
    await actions.propose_cooldown(False)
    entry = t.backend.state["entries"][1]
    await actions.propose_remove_entry(entry_id=entry["entry_id"])
    for p in await t.proposals.pending():
        out = await actions.execute(p.id)
        assert "Not completed" not in out.as_text() and "Unknown action" not in out.as_text()
    kinds = sorted(w[0] for w in t.backend.writes)
    assert kinds == [
        "add_entries",
        "allow_add",
        "allow_remove",
        "cancel_callback",
        "delete_entry",
        "sms_setting",
    ]


async def test_a_status_filter_is_applied_by_the_api(t):
    """Review A-1: filtering server-side keeps the 500-call page relevant."""
    _, data = await actions.view_calls(status="left_vmail")
    assert t.backend.list_calls_status == "left_vmail"
    assert [c["patient"] for c in data["calls"]] == ["Samuel Reyes"]
    await actions.view_calls(status="active")
    assert t.backend.list_calls_status is None  # "active" spans several statuses


# --- recordings (review A-9) ---------------------------------------------------------


async def _recorded_sid(t) -> str:
    c = t.backend.state["calls"][0]
    c["recording_uri"] = "gs://acme-demo-recordings/rec.wav"
    return c["doc_id"]


async def test_play_recording_renders_the_signed_link_in_an_audio_player(t):
    sid = await _recorded_sid(t)
    card = await actions.handle_click("play_recording", {"call_sid": sid})
    comps = check(card)[1]["updateComponents"]["components"]
    (player,) = [c for c in comps if c["component"] == "AudioPlayer"]
    assert player["url"] == (await t.backend.recording_link(sid))["url"]
    assert player["url"].startswith("https://gateway.example.org/recording/")


async def test_play_recording_never_gives_the_model_the_link(t):
    sid = await _recorded_sid(t)
    _, data = await actions.play_recording(sid)
    assert data == {"recording": "shown in the card", "expires_in": 900}
    _, data = await actions.view_call(call_sid=sid)
    assert data["has_recording"] and "Play recording" in data["recording_note"]
    assert "http" not in str(data)


async def test_no_recording_gives_a_plain_card(t):
    sid = t.backend.state["calls"][0]["doc_id"]
    t.backend.state["calls"][0].pop("recording_uri", None)
    card, data = await actions.play_recording(sid)
    check(card)
    assert "No recording for this call." in card.as_text() and "404" not in card.as_text()
    assert "AudioPlayer" not in str(card.messages()) and "http" not in str(data)


async def test_failed_link_gives_a_plain_card(t):
    sid = await _recorded_sid(t)
    t.backend.fail.add("recording_link")
    card, _ = await actions.play_recording(sid)
    check(card)
    text = card.as_text()
    assert "Could not get the recording right now." in text and "503" not in text


# --- patient languages (docs/LANGUAGES-PLAN.md, step 1) ------------------------------


async def test_a_held_language_is_said_on_the_card_before_confirming(t):
    card, data = await actions.propose_place_call(**PATIENT, language="Spanish")
    text = card.as_text()
    assert "Language: Español (Spanish US)" in text
    assert "Calls in Spanish (US) aren't available yet" in text
    (p,) = await t.proposals.pending()
    assert p.params["body"]["language"] == "es-US" and "call_in_english" not in p.params["body"]
    check(card)


async def test_call_in_english_is_sent_and_said(t):
    card, _ = await actions.propose_place_call(**PATIENT, language="vi", call_in_english=True)
    assert "Tiếng Việt (Vietnamese) · will be called in English" in card.as_text()
    assert "aren't available" not in card.as_text()
    (p,) = await t.proposals.pending()
    assert p.params["body"]["call_in_english"] is True
    await actions.execute(p.id)
    assert t.backend.writes[-1][1]["language"] == "vi-VN"


async def test_english_patients_cards_are_unchanged(t):
    card, _ = await actions.propose_place_call(**PATIENT)
    assert "Language" not in card.as_text()
    (p,) = await t.proposals.pending()
    assert "language" not in p.params["body"]


async def test_an_unsupported_language_is_a_problem_before_confirmation(t):
    card, data = await actions.propose_place_call(**PATIENT, language="Tagalog")
    assert card is None and "not one the caller speaks" in data["problems"][0]


async def test_the_api_409_is_a_clear_card(t):
    from voa_nurse.backend import BackendError

    msg = (
        "Jordan Rivera's language is Spanish (US). Calls in Spanish (US) aren't available "
        "yet. Turn on 'Call in English' to call in English."
    )

    async def refuse(body):
        raise BackendError(f"409: {msg}", 409)

    t.backend.place_call = refuse
    await actions.propose_place_call(**PATIENT, language="es-US")
    (p,) = await t.proposals.pending()
    out = await actions.execute(p.id)
    assert out.as_text().startswith("Not called: Jordan Rivera")
    assert msg in out.as_text() and "409" not in out.as_text()


async def test_redial_keeps_the_calls_language():
    with turn.active(make_turn()) as t:
        naomi = next(c for c in t.backend.state["calls"] if c["patient_name"] == "Naomi Castillo")
        naomi["patient_context"] = {**(naomi.get("patient_context") or {}), "language": "es-US"}
        card, data = await actions.propose_redial(naomi["doc_id"], call_in_english=True)
        (p,) = await t.proposals.pending()
        assert p.params["body"]["language"] == "es-US"
        assert p.params["body"]["call_in_english"] is True


async def test_add_entries_carry_language_and_flag_held_patients(t):
    card, data = await actions.propose_add_entries([{**PATIENT, "language": "es"}])
    text = card.as_text()
    assert "Language: Español (Spanish US)" in text and "Held until their language" in text
    (p,) = await t.proposals.pending()
    assert p.params["entries"][0]["language"] == "es-US"


def test_csv_import_reads_the_optional_language_columns():
    raw = (
        b"phone,patient_name,patient_id,patient_dob,language,call_in_english\n"
        b"+12025550103,Maria Lopez,P-1004,1978-06-30,Spanish,yes\n"
    )
    (row,) = actions.parse_call_list_csv(raw)
    assert row["language"] == "Spanish" and row["call_in_english"] is True


def test_the_call_detail_card_shows_the_language():
    from voa_nurse import cards

    configure_cards()
    c = {
        "doc_id": "CA1",
        "to": "+12025550101",
        "patient_name": "Maria Lopez",
        "patient_id": "P-1",
        "language": "es-US",
        "patient_context": {"is_diabetic": False},
    }
    assert "Language: Español (Spanish US)" in cards.call_detail_card(c).as_text()
    assert "Language" not in cards.call_detail_card({**c, "language": "en-US"}).as_text()
