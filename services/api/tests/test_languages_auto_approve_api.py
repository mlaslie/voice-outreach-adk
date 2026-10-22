"""The demo auto-approve through the API (docs/LANGUAGES-PLAN.md, "Language
availability and approval"): GET /api/languages, nobody held, the call doc's
``language_source``, texts in a language without a pack machine-translated at
send time (STOP / 911 / C kept, English on failure, never a mix), the Spanish
draft used as is, and the simulator. Fictional data; a FakeTranslator stands in
for Gemini."""

from __future__ import annotations

import pytest
import test_nurse_console_api as console
import test_sms as t
from voa_api import sms_service as sms
from voa_core import languages, translate
from voa_core.config import load_config
from voa_core.sms_copy import ENGLISH
from voa_core.translate import FakeTranslator


def _switch_on() -> None:
    # Each test's make() loads its own config (switch off); turning it on after
    # that is what a demo service's startup does.
    load_config(
        environ={
            "VOA__GCP__PROJECT_ID": "test-project",
            "VOA_ENV": "test",
            "VOA__LANGUAGES__AUTO_APPROVE": "true",
        }
    )
    assert languages.auto_approved()


@pytest.fixture(autouse=True)
def _reset():
    languages.configure(None)
    translate.clear_outbound_cache()
    yield
    languages.configure(None)
    translate.clear_outbound_cache()


def vi(text: str, target: str) -> str:
    """A fake Vietnamese: keeps STOP, 911, tel:911 and C (like the real prompt asks)."""
    return f"[VI] {text}"


# --------------------------------------------------------------------------- GET /api/languages


def test_the_endpoint_reports_auto_approve_and_the_source_of_each_language():
    client, _, _ = console.make()
    off = client.get("/api/languages").json()
    assert "auto_approved" not in off and "sources_by_campaign" not in off
    _switch_on()
    body = client.get("/api/languages").json()
    assert body["auto_approved"] is True
    assert body["available_by_campaign"]["post_op"] == sorted(languages.CODES)
    by = {x["code"]: x for x in body["languages"]}
    assert all(x["available"] for x in body["languages"])
    assert (by["en-US"]["source"], by["en-US"]["sms_source"]) == ("approved", "approved")
    assert (by["es-US"]["source"], by["es-US"]["sms_source"]) == ("draft_pack", "draft_pack")
    assert (by["vi-VN"]["source"], by["vi-VN"]["sms_source"]) == ("machine", "machine")
    assert body["sources_by_campaign"]["pre_op"]["es-US"] == {
        "voice": "draft_pack",
        "sms": "machine",
    }
    assert body["sources_by_campaign"]["post_op"]["ko-KR"] == {"voice": "machine", "sms": "machine"}


# --------------------------------------------------------------------------- calls


VIETNAMESE = {
    **console.ENTRY,
    "patient_name": "Tran Thi Lan",
    "patient_id": "P-VI1",
    "language": "vi",
}


def test_nobody_is_held_and_the_call_doc_records_the_wording_source():
    client, api, _ = console.make()
    assert client.post("/api/place-call", json=VIETNAMESE).status_code == 409  # off: held
    _switch_on()
    r = client.post("/api/place-call", json=VIETNAMESE)
    assert r.status_code == 200, r.text
    call = console.calls(api)[r.json()["call_sid"]]
    assert call["language"] == "vi-VN" and call["language_source"] == "machine"
    r = client.post("/api/place-call", json={**VIETNAMESE, "language": "es-US"})
    assert console.calls(api)[r.json()["call_sid"]]["language_source"] == "draft_pack"
    # "Call in English" forces English: no source.
    r = client.post("/api/place-call", json={**VIETNAMESE, "call_in_english": True})
    assert "language_source" not in console.calls(api)[r.json()["call_sid"]]
    # The transcript response carries it for the console.
    assert client.get(f"/api/calls/{r.json()['call_sid']}").status_code == 200


def test_an_english_call_has_no_source_either_way():
    client, api, _ = console.make()
    _switch_on()
    r = client.post("/api/place-call", json=console.ENTRY)
    assert "language_source" not in console.calls(api)[r.json()["call_sid"]]


# --------------------------------------------------------------------------- texts


def _vietnamese_patient(translator):
    client, api, clock = t.make()
    _switch_on()
    api.translator = translator
    t._language_call(api, language="vi-VN", call_in_english=False)
    t.consent(client)
    return client, api


