"""The EHR feed: idempotent call batches with per-item results."""

import json
from datetime import timedelta

from test_nurse_console_api import NOW, PHONE, PHONE2, make, run
from voa_api import routes_ingest

URL = "/api/v1/call-batches/epic-test/2026-03-03"
LATER = (NOW + timedelta(hours=2)).isoformat()  # 12:00 New York, inside hours


def call(ext, phone=PHONE, mrn=None, **kw):
    return {
        "external_id": ext,
        "mrn": mrn or f"MRN-{ext}",
        "name": f"Test Patient {ext}",
        "dob": "1950-03-15",
        "phone": phone,
        "procedure": "Knee replacement",
        **kw,
    }


def batch(*calls, when=LATER, label="Morning"):
    return {"tranches": [{"label": label, "scheduled_for": when, "calls": list(calls)}]}


def statuses(r):
    return {i["external_id"]: i["status"] for i in r.json()["items"]}


def without_ids(r) -> str:
    """The response with its random ids removed. An entry id is a uuid4 hex and,
    about once in 1,300 runs, contains "1950": a PHI check on the raw text then
    fails with no PHI in it (the flaky failure of 22 Oct 2026)."""
    body = r.json()
    body.pop("runs", None)
    for i in body.get("items", []):
        i.pop("entry_id", None)
    return json.dumps(body)


def test_a_batch_creates_patients_and_a_run_without_echoing_patient_data():
    client, api, _ = make()
    r = client.put(URL, json=batch(call("e1"), call("e2", phone=PHONE2)))
    assert r.status_code == 200, r.text
    assert statuses(r) == {"e1": "created", "e2": "created"}
    assert "Test Patient" not in r.text and "1950" not in without_ids(r)  # no PHI back
    (rid,) = r.json()["runs"]
    run_doc = run(api.store.get("call_runs", rid))
    assert len(run_doc["entry_ids"]) == 2 and run_doc["source"] == "epic-test"
    entry = run(api.store.get("call_list", run_doc["entry_ids"][0]))
    assert entry["source"] == "epic-test" and entry["external_id"] in ("e1", "e2")


def test_the_phi_check_ignores_an_entry_id_that_happens_to_contain_the_birth_year(monkeypatch):
    # Regression for the 22 Oct flake: pin the "random" id to one containing 1950.
    monkeypatch.setattr(routes_ingest, "new_id", lambda: "0" * 12 + "41950" + "0" * 15)
    client, api, _ = make()
    r = client.put(URL, json=batch(call("e1")))
    assert statuses(r) == {"e1": "created"} and "1950" in r.text  # the id, not PHI
    assert "Test Patient" not in r.text and "1950" not in without_ids(r)


def test_the_same_batch_again_changes_nothing():
    client, api, _ = make()
    body = batch(call("e1"))
    client.put(URL, json=body)
    before = len(run(api.store.query("call_list")))
    again = client.put(URL, json=body).json()
    assert again["replay"] is True
    assert len(run(api.store.query("call_list"))) == before


def test_a_resend_updates_changes_and_cancels_what_is_missing():
    client, api, _ = make()
    client.put(URL, json=batch(call("e1"), call("e2", phone=PHONE2)))
    r = client.put(URL, json=batch(call("e1", procedure="Hip replacement")))
    assert statuses(r) == {"e1": "updated", "e2": "cancelled"}
    e2 = next(i for i in r.json()["items"] if i["external_id"] == "e2")
    assert run(api.store.get("call_list", e2["entry_id"]))["list_status"] == "canceled"


def test_bad_items_are_rejected_one_by_one():
    client, api, _ = make()
    r = client.put(
        URL,
        json=batch(
            call("ok"),
            call("badphone", phone="12345"),
            call("notallowed", phone="+12025550199"),
            call("baddob", dob="1950-13-45"),
        ),
    )
    s = statuses(r)
    assert s["ok"] == "created"
    assert s["badphone"] == s["notallowed"] == s["baddob"] == "rejected"
    reasons = {i["external_id"]: i["reason"] for i in r.json()["items"]}
    assert "phone" in reasons["badphone"]


def test_a_tranche_in_the_past_or_out_of_hours_is_rejected():
    client, api, _ = make()
    past = (NOW - timedelta(hours=1)).isoformat()
    night = (NOW + timedelta(hours=12)).isoformat()  # 22:00 New York
    r1 = client.put(URL, json=batch(call("p"), when=past))
    r2 = client.put("/api/v1/call-batches/epic-test/night", json=batch(call("n"), when=night))
    assert statuses(r1) == {"p": "rejected"} and statuses(r2) == {"n": "rejected"}
    assert "calling hours" in r2.json()["items"][0]["reason"]


