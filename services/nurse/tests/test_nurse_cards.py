"""Every card validates against the A2UI v0.9 schema, reads like a chart, and never
embeds a private recording URL. No network, no model."""

import json

import pytest
from nurse_fixtures import check, configure_cards
from voa_nurse import cards, triage

CALL = {
    "doc_id": "CA" + "1" * 32,
    "to": "+12025550101",
    "initiated_at": "2026-10-07T14:00:00+00:00",
    "answered_at": "2026-10-07T14:00:09+00:00",
    "duration_seconds": 171,
    "call_status": "success",
    "outcome": "questionnaire_completed",
    "patient_id": "P-1001",
    "patient_name": "Jordan Rivera",
    "follow_up": "urgent_same_day",
    "follow_up_reason": "Fever and pain 8/10.",
    "dob_verification_result": "verified",
    "recording_uri": "gs://acme-demo-recordings/rec.wav",
    "patient_context": {
        "operation_name": "Knee replacement",
        "operation_date": "2026-10-01",
        "is_diabetic": True,
    },
    "survey_responses": {
        "pain_present": "yes",
        "pain_severity": 8,
        "pain_trend": "worse",
        "fever_reported": "yes",
        "incision_concerns": "no",
        "blood_sugar_status": "high",
        "overall_feeling": "Pretty rough",
    },
}
ENTRY = {
    "entry_id": "e" * 32,
    "phone": "+12025550101",
    "patient_name": "Jordan Rivera",
    "patient_id": "P-1001",
    "list_status": "pending",
    "attempts": 0,
}
GREEN, WHITE, RED, ORANGE = "✅", "⚪", "\U0001f534", "\U0001f7e0"


@pytest.fixture(autouse=True)
def _cards():
    configure_cards()


def test_every_card_is_schema_valid():
    run = {
        "active": True,
        "run": {
            "status": "running",
            "total": 3,
            "dispatched": 1,
            "succeeded": 1,
            "failed": 0,
            "in_flight": [{"patient_name": "Jordan Rivera"}],
        },
    }
    for card in [
        cards.note("Two patients need a callback."),
        cards.error_card("boom"),
        cards.calls_card([CALL] * 12, "Calls", filter_args={"status": "success"}),
        cards.calls_card([], "Calls", truncated=True),
        cards.follow_up_card([CALL]),
        cards.follow_up_card([]),
        cards.call_detail_card(CALL),
        cards.call_detail_card({**CALL, "survey_responses": None, "call_status": None}),
        cards.transcript_card(
            CALL, {"available": True, "turns": [{"role": "agent", "text": "Hi"}] * 50}
        ),
        cards.transcript_card(CALL, {"available": False, "reason": "not ready"}),
        cards.call_list_card([ENTRY] * 14, run),
        cards.call_list_card([ENTRY], {"active": False, "run": None}),
        cards.followups_card(
            [
                {
                    "phone": "+12025550101",
                    "state": "scheduled",
                    "patient_name": "Jordan",
                    "message_count": 3,
                    "scheduled_for_text": "tomorrow 10am",
                    "updated_at": "2026-10-07T14:00:00+00:00",
                }
            ],
            [
                {
                    "callback_id": "cb1",
                    "phone": "+12025550101",
                    "run_at": "2026-10-08T14:00:00+00:00",
                    "status": "scheduled",
                }
            ],
            {"invite_cooldown_enabled": False},
        ),
        cards.allowlist_card(
            {
                "mode": "list",
                "entries": [
                    {
                        "phone": "+12025550101",
                        "label": "mobile",
                        "source": "firestore",
                        "editable": True,
                    },
                    {"phone": "+12025550102", "label": "", "source": "env", "editable": False},
                ],
            }
        ),
        cards.confirm_card("abcd1234", "Call Jordan now?", ["line"], "Call now"),
        cards.result_card("Done", ["ok"], buttons=[("Again", cards.event("call_list"))]),
        cards.choose_patient_card("Jo", [CALL, {**CALL, "doc_id": "CA" + "2" * 32}], "view_call"),
        cards.place_call_form_card(),
        cards.place_call_form_card(
            {"patient_name": "Jordan", "is_diabetic": True}, ["Check: phone"]
        ),
        cards.components_check_card(),
        cards.recording_card(CALL, "https://gateway.example.org/recording/CA1?exp=1&sig=ab"),
    ]:
        check(card)


