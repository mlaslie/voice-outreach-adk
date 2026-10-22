"""Texts in Spanish (US), end to end (docs/LANGUAGES-PLAN.md step 5): the invite,
the menu, the DOB gate, every post-op question, a patient question and the
closing, all in the pack's Spanish, while the recorded answers and the triage
stay English. Fictional data; a FakeTranslator stands in for Gemini.

The repo's Spanish pack is a draft, so each test that sends "for real" (FakeSms,
not the dry-run simulator) points the pack directory at an approved copy."""

from __future__ import annotations

import pytest
import yaml
from test_sms import CALL, PHONE, _language_call, consent, invite, make, run, session, text
from voa_api import sms_service as sms
from voa_core import language_packs as lp
from voa_core import languages
from voa_core.campaign import default_config_dir, load_campaign
from voa_core.copy import SiteCopy
from voa_core.sms import states as st
from voa_core.sms_copy import PackCopy
from voa_core.translate import FakeTranslator

I18N = default_config_dir() / "campaigns" / "i18n"

#: What the fake translator returns: (language, English). Anything not listed is
#: English (the model's "already English" answer).
SPANISH = {
    "bastante bien, gracias": ("es", "pretty well, thanks"),
    "sí": ("es", "yes"),
    "no": ("es", "no"),
    "ocho": ("es", "eight"),
    "en la rodilla derecha, como un pinchazo": ("es", "in the right knee, like a stabbing"),
    "peor": ("es", "worse"),
    "solo la pierna izquierda y cada día está más hinchada": (
        "es",
        "only the left leg and it gets more swollen every day",
    ),
    "no sé": ("es", "I don't know"),
    "¿Cuándo puedo volver a manejar?": ("es", "When can I drive again?"),
    "mañana 3pm": ("es", "tomorrow 3pm"),
}