def test_get_reports_status_and_delete_withdraws():
    client, api, _ = make()
    client.put(URL, json=batch(call("e1")))
    g = client.get(URL).json()
    assert g["items"][0]["list_status"] == "pending"
    d = client.delete(URL).json()
    assert d["status"] == "withdrawn"
    assert client.get(URL).json()["items"][0]["list_status"] == "canceled"
    assert client.get("/api/v1/call-batches").json()["batches"][0]["status"] == "withdrawn"


def test_duplicate_external_ids_and_bad_paths_are_refused():
    client, api, _ = make()
    assert client.put(URL, json=batch(call("e1"), call("e1"))).status_code == 400
    assert client.put("/api/v1/call-batches/Bad Source/x", json=batch(call("e1"))).status_code in (
        400,
        404,
    )


def test_the_feed_is_audited():
    client, api, _ = make()
    client.put(URL, json=batch(call("e1")), headers={"X-Acting-User": "ehr-sync"})
    actions = [e["action"] for e in client.get("/api/audit").json()["entries"]]
    assert "PUT /api/v1/call-batches/{source}/{batch_id}" in actions


def test_care_setting_is_fed_and_a_change_is_an_update():
    client, api, _ = make()
    r = client.put(URL, json=batch(call("e1", care_setting="outpatient")))
    (item,) = r.json()["items"]
    assert run(api.store.get("call_list", item["entry_id"]))["care_setting"] == "outpatient"
    r = client.put(URL, json=batch(call("e1", care_setting="inpatient")))
    assert statuses(r) == {"e1": "updated"}
    assert run(api.store.get("call_list", item["entry_id"]))["care_setting"] == "inpatient"
    r = client.put(URL, json=batch(call("e2", care_setting="home")))
    assert r.status_code == 422


# --------------------------------------------------------------------------- languages


def test_an_unsupported_language_rejects_that_item_only():
    client, api, _ = make()
    r = client.put(
        URL, json=batch(call("es", language="Spanish"), call("zh", phone=PHONE2, language="zh"))
    )
    assert r.status_code == 200, r.text
    assert statuses(r) == {"es": "created", "zh": "rejected"}
    reason = next(i["reason"] for i in r.json()["items"] if i["external_id"] == "zh")
    assert reason.startswith("language:") and "not supported" in reason
    eid = next(i["entry_id"] for i in r.json()["items"] if i["external_id"] == "es")
    e = run(api.store.get("call_list", eid))
    assert e["language"] == "es-US" and e["call_in_english"] is False


def test_a_language_change_is_an_update_and_keeps_the_admin_override():
    client, api, _ = make()
    r = client.put(URL, json=batch(call("e1", language="es")))
    eid = r.json()["items"][0]["entry_id"]
    run(api.store.merge("call_list", eid, {"call_in_english": True}))  # an admin's override
    r = client.put(URL, json=batch(call("e1", language="vi-VN")))
    assert statuses(r) == {"e1": "updated"}
    e = run(api.store.get("call_list", eid))
    assert (e["language"], e["call_in_english"]) == ("vi-VN", True)
    # The same language spelled differently is not a change.
    assert statuses(client.put(URL, json=batch(call("e1", language="Vietnamese")))) == {
        "e1": "unchanged"
    }


def test_the_fingerprint_is_unchanged_for_batches_without_language():
    """A batch sent before languages were validated must still replay as the same."""
    import hashlib
    import json

    from voa_api.routes_ingest import IngestBatch, _fingerprint

    body = batch(call("e1"))
    data = IngestBatch.model_validate(body).model_dump()
    for t in data["tranches"]:
        t.pop("campaign", None)
        for c in t["calls"]:
            c.pop("campaign", None)
    # The pre-language shape: every call carried "language": None.
    assert all(c["language"] is None for t in data["tranches"] for c in t["calls"])
    expected = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    assert _fingerprint(IngestBatch.model_validate(body)) == expected


def test_an_entry_stored_with_no_language_is_not_reported_updated():
    client, api, _ = make()
    r = client.put(URL, json=batch(call("e1")))
    eid = r.json()["items"][0]["entry_id"]
    run(api.store.merge("call_list", eid, {"language": None}))  # stored before the upgrade
    body = batch(call("e1"), label="Afternoon")  # a different batch, same patient
    assert statuses(client.put(URL, json=body)) == {"e1": "unchanged"}
