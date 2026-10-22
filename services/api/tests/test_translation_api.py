"""Bilingual transcripts (docs/LANGUAGES-PLAN.md step 2): the post-call
translation task, the transcript API shape and the by-text messages.
Fictional data, fake translator only."""

import json

import pytest
from test_api import NOW, _token, make, run
from voa_api import translation
from voa_core.translate import FakeTranslator

CALL = "CA" + "7" * 32
PHONE = "+12025550101"

SPANISH = {
    "Hola, ¿habla María?": ("es", "Hello, is this María?"),
    "Sí, soy yo.": ("es", "Yes, it's me."),
    "No sé, tal vez un cuatro.": ("es", "I don't know, maybe a four."),
    "Tengo un poco de fiebre, 38 grados.": ("es", "I have a slight fever, 38 degrees."),
}
TURNS = [
    {"role": "agent", "text": "Hola, ¿habla María?", "at_ms": 1000},
    {"role": "patient", "text": "Sí, soy yo.", "at_ms": 3000},
    {"role": "agent", "text": "On a scale of 0 to 10, how is your pain?", "at_ms": 5000},
    {"role": "patient", "text": "No sé, tal vez un cuatro.", "at_ms": 8000},
    {"role": "patient", "text": "Tengo un poco de fiebre, 38 grados.", "at_ms": 9000},
    {"role": "patient", "text": "OK", "at_ms": 9500},
]
CALL_DOC = {
    "call_sid": CALL,
    "to": PHONE,
    "call_status": "success",
    "language": "en-US",
    "patient_name": "María Prueba",
    "follow_up": "urgent_same_day",
    "follow_up_reason": "fever reported",
    "survey_responses": {"fever_reported": "yes", "pain_severity": 4},
    "answered_at": NOW.isoformat(),
}


@pytest.fixture
def setup():
    client, api = make()
    api.translator = FakeTranslator(SPANISH)
    run(api.store.set("calls", CALL, dict(CALL_DOC)))
    run(api.store.set("transcripts", CALL, {"turns": [dict(t) for t in TURNS]}))
    return client, api


def _task(client, **payload):
    r = client.post("/tasks/transcript-translate", json={"call_id": CALL, **payload})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_task_translates_every_turn_and_sets_language_used(setup):
    client, api = setup
    assert _task(client)["transcript"] == {"status": "done", "languages": ["es-US", "en-US"]}
    doc = run(api.store.get("transcripts", CALL))
    turns = doc["turns"]
    assert [t["text"] for t in turns] == [t["text"] for t in TURNS]  # originals untouched
    assert turns[0]["lang"] == "es-US" and turns[0]["text_en"] == "Hello, is this María?"
    assert turns[2]["lang"] == "en-US" and turns[2]["text_en"] is None
    assert turns[3]["text_en"] == "I don't know, maybe a four."
    assert doc["translation"]["status"] == "done"
    assert doc["translation"]["model"] == "fake-translator"
    assert doc["translation"]["at"] == "2026-03-03T15:00:00.000+00:00"
    # the whole transcript in one batch
    assert api.translator.calls == [[t["text"] for t in TURNS]]
    call = run(api.store.get("calls", CALL))
    # 3 of 4 patient turns were Spanish
    assert call["language_used"] == "es-US"
    assert call["languages_spoken"] == ["es-US", "en-US"]


def test_the_task_never_changes_follow_up_triage_or_answers(setup):
    client, api = setup
    _task(client)
    call = run(api.store.get("calls", CALL))
    for k in ("follow_up", "follow_up_reason", "survey_responses", "call_status", "language"):
        assert call[k] == CALL_DOC[k], k


def test_a_second_delivery_skips(setup):
    client, api = setup
    _task(client)
    api.translator.calls.clear()
    assert _task(client)["transcript"] == {"skipped": "already translated"}
    assert api.translator.calls == []