def test_surface_ids_are_fresh_per_card():
    a, b = cards.note("x").messages(), cards.note("x").messages()
    assert a[0]["createSurface"]["surfaceId"] != b[0]["createSurface"]["surfaceId"]


def test_messages_are_v09_in_order():
    msgs = cards.result_card("Done", []).messages()
    assert [next(k for k in m if k != "version") for m in msgs] == [
        "createSurface",
        "updateComponents",
    ]
    assert all(m["version"] == "v0.9" for m in msgs)
    assert msgs[0]["createSurface"]["catalogId"] == cards.V09_BASIC


def test_detail_card_reads_like_a_chart():
    text = cards.call_detail_card(CALL).as_text()
    assert "MRN P-1001" in text and "DM: Yes" in text
    assert "Procedure: Knee replacement on 10/01/2026 · called POD 6" in text
    assert "(!) Pain: Reports pain 8/10" in text and "(!) Fever: Reports fever" in text
    assert "Incision / site: Denies concerns" in text and "Urgent: RN callback today" in text
    assert "Call: Completed" in text and "2m 51s" in text


SIGNED = "https://gateway.example.org/recording/CA1?exp=1792000000&sig=abc123"


def test_detail_card_plays_the_recording_never_the_private_api():
    """Review A-9: the detail card asks for a signed link by event; it never carries
    an AudioPlayer or any API / gs:// URL itself."""
    card = cards.call_detail_card(CALL)
    blob = json.dumps(check(card))
    assert "AudioPlayer" not in blob and "gs://" not in blob and "/recording" not in blob
    assert {"name": "play_recording", "context": {"call_sid": CALL["doc_id"]}} in [
        c["action"].get("event")
        for c in json.loads(blob)[1]["updateComponents"]["components"]
        if c["component"] == "Button"
    ]
    assert "https://dashboard.example.org" in blob
    text = card.as_text()
    assert "[Play recording]" in text and "[Open in dashboard]" in text


def test_no_recording_means_no_play_button():
    text = cards.call_detail_card({**CALL, "recording_uri": None}).as_text()
    assert "Play recording" not in text and "Open in dashboard" not in text


def test_recording_card_plays_the_signed_link():
    card = cards.recording_card(CALL, SIGNED)
    comps = check(card)[1]["updateComponents"]["components"]
    (player,) = [c for c in comps if c["component"] == "AudioPlayer"]
    assert player == {
        "id": player["id"],
        "component": "AudioPlayer",
        "url": SIGNED,
        "description": "Call recording, Jordan Rivera",
    }
    text = card.as_text()
    assert "Call recording: Jordan Rivera" in text and "2m 51s" in text
    assert cards.RECORDING_TTL_NOTE in text
    assert "[Back to call]" in text and "[Open in dashboard]" in text and "[Main menu]" in text
    assert SIGNED not in text  # text mode never prints the bearer link
    card.add_menu_button()  # the executor's "way home" must not add a second one
    assert card.as_text().count("[Main menu]") == 1


def test_no_dashboard_configured_means_no_link_button():
    cards.configure(timezone="America/New_York", dashboard_url="")
    card = cards.call_detail_card(CALL)
    check(card)
    assert "openUrl" not in json.dumps(card.messages())
    assert "[Play recording]" in card.as_text()
    rec = cards.recording_card(CALL, "https://gateway.example.org/recording/CA1?exp=1&sig=ab")
    check(rec)
    assert "openUrl" not in json.dumps(rec.messages())


def test_ui_check_card_embeds_no_recording_or_patient_data():
    """Review A-6."""
    blob = json.dumps(cards.components_check_card().messages())
    assert "AudioPlayer" not in blob and "/api/" not in blob and "gs://" not in blob