def _approved_dir(tmp_path):
    doc = yaml.safe_load((I18N / "post_op.es-US.yaml").read_text())
    doc.update(
        status="approved",
        reviewed_by="Test Translator, CMI",
        approved_by="Test Approver",
        approved_at="2026-10-22",
    )
    d = tmp_path / "campaigns" / "i18n"
    d.mkdir(parents=True)
    (d / "post_op.es-US.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True))
    return d


@pytest.fixture
def approved(tmp_path, monkeypatch):
    d = _approved_dir(tmp_path)
    monkeypatch.setattr(languages, "pack_dir", lambda config_dir=None: d)
    return d


def _es():
    pack = lp.sms_pack("post_op", "es-US")
    assert pack is not None and pack.approved
    return PackCopy(pack.sms_copy, "es-US", approved=True), lp.localize_sms(
        load_campaign("post_op"), pack
    )


def _patient(translator=None, **cfg):
    client, api, clock = make(**cfg)
    api.translator = translator or FakeTranslator(SPANISH)
    _language_call(api, language="es-US", call_in_english=False)
    consent(client)
    res = invite(client, api)
    assert res[0].json() == {"sent": True}
    return client, api


def _site(api) -> SiteCopy:
    return sms.site_copy(api)


def _out(api) -> list[str]:
    return api.sms.bodies()


# --------------------------------------------------------------------------- the conversation


def test_a_full_spanish_conversation(approved):
    client, api = _patient()
    es, c = _es()
    site = _site(api)
    assert session(api)["sms_language"] == "es-US"

    # The invite: Spanish, consent wording, STOP still advertised in English.
    invite_text = _out(api)[-1]
    assert invite_text == es.invite("Jordan Rivera", site)
    assert invite_text.startswith("Acme Health tiene un mensaje de seguimiento para Jordan.")
    assert "3 = responder por texto" in invite_text
    assert invite_text.endswith("Responda STOP para cancelar.")

    # "3" for text (a digit: never translated).
    text(client, "3")
    assert _out(api)[-1] == es.ask_dob("Jordan Rivera")
    assert "DD/MM/AAAA" in _out(api)[-1]

    # The DOB, day first.
    text(client, "15/03/1950")
    s = session(api)
    assert s["state"] == st.STATE_SURVEY
    plan = s["survey_plan"]
    from voa_core import copy as messages

    assert _out(api)[-2] == messages.survey_preamble(len(plan), c)
    assert _out(api)[-2].startswith("Gracias, quedó verificado.")
    assert _out(api)[-1] == messages.question(1, len(plan), c.question("overall_feeling").sms, c)
    assert _out(api)[-1] == f"(1/{len(plan)}) ¿Cómo se siente en general desde su procedimiento?"

    replies = [
        ("bastante bien, gracias", c.question("pain_present").sms),
        ("sí", c.question("pain_severity").sms),
        ("ocho", c.question("pain_location").sms),
        ("en la rodilla derecha, como un pinchazo", c.question("pain_trend").sms),
        ("peor", c.question("fever_reported").sms),
        ("no", c.question("incision_concerns").sms),
        ("no", c.question("legs_swollen").sms),
        ("sí", c.question("swelling_description").sms),
        ("solo la pierna izquierda y cada día está más hinchada", c.question("sleeping_well").sms),
        # "no sé" is never "no": asked once more (in Spanish), as an English
        # "I don't know" is, then recorded as unknown.
        ("no sé", "Disculpe, no entendí. (6/7) " + c.question("sleeping_well").sms),
        ("no sé", c.question("has_questions").sms),
    ]
    for body, want in replies:
        text(client, body)
        assert _out(api)[-1].endswith(want), (body, _out(api)[-1])

    # A patient question, then "no" for no more.
    text(client, "¿Cuándo puedo volver a manejar?")
    # Echoed in the patient's own words (recorded in English, below).
    assert _out(api)[-1] == messages.question_noted("¿Cuándo puedo volver a manejar?", c)
    assert _out(api)[-1].startswith('Anotado: "¿Cuándo puedo volver a manejar?".')
    text(client, "no")

    # The recorded answers are English values; the patient's words are kept.
    report = [d for _, d in run(api.store.query("reports"))][0]
    sr = report["survey_responses"]
    assert sr["overall_feeling"] == "pretty well, thanks"
    assert sr["pain_present"] == "yes" and sr["pain_severity"] == 8
    assert sr["pain_trend"] == "worse"
    assert sr["fever_reported"] == "no" and sr["incision_concerns"] == "no"
    assert sr["legs_swollen"] == "yes"
    assert sr["sleeping_well"] == "unknown"  # "no sé" is never "no"
    assert sr["_original"]["pain_severity"] == "ocho"
    assert sr["_original"]["sleeping_well"] == "no sé"
    q = sr["patient_questions"][0]
    assert q["text"] == "When can I drive again?"
    assert q["text_original"] == "¿Cuándo puedo volver a manejar?"
    # The triage: pain 8/10, worsening pain, one-sided worsening swelling.
    assert report["follow_up"] == "urgent_same_day"
    reasons = report["follow_up_reason"]
    assert "pain 8/10" in reasons and "pain worsening" in reasons

    # The closing: Spanish, urgent, with the questions line and the medication reminder.
    closing = _out(api)[-1]
    assert closing == messages.survey_done_urgent(True, c)
    assert closing.startswith("Gracias, es todo.")
    assert "Lleve una lista de sus medicamentos a su cita de seguimiento." in closing
    assert "Le responderán sus preguntas." in closing and "911" in closing
    assert session(api)["state"] == st.STATE_CLOSED

    # Every text this patient got was the pack's Spanish (no English copy).
    english = load_campaign("post_op")
    for body in _out(api):
        assert "Reply" not in body and "Thank you" not in body, body
    assert english.question("pain_present").sms not in _out(api)


def test_a_spanish_red_flag_mid_survey_gets_the_spanish_emergency_text(approved):
    client, api = _patient(FakeTranslator(SPANISH))
    es, _ = _es()
    text(client, "3")
    text(client, "15/03/1950")
    r = text(client, "me duele el pecho y no puedo respirar")  # checked on the original
    assert r.json()["effect"] == "file_emergency"
    assert _out(api)[-1] == es.emergency()
    assert "911" in _out(api)[-1] and "tel:911" in _out(api)[-1]
    assert run(api.store.get("calls", CALL))["follow_up"] == "emergency"


def test_alto_opts_out_in_spanish_and_ayuda_helps_in_spanish(approved):
    client, api = _patient(reply_to_compliance_keywords=True)
    es, _ = _es()
    site = _site(api)
    r = text(client, "AYUDA")
    assert r.json()["note"] == "help"
    assert _out(api)[-1] == es.help_message(site)
    assert "Responda STOP para cancelar." in _out(api)[-1] and "911" in _out(api)[-1]
    r = text(client, "ALTO")
    assert r.json()["effect"] == "opt_out"
    assert _out(api)[-1] == es.opted_out(site)
    assert _out(api)[-1] == "Ya no recibirá mensajes de seguimiento de Acme Health."
    assert session(api)["state"] == st.STATE_OPTED_OUT
    # STOP was advertised on the invite, in English.
    assert "STOP" in _out(api)[0]
    assert api.translator.calls == []  # keywords are read on the original


def test_a_time_in_spanish_is_booked_and_read_back_in_spanish(approved):
    client, api = _patient()
    text(client, "2")
    assert _out(api)[-1].startswith("¿Qué día y hora le conviene?")
    text(client, "mañana 3pm")
    assert _out(api)[-1] == "Su llamada queda para el mié 4 mar, 3:00 PM. Responda C para cancelar."
    assert session(api)["scheduled_for_text"] == "mié 4 mar, 3:00 PM"
    # "C" is never sent to the translator, and cancels.
    api.translator = FakeTranslator(fail=True)
    r = text(client, "C")
    assert r.json()["effect"] == "cancel_booking"
    assert _out(api)[-1].startswith("Cancelado: no le llamaremos.")


def test_call_in_english_still_gets_english(approved):
    client, api, _ = make()
    api.translator = FakeTranslator(SPANISH)
    _language_call(api, language="es-US", call_in_english=True)
    consent(client)
    invite(client, api)
    assert "sms_language" not in session(api)
    assert _out(api)[-1].startswith("Acme Health has a follow-up message for Jordan.")


# --------------------------------------------------------------------------- the gates


def test_a_draft_pack_is_never_used_for_a_real_text(approved, monkeypatch):
    client, api = _patient()
    sent = len(_out(api))
    # The pack goes back to its draft (or is retired) mid-conversation.
    monkeypatch.setattr(languages, "pack_dir", lambda config_dir=None: I18N)
    text(client, "3")
    assert len(_out(api)) == sent  # nothing: not the draft, not English
    assert sms.wording_block(api, session(api)) == "No approved Spanish (US) text wording."
    with pytest.raises(sms.SendBlocked, match="No approved Spanish"):
        run(sms.send_sms(api, PHONE, "Hola"))


def test_a_draft_flag_on_a_real_session_does_not_unlock_drafts():
    _, api, _ = make()
    s = {
        "sms_language": "es-US",
        "sms_draft_wording": True,
        "patient_context": {"language": "es-US"},
    }
    assert sms.wording_block(api, s) == "No approved Spanish (US) text wording."
    # Even with the simulator's flag, a real (non dry-run) provider refuses it.
    s["patient_context"][sms.SIMULATOR_FLAG] = True
    assert sms.wording_block(api, s) == "No approved Spanish (US) text wording."
    assert sms.wording_block(api, {"patient_context": {}}) is None  # English: unchanged


def test_the_draft_never_opens_a_spanish_conversation():
    client, api, _ = make()
    _language_call(api, language="es-US", call_in_english=False)
    consent(client)
    r = client.post("/tasks/sms-missed-call", json={"call_id": CALL})
    assert r.json() == {"skipped": "no approved Spanish (US) text wording"}
    assert not api.sms.sent


def test_the_budget_counts_ucs2_segments(approved):
    client, api = _patient()
    from voa_core.sms_copy import segments

    def used():
        return run(api.store.get("rate_limits", sms.BUDGET_NAME))["count"]

    before = used()
    es, _ = _es()
    body = es.emergency()
    assert segments(body).unicode and segments(body).segments == 3
    run(sms.send_sms(api, PHONE, body))
    assert used() == before + 3
    run(sms.send_sms(api, PHONE, "Hola"))  # GSM-7: one, as always
    assert used() == before + 4
    assert sms.budget_units("a" * 400) == 1  # English stays one per message