def test_a_failure_keeps_the_original_and_records_failed(setup):
    client, api = setup
    api.translator = FakeTranslator(fail="model unavailable")
    assert _task(client)["transcript"]["status"] == "failed"
    doc = run(api.store.get("transcripts", CALL))
    assert doc["turns"] == TURNS  # nothing added, nothing made up
    assert doc["translation"]["status"] == "failed"
    assert "model unavailable" in doc["translation"]["error"]
    assert "language_used" not in run(api.store.get("calls", CALL))
    # a failed translation is tried again on the next delivery
    api.translator = FakeTranslator(SPANISH)
    assert _task(client)["transcript"]["status"] == "done"


def test_a_short_reply_is_a_failure_not_a_partial_transcript(setup):
    client, api = setup

    class Short(FakeTranslator):
        async def translate_turns(self, turns, *, target="en"):
            return (await super().translate_turns(turns))[:-1]

    api.translator = Short(SPANISH)
    assert _task(client)["transcript"]["status"] == "failed"
    assert run(api.store.get("transcripts", CALL))["turns"] == TURNS


def test_an_english_looking_call_is_still_translated_when_the_patient_spoke(setup):
    # Step 3: looks_english is a hint, never a gate. Unaccented Spanish can look
    # English, so every transcript with a patient turn goes to the model.
    client, api = setup
    turns = [
        {"role": "agent", "text": "Hi, is this Jordan?"},
        {"role": "patient", "text": "Yes"},
    ]
    run(api.store.set("transcripts", CALL, {"turns": turns}))
    assert _task(client)["transcript"] == {"status": "done", "languages": ["en-US"]}
    assert api.translator.calls == [["Hi, is this Jordan?", "Yes"]]
    doc = run(api.store.get("transcripts", CALL))
    assert doc["translation"]["status"] == "done"
    assert doc["turns"][1]["lang"] == "en-US" and doc["turns"][1]["text_en"] is None
    call = run(api.store.get("calls", CALL))
    assert call["language_used"] == "en-US"
    # An English call is not re-checked: nothing about its triage changes.
    assert "translation_check" not in call
    assert call["follow_up"] == CALL_DOC["follow_up"]


def test_an_agent_only_english_transcript_is_skipped_without_a_model_call(setup):
    client, api = setup
    run(
        api.store.set(
            "transcripts",
            CALL,
            {"turns": [{"role": "agent", "text": "Hi, this is the clinic calling back."}]},
        )
    )
    assert _task(client)["transcript"] == {"status": "skipped", "languages": ["en-US"]}
    assert api.translator.calls == []
    doc = run(api.store.get("transcripts", CALL))
    assert doc["translation"]["status"] == "skipped" and "lang" not in doc["turns"][0]


def test_language_used_is_the_majority_of_patient_turns_only(setup):
    client, api = setup
    run(
        api.store.set(
            "transcripts",
            CALL,
            {
                "turns": [
                    {"role": "agent", "text": "Hola, ¿habla María?"},
                    {"role": "agent", "text": "Sí, soy yo."},
                    {"role": "patient", "text": "Yes"},
                    {"role": "patient", "text": "No sé, tal vez un cuatro."},
                    {"role": "patient", "text": "Fine"},
                ]
            },
        )
    )
    _task(client)
    call = run(api.store.get("calls", CALL))
    assert call["language_used"] == "en-US"  # 2 English patient turns of 3
    assert call["languages_spoken"] == ["en-US", "es-US"]


def test_no_transcript_or_a_bad_id_is_skipped(setup):
    client, api = setup
    run(api.store.delete("transcripts", CALL))
    assert _task(client)["transcript"] == {"skipped": "no transcript"}
    r = client.post("/tasks/transcript-translate", json={"call_id": "../x"})
    assert r.json() == {"skipped": "bad call id"}


def test_the_step_3_seam_runs_after_a_done_translation(setup, monkeypatch):
    client, api = setup
    seen = []

    async def seam(api_, call_id, turns, tr):
        seen.append((call_id, turns[3]["text_en"], tr["status"]))

    monkeypatch.setattr(translation, "after_translation", seam)
    _task(client)
    assert seen == [(CALL, "I don't know, maybe a four.", "done")]


# --------------------------------------------------------------------------- the API


