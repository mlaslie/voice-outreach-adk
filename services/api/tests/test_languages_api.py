"""Patient languages, step 1 (docs/LANGUAGES-PLAN.md): the field end to end and
the safe hold. Calls are still English only; a patient whose language has no
approved pack is held, never called in English silently. Fictional data only."""

import json
from datetime import timedelta

import pytest
from test_nurse_console_api import ENTRY, NOW, PHONE2, calls, drain, make, run
from voa_core import languages

SPANISH = {**ENTRY, "patient_name": "Maria Lopez", "patient_id": "P-ES1", "language": "es"}
ENGLISH = {**ENTRY, "phone": PHONE2, "patient_id": "P-EN1"}


def add(client, body):
    r = client.post("/api/call-list", json=body)
    assert r.status_code == 201, r.text
    return r.json()["entry_id"]


def entry(api, entry_id):
    return run(api.store.get("call_list", entry_id))


def current_run(api):
    return run(api.store.get("list_runs", "current"))


# --------------------------------------------------------------------------- models


def test_entries_store_the_normalised_language_and_default_to_english():
    client, api, _ = make()
    es = add(client, {**SPANISH, "language": "Spanish"})
    en = add(client, ENGLISH)
    assert entry(api, es)["language"] == "es-US" and entry(api, es)["call_in_english"] is False
    assert entry(api, en)["language"] == "en-US"


def test_an_unsupported_language_is_a_422_listing_the_supported_ones():
    client, _, _ = make()
    r = client.post("/api/call-list", json={**ENTRY, "language": "zh"})
    assert r.status_code == 422
    msg = json.dumps(r.json())
    assert "not supported" in msg and "es-US" in msg and "vi-VN" in msg
    assert client.post("/api/place-call", json={**ENTRY, "language": "tl"}).status_code == 422


def test_patch_changes_language_and_override_but_an_edit_without_them_keeps_them():
    client, api, _ = make()
    eid = add(client, SPANISH)
    r = client.patch(f"/api/call-list/{eid}", json={**SPANISH, "call_in_english": True})
    assert r.status_code == 200 and entry(api, eid)["call_in_english"] is True
    # An older client that does not send the fields never resets them.
    old = {k: v for k, v in SPANISH.items() if k != "language"}
    assert (
        client.patch(f"/api/call-list/{eid}", json={**old, "operation_name": "Hip"}).status_code
        == 200
    )
    e = entry(api, eid)
    assert (e["language"], e["call_in_english"], e["operation_name"]) == ("es-US", True, "Hip")
    client.patch(f"/api/call-list/{eid}", json={**SPANISH, "language": "vi"})
    assert entry(api, eid)["language"] == "vi-VN"


# --------------------------------------------------------------------------- the hold


def test_a_run_skips_held_patients_and_dials_english_ones():
    client, api, _ = make()
    es, en = add(client, SPANISH), add(client, ENGLISH)
    assert client.post("/api/call-list/run", json={}).status_code == 200
    res = [r.json() for r in drain(client, api)]
    assert {"skipped": "held for language"} in res
    assert len(api.voice.placed) == 1 and api.voice.placed[0][1].to == PHONE2
    rd = current_run(api)
    assert rd["held"] == [es] and rd["dispatched"] == 1
    held = entry(api, es)
    # Not terminal, not attempted: re-evaluated on the next run.
    assert held["list_status"] == "pending" and held["attempts"] == 0
    assert entry(api, en)["list_status"] == "calling"


def test_a_named_row_is_held_too_and_the_override_dials_it():
    client, api, _ = make()
    es = add(client, SPANISH)
    client.post("/api/call-list/run", json={"entry_ids": [es]})
    drain(client, api)
    assert api.voice.placed == [] and current_run(api)["held"] == [es]
    # An admin turns on "Call in English": the next run dials it.
    client.patch(f"/api/call-list/{es}", json={**SPANISH, "call_in_english": True})
    client.post("/api/call-list/run", json={"entry_ids": [es]})
    drain(client, api)
    assert len(api.voice.placed) == 1
    call = next(iter(calls(api).values()))
    assert call["language"] == "es-US" and call["call_in_english"] is True
    ctx = call["patient_context"]
    assert ctx["language"] == "es-US" and ctx["call_in_english"] is True


