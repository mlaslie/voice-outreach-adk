"""The safety layer for other languages, end to end (docs/LANGUAGES-PLAN.md §3.3,
step 3): the post-call re-check on the translated transcript, and inbound texts
in another language. Fictional data, fake translators only."""

from __future__ import annotations

import pytest
from test_api import make as make_api
from test_api import run
from test_sms import CALL as SMS_CALL
from test_sms import PHONE, _language_call, consent, invite, session, text
from test_sms import make as make_sms
from voa_core.sms import states as st
from voa_core.translate import FakeTranslator

CALL = "CA" + "8" * 32

SPANISH = {
    "Hola, ¿habla Lucía?": ("es", "Hello, is this Lucía?"),
    "Sí, soy yo.": ("es", "Yes, it's me."),
    "Me duele el pecho desde anoche.": ("es", "My chest has been hurting since last night."),
    "No, no tengo dolor en el pecho.": ("es", "No, I don't have chest pain."),
    "Estoy un poco preocupada.": ("es", "I'm a little worried."),
    "la rodilla derecha": ("es", "the right knee"),
    "¿Puedo comer normal si tengo fiebre?": ("es", "Can I eat normally if I have a fever?"),
}


def _seed(api, turns, **call):
    doc = {
        "call_sid": CALL,
        "to": PHONE,
        "call_status": "success",
        "language": "es-US",
        "patient_name": "Lucía Prueba",
        "outcome": "questionnaire_completed",
        "follow_up": "routine_next_business_day",
        "follow_up_reason": "Patient reports pain 5/10.",
        "follow_up_reasons": [
            {"tier": "routine_next_business_day", "text": "pain 5/10", "source": "triage"}
        ],
        "survey_responses": {"pain_present": "yes", "pain_severity": 5},
        **call,
    }
    run(api.store.set("calls", CALL, doc))
    run(api.store.set("transcripts", CALL, {"turns": turns}))


def _turns(*patient):
    out = [{"role": "agent", "text": "Hola, ¿habla Lucía?"}]
    for p in patient:
        out.append({"role": "patient", "text": p})
    return out


def _task(client):
    r = client.post("/tasks/transcript-translate", json={"call_id": CALL})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def app():
    client, api = make_api()
    api.translator = FakeTranslator(SPANISH)
    return client, api


# --------------------------------------------------------------------------- B: the re-check


def test_a_red_flag_on_the_translated_transcript_raises_to_emergency(app):
    client, api = app
    _seed(api, _turns("Sí, soy yo.", "Me duele el pecho desde anoche."))
    out = _task(client)
    assert out["transcript"]["raised_to"] == "emergency"
    call = run(api.store.get("calls", CALL))
    assert call["follow_up"] == "emergency"
    top = call["follow_up_reasons"][0]
    assert top["source"] == "translation_check" and top["tier"] == "emergency"
    assert top["text"].startswith("red flag found on translated transcript: ")
    assert call["follow_up_reasons"][1]["text"] == "pain 5/10"  # kept
    check = call["translation_check"]
    assert check["status"] == "done" and check["raised_to"] == "emergency"
    assert check["from"] == "routine_next_business_day"
    # The nurse console's emergency banner reads the open follow-ups.
    rows = client.get("/api/follow-ups/open").json()["calls"]
    assert [(r["doc_id"], r["follow_up"]) for r in rows] == [(CALL, "emergency")]
    # Logged and audited.
    audit = [d for _, d in run(api.store.query("audit_log"))]
    assert audit[-1]["action"] == "translation_check.raised"
    assert audit[-1]["target"] == {"call_id": CALL} and audit[-1]["to"] == "emergency"


def test_a_negated_red_flag_raises_nothing(app):
    client, api = app
    _seed(api, _turns("No, no tengo dolor en el pecho."))
    _task(client)
    call = run(api.store.get("calls", CALL))
    assert call["follow_up"] == "routine_next_business_day"
    assert call["translation_check"]["raised_to"] is None


def test_the_recheck_never_lowers(app):
    client, api = app
    _seed(api, _turns("Estoy un poco preocupada."), follow_up="emergency")
    _task(client)
    call = run(api.store.get("calls", CALL))
    assert call["follow_up"] == "emergency"
    assert [f["kind"] for f in call["translation_check"]["findings"]] == ["expressed_worry"]