def test_a_text_in_a_language_without_a_pack_is_machine_translated():
    tr = FakeTranslator(out=vi)
    client, api = _vietnamese_patient(tr)
    res = t.invite(client, api)
    assert res[0].json() == {"sent": True}
    s = t.session(api)
    assert s["sms_language"] == "vi-VN" and s["language_source"] == "machine"
    sent = api.sms.bodies()[-1]
    assert sent.startswith("[VI] ") and "STOP" in sent
    english = sent.removeprefix("[VI] ")
    assert "Reply STOP" in english or "STOP" in english
    msgs = t.run(sms.list_messages(api, t.PHONE))
    assert msgs[-1]["body"] == sent
    assert msgs[-1]["wording"] == {"language": "vi-VN", "source": "machine", "english": english}
    # The DOB prompt asks day first (the reply is read day first).
    t.text(client, "3")
    assert "DD/MM/YYYY" in api.sms.bodies()[-1] and api.sms.bodies()[-1].startswith("[VI] ")


def test_a_dropped_stop_sends_the_whole_english_text_and_stamps_why():
    tr = FakeTranslator(out=lambda text, target: "Tin nhắn đã dịch, không có từ khóa.")
    client, api = _vietnamese_patient(tr)
    t.invite(client, api)
    sent = api.sms.bodies()[-1]
    assert "STOP" in sent and not sent.startswith("Tin")  # the English, whole
    msgs = t.run(sms.list_messages(api, t.PHONE))
    w = msgs[-1]["wording"]
    assert w["source"] == "english_fallback" and "STOP" in w["error"]
    s = t.session(api)
    assert "STOP" in s["last_translation_error"] and s["english_fallbacks"] == 1


def test_a_failed_translation_sends_the_english_text():
    client, api = _vietnamese_patient(FakeTranslator(fail_out=True))
    t.invite(client, api)
    msgs = t.run(sms.list_messages(api, t.PHONE))
    assert msgs[-1]["wording"]["source"] == "english_fallback"
    assert msgs[-1]["body"] == msgs[-1]["wording"]["english"]
    assert t.session(api)["last_translation_error"].startswith("translation failed")


def test_the_emergency_text_goes_out_in_english_with_911_when_translation_fails():
    tr = FakeTranslator(
        {"tôi bị đau ngực và không thở được": ("vi", "I have chest pain and can't breathe")},
        out=vi,
    )
    client, api = _vietnamese_patient(tr)
    t.invite(client, api)
    t.text(client, "3")
    t.text(client, "15/03/1950")
    tr.fail_out = True  # the translator goes down mid-conversation
    r = t.text(client, "tôi bị đau ngực và không thở được")
    assert r.json()["effect"] == "file_emergency"
    sent = api.sms.bodies()[-1]
    assert sent == ENGLISH["emergency"]
    assert "911" in sent and "tel:911" in sent
    assert t.run(api.store.get("calls", t.CALL))["follow_up"] == "emergency"


def test_the_emergency_text_is_translated_when_the_translation_keeps_911():
    tr = FakeTranslator(
        {"tôi bị đau ngực và không thở được": ("vi", "I have chest pain and can't breathe")},
        out=vi,
    )
    client, api = _vietnamese_patient(tr)
    t.invite(client, api)
    t.text(client, "3")
    t.text(client, "15/03/1950")
    t.text(client, "tôi bị đau ngực và không thở được")
    assert api.sms.bodies()[-1] == "[VI] " + ENGLISH["emergency"]


def test_the_spanish_draft_wording_is_sent_as_is_not_translated():
    tr = FakeTranslator(out=vi)
    client, api, _ = t.make()
    _switch_on()
    api.translator = tr
    t._language_call(api, language="es-US", call_in_english=False)
    t.consent(client)
    t.invite(client, api)
    s = t.session(api)
    assert s["sms_language"] == "es-US" and s["language_source"] == "draft_pack"
    assert api.sms.bodies()[-1].startswith("Acme Health tiene un mensaje de seguimiento")
    assert tr.out_calls == []
    assert "wording" not in t.run(sms.list_messages(api, t.PHONE))[-1]


def test_off_a_patient_without_a_pack_gets_no_text():
    client, api, _ = t.make()
    api.translator = FakeTranslator(out=vi)
    t._language_call(api, language="vi-VN", call_in_english=False)
    t.consent(client)
    t.invite(client, api)
    assert api.sms.bodies() == []
    fu = t.run(api.store.get("calls", t.CALL))["sms_follow_up"]
    assert fu["status"] == "not_sent" and "Vietnamese" in fu["reason"]


# --------------------------------------------------------------------------- simulator


def test_the_simulator_runs_a_machine_language_when_auto_approved():
    import test_sms_simulator as simt

    client, api = simt.make()
    assert simt.start(client, language="vi-VN").status_code == 409  # off: no wording
    _switch_on()
    api.translator = FakeTranslator(out=vi)
    r = simt.start(client, language="vi-VN")
    assert r.status_code == 200, r.text
    assert r.json()["wording"] == {
        "language": "vi-VN",
        "name": "Vietnamese",
        "status": "machine",
        "approved": False,
        "label": "Machine-translated, unreviewed (demo)",
    }