def test_a_held_patient_becomes_callable_once_a_pack_is_approved(tmp_path, monkeypatch):
    client, api, _ = make()
    es = add(client, SPANISH)
    client.post("/api/call-list/run", json={})
    drain(client, api)
    assert api.voice.placed == []
    d = tmp_path / "i18n"
    d.mkdir()
    (d / "post_op.es-US.yaml").write_text("status: approved\n")
    monkeypatch.setattr(languages, "pack_dir", lambda config_dir=None: d)
    client.post("/api/call-list/run", json={})
    drain(client, api)
    assert len(api.voice.placed) == 1 and entry(api, es)["attempts"] == 1


def test_the_due_pass_does_not_start_a_run_for_a_held_patient_every_minute():
    client, api, clock = make()
    when = (NOW - timedelta(minutes=5)).isoformat()
    add(client, {**SPANISH, "scheduled_for": when})
    r = client.post("/cron/dispatch-due").json()
    assert r == {"started": False, "reason": "nothing due"}
    en = add(client, {**ENGLISH, "scheduled_for": when})
    assert client.post("/cron/dispatch-due").json()["dialled"] == 1
    drain(client, api)
    assert entry(api, en)["attempts"] == 1


def test_a_held_member_of_a_scheduled_run_is_recorded_as_held_not_blocked():
    client, api, clock = make()
    es, en = add(client, SPANISH), add(client, ENGLISH)
    when = (NOW + timedelta(hours=1)).isoformat()
    assert (
        client.post(
            "/api/call-runs", json={"scheduled_for": when, "entry_ids": [es, en]}
        ).status_code
        == 201
    )
    clock.at = NOW + timedelta(hours=1, minutes=1)
    client.post("/cron/dispatch-due")
    drain(client, api)
    assert len(api.voice.placed) == 1
    assert current_run(api)["held"] == [es]
    assert entry(api, es)["list_status"] == "pending"


def test_place_call_with_a_held_language_is_a_409_and_the_override_calls():
    client, api, _ = make()
    r = client.post("/api/place-call", json=SPANISH)
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "Maria Lopez's language is Spanish (US). Calls in Spanish (US) aren't available yet. "
        "Turn on 'Call in English' to call in English."
    )
    assert api.voice.placed == []
    r = client.post("/api/place-call", json={**SPANISH, "call_in_english": True})
    assert r.status_code == 200
    call = calls(api)[r.json()["call_sid"]]
    assert (call["language"], call["call_in_english"]) == ("es-US", True)
    pending = run(api.store.get("pending_calls", r.json()["call_sid"]))
    assert (pending["language"], pending["call_in_english"]) == ("es-US", True)


def test_an_english_call_records_en_us():
    client, api, _ = make()
    r = client.post("/api/place-call", json=ENTRY)
    call = calls(api)[r.json()["call_sid"]]
    assert call["language"] == "en-US" and call["call_in_english"] is False
    assert call["patient_context"]["language"] == "en-US"


def test_place_call_in_english_india_is_not_held():
    client, _, _ = make()
    assert client.post("/api/place-call", json={**ENTRY, "language": "en-IN"}).status_code == 200


# --------------------------------------------------------------------------- GET /api/languages


def test_languages_endpoint():
    client, _, _ = make()
    body = client.get("/api/languages").json()
    assert body["campaign_id"] == "post_op" and body["default"] == "en-US"
    assert len(body["languages"]) == 24
    by = {x["code"]: x for x in body["languages"]}
    assert by["es-US"] == {
        "code": "es-US",
        "name": "Spanish (US)",
        "native": "Español",
        "available": False,
    }
    assert by["en-US"]["available"] and by["en-IN"]["available"]
    assert body["available_by_campaign"]["pre_op"] == ["en-IN", "en-US"]
    assert client.get("/api/languages?campaign_id=pre_op").json()["campaign_id"] == "pre_op"
    assert client.get("/api/languages?campaign_id=nope").status_code == 422


def test_ui_sample_languages_match_the_endpoint():
    """The UI's sample-data copy (?data=demo has no backend) must not drift."""
    from pathlib import Path

    client, _, _ = make()
    sample = Path(__file__).parents[3] / "ui/src/lib/languages-sample.json"
    assert json.loads(sample.read_text()) == client.get("/api/languages").json()


@pytest.mark.parametrize("path", ["/api/languages"])
def test_languages_route_is_a_read_for_every_console(path):
    from voa_api import auth

    assert auth.ROUTE_POLICY[f"GET {path}"] == auth.READ