def test_translated_answers_and_questions_are_rechecked(app):
    client, api = app
    sr = {
        "pain_present": "yes",
        "pain_severity": 5,
        "pain_location": "la rodilla derecha",
        "overall_feeling": "Estoy un poco preocupada.",
        "patient_questions": [
            {
                "text": "¿Puedo comer normal si tengo fiebre?",
                "text_original": "¿Puedo comer normal si tengo fiebre?",
                "lang": "es-US",
                "category": "other",
                "priority": "urgent_same_day",
            }
        ],
    }
    _seed(api, _turns("Sí, soy yo."), survey_responses=sr)
    _task(client)
    call = run(api.store.get("calls", CALL))
    assert call["follow_up"] == "urgent_same_day"
    kinds = {f["kind"] for f in call["translation_check"]["findings"]}
    assert {"expressed_worry", "question"} <= kinds
    q = call["survey_responses"]["patient_questions"][0]
    assert q["text_en"] == "Can I eat normally if I have a fever?"
    assert q["priority"] == "urgent_same_day" and q["category"] == "symptom"
    assert q["text_original"] == "¿Puedo comer normal si tengo fiebre?"


def test_the_recheck_is_idempotent(app):
    client, api = app
    _seed(api, _turns("Me duele el pecho desde anoche."))
    _task(client)
    first = run(api.store.get("calls", CALL))
    from voa_api import translation

    out = run(translation.recheck(api, CALL, run(api.store.get("transcripts", CALL))["turns"]))
    assert out == {"skipped": "already checked"}
    assert run(api.store.get("calls", CALL)) == first


def test_a_signed_off_review_is_reopened_and_its_export_withdrawn(app):
    client, api = app
    _seed(
        api,
        _turns("Me duele el pecho desde anoche."),
        review={"status": "signed_off", "by": "nurse@example.org", "at": "2026-03-03T14:00:00Z"},
    )
    run(api.store.set("ehr_outbox", CALL, {"status": "pending_export"}))
    _task(client)
    call = run(api.store.get("calls", CALL))
    assert call["follow_up"] == "emergency"
    assert call["review"]["status"] == "reopened"
    assert call["review"]["reopened_by"] == "translation_check"
    assert call["translation_check"]["reopened"] is True
    assert run(api.store.get("ehr_outbox", CALL))["status"] == "withdrawn"
    assert [r["doc_id"] for r in client.get("/api/follow-ups/open").json()["calls"]] == [CALL]


def test_an_english_call_is_not_rechecked(app):
    client, api = app
    _seed(
        api,
        [
            {"role": "agent", "text": "Any chest pain?"},
            {"role": "patient", "text": "chest pain? no"},
        ],
        language="en-US",
    )
    _task(client)
    call = run(api.store.get("calls", CALL))
    assert call["follow_up"] == "routine_next_business_day"
    assert "translation_check" not in call


def test_unaccented_spanish_is_translated_and_rechecked(app):
    # "dolor de pecho" can look English; it is no longer skipped (step 3, A).
    client, api = app
    api.translator = FakeTranslator({"dolor de pecho": ("es", "chest pain")})
    _seed(api, [{"role": "patient", "text": "dolor de pecho"}], language="en-US")
    _task(client)
    assert run(api.store.get("calls", CALL))["follow_up"] == "emergency"


def test_a_failed_translation_of_a_spanish_call_goes_to_a_nurse(app):
    client, api = app
    api.translator = FakeTranslator(fail="model unavailable")
    _seed(api, _turns("Me duele el pecho desde anoche."), follow_up="none")
    assert _task(client)["transcript"]["status"] == "failed"
    call = run(api.store.get("calls", CALL))
    assert call["follow_up"] == "urgent_same_day"
    assert call["follow_up_reasons"][0]["text"].startswith("couldn't read the transcript in")
    assert call["translation_check"]["status"] == "failed"
    # A later delivery that translates runs the full re-check.
    api.translator = FakeTranslator(SPANISH)
    _task(client)
    call = run(api.store.get("calls", CALL))
    assert call["follow_up"] == "emergency" and call["translation_check"]["status"] == "done"


# --------------------------------------------------------------------------- D: texts


def _spanish_patient(translator=None):
    client, api, clock = make_sms()
    api.translator = translator or FakeTranslator()
    _language_call(api, language="es-US", call_in_english=True)
    consent(client)
    invite(client, api)
    return client, api


def test_alto_opts_out():
    client, api = _spanish_patient()
    r = text(client, "ALTO")
    assert r.json()["effect"] == "opt_out"
    assert session(api)["state"] == st.STATE_OPTED_OUT
    assert api.translator.calls == []  # checked on the original, never translated