def test_confirm_card_carries_only_the_id_and_tells_text_users_how():
    card = cards.confirm_card("abcd1234", "Place call?", ["x"], "Place call")
    comps = card.messages()[1]["updateComponents"]["components"]
    actions = [c["action"]["event"] for c in comps if c["component"] == "Button"]
    assert actions[0] == {"name": "confirm", "context": {"proposal_id": "abcd1234"}}
    assert "reply: confirm abcd1234" in card.as_text()


def test_main_menu_button_returns_home():
    card = cards.result_card("Done", [])
    card.add_menu_button()
    comps = check(card)[1]["updateComponents"]["components"]
    buttons = [c for c in comps if c["component"] == "Button"]
    assert buttons[-1]["action"] == {"event": {"name": "welcome", "context": {}}}


def test_clinical_formats():
    assert cards.duration(171) == "2m 51s" and cards.duration(9) == "9s"
    assert cards.us_date("2026-10-01") == "10/01/2026" and cards.us_date("bad") == "bad"
    assert cards.pod("2026-10-01", "2026-10-03T15:00:00+00:00") == 2
    assert cards.pod(None, "2026-10-03T15:00:00+00:00") is None
    assert cards.phone("+12025550123") == "(202) 555-0123"


@pytest.mark.parametrize("value", [None, "", "yesterday", 12345])
def test_when_tolerates_missing_and_garbled_times(value):
    """Review A-2."""
    assert isinstance(cards.when(value), str)


def test_form_card_keeps_what_was_typed():
    msgs = check(cards.place_call_form_card({"patient_name": "Jordan Rivera"}))
    model = msgs[-1]["updateDataModel"]["value"]["call"]
    assert model["patient_name"] == "Jordan Rivera" and model["is_diabetic"] is False
    assert "phone number" in cards.place_call_form_card().as_text()


# --- colour status markers -----------------------------------------------------------


def test_every_verdict_tone_has_a_marker():
    tones = {
        triage.verdict_for(c)["tone"]
        for c in (
            CALL,
            {**CALL, "follow_up": "emergency"},
            {**CALL, "follow_up": "routine_next_business_day"},
            {**CALL, "follow_up": "informational"},
            {**CALL, "follow_up": "none"},
            {**CALL, "follow_up": None, "outcome": "voicemail_left"},
            {**CALL, "follow_up": None, "outcome": None, "call_status": None},
        )
    }
    assert tones == set(cards.MARK)


@pytest.mark.parametrize(
    "overrides",
    [
        {"outcome": "voicemail_left", "follow_up": None, "call_status": "left_vmail"},
        {"outcome": "voicemail_left", "follow_up": "none", "call_status": "left_vmail"},
        {"outcome": None, "follow_up": None, "call_status": "rna"},
        {"outcome": None, "follow_up": None, "call_status": None},
        {"outcome": "reschedule_requested", "follow_up": None},
        {"outcome": "identity_verification_failed", "follow_up": "none"},
    ],
)
def test_a_patient_nobody_assessed_never_gets_the_green_check(overrides):
    c = {**CALL, **overrides}
    assert cards.mark(c)[0] == WHITE
    assert GREEN not in cards.calls_card([c], "Calls").as_text()


def test_calls_card_leads_with_a_colour_summary():
    calls = [
        CALL,
        {**CALL, "doc_id": "CA2", "follow_up": "emergency"},
        {**CALL, "doc_id": "CA3", "follow_up": "none"},
        {**CALL, "doc_id": "CA4", "follow_up": "none"},
    ]
    text = cards.calls_card(calls, "Calls").as_text()
    assert (
        f"{RED} 1 Emergency · {ORANGE} 1 RN callback today · {GREEN} 2 No follow-up needed" in text
    )


def test_truncated_lists_say_so():
    """Review A-1: a bounded list is labelled, not passed off as complete."""
    text = cards.follow_up_card([CALL], truncated=True).as_text()
    assert "Based on the newest 1 calls" in text