def test_transcript_api_shape(setup):
    client, api = setup
    before = client.get(f"/api/calls/{CALL}/transcript").json()
    assert before["translation"] is None and before["language_used"] is None
    assert set(before["turns"][0]) == {"role", "text", "text_en", "lang", "timestamp"}
    assert before["turns"][0]["text_en"] is None
    _task(client)
    t = client.get(f"/api/calls/{CALL}/transcript").json()
    assert t["available"] is True and t["conversation_id"] == CALL
    assert t["language"] == "en-US" and t["language_used"] == "es-US"
    assert t["translation"]["status"] == "done"
    assert t["title"] == "Post-op check"  # the use case's display title
    assert t["turns"][1] == {
        "role": "patient",
        "text": "Sí, soy yo.",
        "text_en": "Yes, it's me.",
        "lang": "es-US",
        "timestamp": "2026-03-03T15:00:03+00:00",
    }


def test_the_task_route_is_for_the_tasks_invoker_only():
    client, _ = make(env="demo")
    body = {"call_id": CALL}
    ui = _token("ui@example.iam.gserviceaccount.com")
    assert client.post("/tasks/transcript-translate", json=body, headers=ui).status_code == 403
    ok = client.post(
        "/tasks/transcript-translate",
        json=body,
        headers=_token("invoker@example.iam.gserviceaccount.com"),
    )
    assert ok.status_code == 200


# --------------------------------------------------------------------------- texts


def _msg(api, mid, direction, body, at):
    run(
        api.store.set(
            "sms_messages",
            mid,
            {
                "phone": PHONE,
                "direction": direction,
                "body": body,
                "call_sid": CALL,
                "at": at,
                "message_sid": None,
            },
        )
    )


def test_text_messages_get_body_en_and_lang(setup):
    client, api = setup
    _msg(api, "m1", "outbound", "How are you feeling today?", "2026-03-03T15:01:00Z")
    _msg(api, "m2", "inbound", "Tengo un poco de fiebre, 38 grados.", "2026-03-03T15:02:00Z")
    assert _task(client, phone=PHONE)["texts"] == {"status": "done", "messages": 2}
    msgs = client.get("/api/sms/messages", params={"phone": PHONE, "call_sid": CALL}).json()[
        "messages"
    ]
    assert msgs[0]["lang"] == "en-US" and msgs[0]["body_en"] is None
    assert msgs[1]["body"] == "Tengo un poco de fiebre, 38 grados."
    assert msgs[1]["body_en"] == "I have a slight fever, 38 degrees." and msgs[1]["lang"] == "es-US"
    # done once
    api.translator.calls.clear()
    assert _task(client, phone=PHONE)["texts"] == {"skipped": "nothing to translate"}
    assert api.translator.calls == []


def test_an_english_thread_is_left_alone(setup):
    client, api = setup
    _msg(api, "m1", "inbound", "pretty good", "2026-03-03T15:02:00Z")
    run(api.store.delete("transcripts", CALL))
    assert _task(client, phone=PHONE)["texts"] == {"skipped": "all English"}
    msgs = client.get("/api/sms/messages", params={"phone": PHONE}).json()["messages"]
    assert "lang" not in msgs[0] and "body_en" not in msgs[0]
    assert "lang" not in json.dumps(msgs)


def test_a_failed_text_translation_leaves_the_messages(setup):
    client, api = setup
    _msg(api, "m2", "inbound", "Tengo un poco de fiebre, 38 grados.", "2026-03-03T15:02:00Z")
    api.translator = FakeTranslator(fail=True)
    assert _task(client, phone=PHONE)["texts"] == {"status": "failed"}
    assert "lang" not in run(api.store.get("sms_messages", "m2"))


def test_filing_a_report_by_text_queues_the_translation():
    from test_sms import CALL as SMS_CALL
    from test_sms import CLEAN_SURVEY, answer_survey, ready
    from test_sms import PHONE as SMS_PHONE

    client, api, _, _ = ready()
    answer_survey(client, CLEAN_SURVEY)
    tasks = [t for t in api.tasks.tasks if t["path"] == "/tasks/transcript-translate"]
    assert len(tasks) == 1
    assert tasks[0]["payload"] == {"call_id": SMS_CALL, "phone": SMS_PHONE}
    assert tasks[0]["name"].startswith(f"xlate-sms-{SMS_CALL}-")