def test_a_spanish_red_flag_by_text_gets_the_911_reply_without_translation():
    client, api = _spanish_patient(FakeTranslator(fail=True))
    r = text(client, "me duele el pecho y no puedo respirar")
    assert r.json()["effect"] == "cancel_all"
    assert "911" in api.sms.bodies()[-1]


def test_a_red_flag_only_the_translation_shows_triggers_the_emergency_path():
    t = FakeTranslator({"Tôi bị ngất xỉu": ("vi", "I fainted")})
    client, api = _spanish_patient(t)
    r = text(client, "Tôi bị ngất xỉu")
    assert r.json()["effect"] == "cancel_all"


def test_a_spanish_reply_is_translated_then_read_and_the_original_kept():
    t = FakeTranslator(
        {
            "por texto": ("es", "by text"),
            "15 de marzo de 1950": ("es", "March 15, 1950"),
            "bastante bien": ("es", "pretty good"),
            "no sé": ("es", "I don't know"),
        }
    )
    client, api = _spanish_patient(t)
    text(client, "por texto")
    assert session(api)["state"] == st.STATE_AWAITING_DOB
    text(client, "15 de marzo de 1950")
    assert session(api)["state"] == st.STATE_SURVEY
    text(client, "bastante bien")
    answers = session(api)["survey_answers"]
    assert answers["overall_feeling"] == "pretty good"
    assert answers["_original"]["overall_feeling"] == "bastante bien"
    text(client, "no sé")  # pain_present: never "no"
    assert "pain_present" not in session(api)["survey_answers"]
    msgs = client.get("/api/sms/messages", params={"phone": PHONE}).json()["messages"]
    inbound = [m for m in msgs if m["direction"] == "inbound"]
    assert inbound[0]["body"] == "por texto" and inbound[0]["body_en"] == "by text"
    assert inbound[0]["lang"] == "es-US"
    assert "body_en" not in inbound[1]  # the DOB reply is never stored


def test_a_day_first_birthdate_by_text():
    client, api = _spanish_patient()
    text(client, "3")
    text(client, "15/03/1950")
    assert session(api)["state"] == st.STATE_SURVEY


def test_an_unreadable_text_is_never_no_and_goes_to_a_nurse():
    client, api = _spanish_patient(FakeTranslator(fail=True))
    before = session(api)["state"]
    r = text(client, "quizás mañana por la tarde")
    assert r.json()["note"] == "translation-failed"
    assert session(api)["state"] == before
    call = run(api.store.get("calls", SMS_CALL))
    assert call["follow_up"] == "urgent_same_day"
    assert call["follow_up_reasons"][0]["text"].startswith(
        "couldn't read a text reply in Spanish (US)"
    )
    assert call["text_translation_check"]["raised_to"] == "urgent_same_day"


def test_a_question_by_text_in_another_language_keeps_the_original():
    t = FakeTranslator(
        {
            "Когда я смогу водить машину?": ("ru", "When will I be able to drive?"),
        }
    )
    client, api, clock = make_sms()
    api.translator = t
    _language_call(api, language="ru-RU", call_in_english=True)
    consent(client)
    invite(client, api)
    text(client, "3")
    text(client, "15.03.1950")
    for a in ("pretty good", "no", "no", "no", "no", "yes"):
        text(client, a)
    text(client, "Когда я смогу водить машину?")
    qs = session(api)["survey_answers"]["patient_questions"]
    assert qs[0]["text"] == "When will I be able to drive?"
    assert qs[0]["text_original"] == "Когда я смогу водить машину?"
    assert qs[0]["lang"] == "ru-RU" and qs[0]["category"] == "activity"


def test_an_english_patient_texting_english_is_never_translated():
    client, api, clock = make_sms()
    api.translator = FakeTranslator(fail=True)
    from test_sms import seed_call

    seed_call(api)
    consent(client)
    invite(client, api)
    r = text(client, "2")
    assert r.json()["note"] == "ask-time"
    assert api.translator.calls == []


# --------------------------------------------------------------------------- F: knowledge


def test_a_spanish_knowledge_item_cannot_be_created():
    client, api = make_api()
    r = client.post(
        "/api/admin/knowledge/items",
        json={"question": "¿Cuándo puedo ducharme?", "answer": "Después de 48 horas.",
              "category": "activity"},
    )  # fmt: skip
    assert r.status_code == 422 and "language packs" in r.json()["detail"]
