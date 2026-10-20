"""Admin → Knowledge base: items, approval, settings and "Try it"."""

from datetime import timedelta

import pytest
from test_api import NOW, make, run
from voa_api import audit
from voa_core import knowledge as kb

BASE = "/api/admin/knowledge"
ADMIN = {"X-Acting-User": "dr.lee@example.org"}
SHOWER = {
    "question": "When can I shower?",
    "answer": "You can usually shower two days after surgery.",
    "category": "activity",
    "alt_questions": ["Is it okay to shower yet?"],
}


@pytest.fixture
def setup():
    client, api = make()
    api.knowledge_matcher = kb.keyword_matcher  # no model in tests
    kb.forget(api.store)
    return client, api


def create(client, body=SHOWER):
    r = client.post(f"{BASE}/items", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def test_item_shape_and_lifecycle(setup):
    client, _ = setup
    it = create(client)
    assert set(it) == {
        "item_id",
        "question",
        "alt_questions",
        "answer",
        "category",
        "category_label",
        "status",
        "version",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "approved_at",
        "approved_by",
    }
    assert it["status"] == "draft" and it["version"] == 1 and it["category_label"] == "Activity"
    assert it["created_by"] == "dr.lee@example.org" and it["approved_at"] is None
    iid = it["item_id"]

    ok = client.post(f"{BASE}/items/{iid}/approve").json()
    assert ok["status"] == "approved" and ok["approved_by"] == "admin" and ok["approved_at"]
    ok = client.post(f"{BASE}/items/{iid}/approve", headers=ADMIN).json()  # idempotent
    assert ok["approved_by"] == "admin"

    same = client.patch(f"{BASE}/items/{iid}", json={"answer": SHOWER["answer"]}).json()
    assert same["version"] == 1 and same["status"] == "approved"  # nothing changed

    edited = client.patch(f"{BASE}/items/{iid}", json={"answer": "Three days."}, headers=ADMIN)
    e = edited.json()
    assert e["version"] == 2 and e["status"] == "draft" and e["approved_by"] is None

    client.post(f"{BASE}/items/{iid}/approve", headers=ADMIN)
    r = client.delete(f"{BASE}/items/{iid}")
    assert r.status_code == 409 and r.json()["detail"] == "Retire it first."
    assert client.post(f"{BASE}/items/{iid}/retire").json()["status"] == "retired"
    assert client.delete(f"{BASE}/items/{iid}").json() == {"deleted": True}
    assert client.get(f"{BASE}/items").json() == {"items": []}
    assert client.delete(f"{BASE}/items/{iid}").status_code == 404


def test_list_is_newest_first(setup):
    client, api = setup
    first = create(client)
    api.clock = lambda: NOW + timedelta(days=1)
    second = create(
        client,
        {
            **SHOWER,
            "question": "Where do I park for my follow-up appointment?",
            "category": "appointment",
        },
    )
    ids = [i["item_id"] for i in client.get(f"{BASE}/items").json()["items"]]
    assert ids == [second["item_id"], first["item_id"]]


@pytest.mark.parametrize(
    "body",
    [
        {**SHOWER, "category": "medication"},
        {**SHOWER, "category": "symptom"},
        {**SHOWER, "category": "other"},
        {**SHOWER, "category": "nonsense"},
        {**SHOWER, "answer": "x" * 601},
        {**SHOWER, "question": "x" * 301},
        {**SHOWER, "alt_questions": ["a"] * 11},
        {**SHOWER, "answer": "Call Jordan back on 202-555-0143."},
        {**SHOWER, "answer": "Patient born 03/15/1950."},
        {**SHOWER, "question": "Is jordan.rivera@example.org on file?"},
        {**SHOWER, "answer": "MRN 0012345 is cleared."},
        {**SHOWER, "unknown": 1},
    ],
)
def test_bad_items_are_refused(setup, body):
    client, _ = setup
    assert client.post(f"{BASE}/items", json=body).status_code == 422


def test_phi_in_an_edit_is_refused(setup):
    client, _ = setup
    iid = create(client)["item_id"]
    r = client.patch(f"{BASE}/items/{iid}", json={"alt_questions": ["call 202-555-0143"]})
    assert r.status_code == 422 and "patient details" in r.json()["detail"]
    assert client.patch(f"{BASE}/items/{iid}", json={"category": "medication"}).status_code == 422
    assert client.patch(f"{BASE}/items/{iid}", json={"question": None}).status_code == 422
    assert client.patch(f"{BASE}/items/nope", json={"answer": "x"}).status_code == 404


def test_settings_defaults_and_partial_update(setup):
    client, _ = setup
    s = client.get(f"{BASE}/settings").json()
    assert s["enabled"] is False and s["channels"] == {"voice": True, "sms": True}
    assert s["min_match"] == 0.8 and s["ask_satisfied"] is True
    assert s["locked_categories"] == ["medication", "symptom"]
    assert s["available_categories"] == [
        {"key": "appointment", "label": "Appointment"},
        {"key": "activity", "label": "Activity"},
        {"key": "diet", "label": "Diet"},
        {"key": "paperwork_billing", "label": "Paperwork or billing"},
        {"key": "supplies", "label": "Supplies"},
        {"key": "recovery", "label": "Recovery timeline"},
    ]
    s = client.put(
        f"{BASE}/settings", json={"enabled": True, "channels": {"sms": False}}, headers=ADMIN
    ).json()
    assert s["enabled"] is True and s["channels"] == {"voice": True, "sms": False}
    assert s["updated_by"] == "dr.lee@example.org"
    s = client.put(f"{BASE}/settings", json={"allowed_categories": ["diet", "activity"]}).json()
    assert s["allowed_categories"] == ["activity", "diet"] and s["enabled"] is True


@pytest.mark.parametrize(
    "body",
    [
        {"allowed_categories": ["medication"]},
        {"allowed_categories": ["activity", "symptom"]},
        {"allowed_categories": ["other"]},
        {"allowed_categories": ["nonsense"]},
        {"min_match": 0.4},
        {"min_match": 1.1},
        {"enabled": None},
        {"channels": {"fax": True}},
        {"channels": {"voice": None}},
        {"surprise": True},
    ],
)
def test_bad_settings_are_refused(setup, body):
    client, _ = setup
    assert client.put(f"{BASE}/settings", json=body).status_code == 422


def test_try_runs_the_real_path_and_says_when_grounding_is_off(setup):
    client, _ = setup
    iid = create(client)["item_id"]
    r = client.post(f"{BASE}/try", json={"question": "When can I shower?"}).json()
    assert r["outcome"] == "deferred" and r["reason"] == "no_approved_items"  # still a draft
    assert r["note"] == "test only: grounding is off"

    client.post(f"{BASE}/items/{iid}/approve")
    r = client.post(f"{BASE}/try", json={"question": "Is it okay to shower yet?"}).json()
    assert r == {
        "outcome": "answered",
        "category": "activity",
        "category_label": "Activity",
        "reason": "matched",
        "answer": SHOWER["answer"],
        "item_id": iid,
        "item_version": 1,
        "confidence": 1.0,
        "note": "test only: grounding is off",
    }
    r = client.post(f"{BASE}/try", json={"question": "How much Tylenol can I take?"}).json()
    assert (r["outcome"], r["category"], r["reason"]) == (
        "deferred",
        "medication",
        "locked_category",
    )

    client.put(f"{BASE}/settings", json={"enabled": True})
    r = client.post(f"{BASE}/try", json={"question": "When can I shower?"}).json()
    assert r["outcome"] == "answered" and "note" not in r
    client.post(f"{BASE}/items/{iid}/retire")
    assert (
        client.post(f"{BASE}/try", json={"question": "When can I shower?"}).json()["outcome"]
        == "deferred"
    )


def test_writes_are_audited_and_try_is_not(setup):
    client, _ = setup
    iid = create(client)["item_id"]
    client.post(f"{BASE}/items/{iid}/approve", headers=ADMIN)
    client.put(f"{BASE}/settings", json={"enabled": True}, headers=ADMIN)
    client.post(f"{BASE}/try", json={"question": "When can I shower?"}, headers=ADMIN)
    entries = client.get("/api/audit").json()["entries"]
    actions = sorted(e["action"] for e in entries)
    assert actions == [
        "POST /api/admin/knowledge/items",
        "POST /api/admin/knowledge/items/{item_id}/approve",
        "PUT /api/admin/knowledge/settings",
    ]
    assert all(e["by"] == "dr.lee@example.org" for e in entries)
    approve = next(e for e in entries if e["action"].endswith("/approve"))
    assert approve["target"] == {"item_id": iid}
    assert "shower" not in str(entries).lower()  # bodies are never kept
    assert not audit.audited("POST /api/admin/knowledge/try")


def test_item_limit(setup, monkeypatch):
    client, api = setup
    monkeypatch.setattr(kb, "MAX_ITEMS", 2)
    create(client)
    create(client)
    r = client.post(f"{BASE}/items", json=SHOWER)
    assert r.status_code == 409


def test_settings_written_directly_still_cannot_unlock_medication(setup):
    client, api = setup
    run(
        api.store.set(
            "settings",
            "knowledge",
            {"enabled": True, "allowed_categories": ["medication", "symptom", "activity"]},
        )
    )
    s = client.get(f"{BASE}/settings").json()
    assert s["allowed_categories"] == ["activity"]
