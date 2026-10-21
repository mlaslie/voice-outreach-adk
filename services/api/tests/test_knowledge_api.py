"""Knowledge agent API (v2): examples, guidelines, settings, "Try it", shadow drafts,
the nurse rating, and care setting on the call list. Fake models only."""

from datetime import timedelta

import pytest
from test_api import ENTRY, NOW, make, run
from voa_api import audit
from voa_core import knowledge as kb
from voa_core import patient_questions as pq
from voa_knowledge.testing import FixedClassifier, FixedGrounder, offline_agent

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
    api.knowledge_agent = offline_agent()  # no model in tests
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
        {**SHOWER, "question": "Can I take ibuprofen?"},
        {**SHOWER, "alt_questions": ["Is my wound okay to wet?"]},
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


def test_an_examples_category_no_longer_has_to_match_its_wording(setup):
    client, _ = setup
    assert create(client, {**SHOWER, "category": "diet"})["category"] == "diet"
    park = {**SHOWER, "question": "Where do I park?", "category": "appointment"}
    assert create(client, park)["category_label"] == "Appointment"


def test_settings_defaults_and_partial_update(setup):
    client, _ = setup
    s = client.get(f"{BASE}/settings").json()
    assert s == {
        "mode": "off",
        "channels": {"voice": True, "sms": False},
        "allowed_categories": [
            "appointment",
            "activity",
            "diet",
            "paperwork_billing",
            "supplies",
            "recovery",
        ],
        "min_support": 0.85,
        "ask_satisfied": True,
        "updated_at": None,
        "updated_by": None,
        "locked_categories": ["medication", "symptom"],
        "available_categories": [
            {"key": "appointment", "label": "Appointment"},
            {"key": "activity", "label": "Activity"},
            {"key": "diet", "label": "Diet"},
            {"key": "paperwork_billing", "label": "Paperwork or billing"},
            {"key": "supplies", "label": "Supplies"},
            {"key": "recovery", "label": "Recovery timeline"},
        ],
    }
    s = client.put(
        f"{BASE}/settings", json={"mode": "shadow", "channels": {"sms": True}}, headers=ADMIN
    ).json()
    assert s["mode"] == "shadow" and s["channels"] == {"voice": True, "sms": True}
    assert s["updated_by"] == "dr.lee@example.org"
    s = client.put(f"{BASE}/settings", json={"allowed_categories": ["diet", "activity"]}).json()
    assert s["allowed_categories"] == ["activity", "diet"] and s["mode"] == "shadow"
    s = client.put(f"{BASE}/settings", json={"min_support": 0.9, "mode": "live"}).json()
    assert s["min_support"] == 0.9 and s["mode"] == "live"


def test_a_v1_settings_document_migrates(setup):
    client, api = setup
    run(api.store.set("settings", "knowledge", {"enabled": True, "min_match": 0.9}))
    s = client.get(f"{BASE}/settings").json()
    assert s["mode"] == "live" and "enabled" not in s and "min_match" not in s
    client.put(f"{BASE}/settings", json={"ask_satisfied": False})
    stored = run(api.store.get("settings", "knowledge"))
    assert "enabled" not in stored and "min_match" not in stored and stored["mode"] == "live"
    run(api.store.set("settings", "knowledge", {"enabled": False}))
    assert client.get(f"{BASE}/settings").json()["mode"] == "off"


@pytest.mark.parametrize(
    "body",
    [
        {"allowed_categories": ["medication"]},
        {"allowed_categories": ["activity", "symptom"]},
        {"allowed_categories": ["other"]},
        {"allowed_categories": ["nonsense"]},
        {"min_support": 0.4},
        {"min_support": 1.1},
        {"mode": "on"},
        {"mode": None},
        {"enabled": True},  # v1 fields are refused
        {"min_match": 0.8},
        {"channels": {"fax": True}},
        {"channels": {"voice": None}},
        {"surprise": True},
    ],
)
def test_bad_settings_are_refused(setup, body):
    client, _ = setup
    assert client.put(f"{BASE}/settings", json=body).status_code == 422


# --------------------------------------------------------------------------- guidelines

GUIDE = {
    "title": "Lifting after outpatient knee surgery",
    "body": "Lift nothing heavier than ten pounds for two weeks.",
    "procedures": ["knee arthroscopy"],
    "care_settings": ["outpatient"],
}


def create_doc(client, body=GUIDE):
    r = client.post(f"{BASE}/documents", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def test_guideline_shape_and_lifecycle(setup):
    client, _ = setup
    d = create_doc(client)
    assert d == {
        "doc_id": d["doc_id"],
        "title": GUIDE["title"],
        "body": GUIDE["body"],
        "procedures": ["knee arthroscopy"],
        "care_settings": ["outpatient"],
        "status": "draft",
        "version": 1,
        "chars": len(GUIDE["body"]),
        "created_at": d["created_at"],
        "created_by": "dr.lee@example.org",
        "updated_at": d["updated_at"],
        "updated_by": "dr.lee@example.org",
        "approved_at": None,
        "approved_by": None,
    }
    did = d["doc_id"]
    ok = client.post(f"{BASE}/documents/{did}/approve", headers=ADMIN).json()
    assert ok["status"] == "approved" and ok["approved_by"] == "dr.lee@example.org"
    same = client.patch(f"{BASE}/documents/{did}", json={"title": GUIDE["title"]}).json()
    assert same["version"] == 1 and same["status"] == "approved"
    e = client.patch(f"{BASE}/documents/{did}", json={"care_settings": []}).json()
    assert e["version"] == 2 and e["status"] == "draft" and e["care_settings"] == []
    client.post(f"{BASE}/documents/{did}/approve")
    r = client.delete(f"{BASE}/documents/{did}")
    assert r.status_code == 409 and r.json()["detail"] == "Retire it first."
    assert client.post(f"{BASE}/documents/{did}/retire").json()["status"] == "retired"
    assert client.get(f"{BASE}/documents").json()["documents"][0]["status"] == "retired"
    assert client.delete(f"{BASE}/documents/{did}").json() == {"deleted": True}
    assert client.get(f"{BASE}/documents").json() == {"documents": []}
    for path in (f"{BASE}/documents/{did}/approve", f"{BASE}/documents/{did}/retire"):
        assert client.post(path).status_code == 404
    assert client.delete(f"{BASE}/documents/{did}").status_code == 404
    assert client.patch(f"{BASE}/documents/{did}", json={"title": "x"}).status_code == 404


def test_guidelines_list_newest_first(setup):
    client, api = setup
    first = create_doc(client)
    api.clock = lambda: NOW + timedelta(days=1)
    second = create_doc(client, {"title": "Driving", "body": "Wait six weeks."})
    ids = [d["doc_id"] for d in client.get(f"{BASE}/documents").json()["documents"]]
    assert ids == [second["doc_id"], first["doc_id"]]
    assert second["procedures"] == [] and second["care_settings"] == []


@pytest.mark.parametrize(
    "body",
    [
        {**GUIDE, "title": "x" * 121},
        {**GUIDE, "title": " "},
        {**GUIDE, "body": "x" * 20_001},
        {**GUIDE, "procedures": ["p"] * 21},
        {**GUIDE, "care_settings": ["home"]},
        {**GUIDE, "body": "Call 202-555-0143 for the patient."},
        {**GUIDE, "title": "For MRN 1234567"},
        {**GUIDE, "procedures": ["jordan.rivera@example.org"]},
        {**GUIDE, "unknown": 1},
        {"title": "No body"},
    ],
)
def test_bad_guidelines_are_refused(setup, body):
    client, _ = setup
    assert client.post(f"{BASE}/documents", json=body).status_code == 422


def test_guideline_patch_rules(setup):
    client, _ = setup
    did = create_doc(client)["doc_id"]
    assert client.patch(f"{BASE}/documents/{did}", json={"title": None}).status_code == 422
    r = client.patch(f"{BASE}/documents/{did}", json={"body": "Born 03/15/1950."})
    assert r.status_code == 422 and "patient details" in r.json()["detail"]


def test_guideline_limits(setup, monkeypatch):
    client, _ = setup
    monkeypatch.setattr(kb, "MAX_DOCUMENTS", 3)
    a = create_doc(client, {**GUIDE, "body": "a" * 30})
    b = create_doc(client, {**GUIDE, "body": "b" * 30})
    create_doc(client)
    assert client.post(f"{BASE}/documents", json=GUIDE).status_code == 409
    monkeypatch.setattr(kb, "MAX_APPROVED_CHARS", 50)
    assert client.post(f"{BASE}/documents/{a['doc_id']}/approve").status_code == 200
    r = client.post(f"{BASE}/documents/{b['doc_id']}/approve")
    assert r.status_code == 409 and "50 characters" in r.json()["detail"]
    client.post(f"{BASE}/documents/{a['doc_id']}/retire")
    assert client.post(f"{BASE}/documents/{b['doc_id']}/approve").status_code == 200
    # approving the approved one again is not counted twice
    assert client.post(f"{BASE}/documents/{b['doc_id']}/approve").status_code == 200


# --------------------------------------------------------------------------- try it

TRY_KEYS = {
    "outcome",
    "reason",
    "classification",
    "answer",
    "sources",
    "support_score",
    "latency_ms",
    "note",
}


def test_try_runs_the_real_agent_in_any_mode_with_the_note(setup):
    client, _ = setup
    iid = create(client)["item_id"]
    r = client.post(f"{BASE}/try", json={"question": "When can I shower?"}).json()
    assert set(r) == TRY_KEYS
    assert r["outcome"] == "deferred" and r["reason"] == "no_knowledge"  # still a draft
    assert r["note"] == "test only: mode is off"

    client.post(f"{BASE}/items/{iid}/approve")
    r = client.post(f"{BASE}/try", json={"question": "Is it okay to shower yet?"}).json()
    assert r == {
        "outcome": "answered",
        "reason": "answered",
        "classification": {
            "category": "activity",
            "category_label": "Activity",
            "llm_category": "activity",
            "keyword_category": "activity",
            "red_flag": False,
            "vetoed_by": None,
        },
        "answer": SHOWER["answer"],
        "sources": [{"kind": "example", "id": iid, "title": SHOWER["question"], "version": 1}],
        "support_score": 1.0,
        "latency_ms": r["latency_ms"],
        "note": "test only: mode is off",
    }
    assert isinstance(r["latency_ms"], int)
    r = client.post(f"{BASE}/try", json={"question": "How much Tylenol can I take?"}).json()
    assert (r["outcome"], r["reason"], r["classification"]["vetoed_by"]) == (
        "deferred",
        "locked_category",
        "keywords",
    )
    assert r["classification"]["llm_category"] is None and r["answer"] is None

    client.put(f"{BASE}/settings", json={"mode": "shadow"})
    assert client.post(f"{BASE}/try", json={"question": "When can I shower?"}).json()["note"] == (
        "test only: mode is shadow"
    )
    client.put(f"{BASE}/settings", json={"mode": "live"})
    r = client.post(f"{BASE}/try", json={"question": "When can I shower?"}).json()
    assert r["outcome"] == "answered" and r["note"] is None
    client.post(f"{BASE}/items/{iid}/retire")
    r = client.post(f"{BASE}/try", json={"question": "When can I shower?"}).json()
    assert r["outcome"] == "deferred"


def test_try_uses_the_context_for_guidelines(setup):
    client, _ = setup
    did = create_doc(client)["doc_id"]
    client.post(f"{BASE}/documents/{did}/approve")
    q = {"question": "Lifting after outpatient knee surgery?"}
    knee = {"procedure": "Knee arthroscopy", "care_setting": "outpatient", "days_since_surgery": 3}
    r = client.post(f"{BASE}/try", json={**q, "context": knee}).json()
    assert r["outcome"] == "answered" and r["sources"][0]["kind"] == "guideline"
    r = client.post(f"{BASE}/try", json={**q, "context": {**knee, "care_setting": "inpatient"}})
    assert r.json()["reason"] == "no_knowledge"
    r = client.post(f"{BASE}/try", json=q).json()  # no context: the guideline doesn't apply
    assert r["reason"] == "no_knowledge"
    for bad in (
        {"care_setting": "home"},
        {"days_since_surgery": -1},
        {"procedure": "x" * 121},
        {"name": "Jordan"},
    ):
        assert client.post(f"{BASE}/try", json={**q, "context": bad}).status_code == 422


def test_writes_are_audited_and_try_is_not(setup):
    client, _ = setup
    iid = create(client)["item_id"]
    client.post(f"{BASE}/items/{iid}/approve", headers=ADMIN)
    client.put(f"{BASE}/settings", json={"mode": "live"}, headers=ADMIN)
    client.post(f"{BASE}/documents", json=GUIDE, headers=ADMIN)
    client.post(f"{BASE}/try", json={"question": "When can I shower?"}, headers=ADMIN)
    entries = client.get("/api/audit").json()["entries"]
    actions = sorted(e["action"] for e in entries)
    assert actions == [
        "POST /api/admin/knowledge/documents",
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
            {"mode": "live", "allowed_categories": ["medication", "symptom", "activity"]},
        )
    )
    s = client.get(f"{BASE}/settings").json()
    assert s["allowed_categories"] == ["activity"]


# --------------------------------------------------------------------------- shadow mode

CALL = "CA" + "a" * 32


def _call(api, *questions, follow_up="routine_next_business_day"):
    doc = {
        "call_sid": CALL,
        "patient_name": "Jordan Rivera",
        "follow_up": follow_up,
        "follow_up_reason": "question asked (activity)",
        "patient_context": {
            "first_name": "Jordan",
            "operation_name": "Knee arthroscopy",
            "operation_date": "2026-03-01",
            "care_setting": "outpatient",
            "is_diabetic": False,
        },
        "survey_responses": {
            "has_questions": "yes",
            "patient_questions": [pq.record(q) for q in questions],
        },
        "updated_at": "2026-03-03T15:00:00.000+00:00",
    }
    run(api.store.set("calls", CALL, doc))
    return doc


def _questions(api):
    return run(api.store.get("calls", CALL))["survey_responses"]["patient_questions"]


def test_the_shadow_task_stores_drafts_and_is_idempotent(setup):
    client, api = setup
    iid = create(client)["item_id"]
    client.post(f"{BASE}/items/{iid}/approve")
    spy = []

    class Spy:
        def __init__(self, agent):
            self.agent = agent

        async def answer(self, question, context=None, **kw):
            spy.append((question, dict(context or {})))
            return await self.agent.answer(question, context, **kw)

    api.knowledge_agent = Spy(offline_agent(grounder=FixedGrounder(0.9)))
    before = _call(api, "When can I shower?", "How much Tylenol can I take?")
    r = client.post("/tasks/knowledge-shadow", json={"call_id": CALL})
    assert r.json() == {"stored": 2}
    q1, q2 = _questions(api)
    assert q1["shadow"] == {
        "outcome": "answered",
        "reason": "answered",
        "answer": SHOWER["answer"],
        "sources": [{"kind": "example", "id": iid, "title": SHOWER["question"], "version": 1}],
        "support_score": 0.9,
        "at": "2026-03-03T15:00:00.000+00:00",
    }
    assert q2["shadow"]["outcome"] == "deferred" and q2["shadow"]["reason"] == "locked_category"
    assert pq.valid_entry(q1) and pq.valid_entry(q2)
    # nothing else changes: the question is still a nurse follow-up
    call = run(api.store.get("calls", CALL))
    assert call["follow_up"] == before["follow_up"] and "outcome" not in q1
    assert {k: v for k, v in q1.items() if k != "shadow"} == before["survey_responses"][
        "patient_questions"
    ][0]
    # the patient context, never identifiers
    assert spy[0][1] == {
        "procedure": "Knee arthroscopy",
        "days_since_surgery": 2,
        "care_setting": "outpatient",
        "is_diabetic": False,
    }
    # a second delivery drafts nothing
    spy.clear()
    assert client.post("/tasks/knowledge-shadow", json={"call_id": CALL}).json() == {
        "skipped": "nothing to draft"
    }
    assert spy == []
    assert client.post("/tasks/knowledge-shadow", json={"call_id": "CA" + "b" * 32}).json() == {
        "skipped": "no call"
    }


def test_a_slow_or_broken_agent_still_stores_a_deferral(setup):
    client, api = setup
    api.knowledge_agent = offline_agent(classifier=FixedClassifier("activity", confidence=0.1))
    _call(api, "When can I shower?")
    client.post("/tasks/knowledge-shadow", json={"call_id": CALL})
    (q,) = _questions(api)
    assert q["shadow"]["outcome"] == "deferred" and q["shadow"]["reason"] == "low_confidence"


def _shadowed(client, api):
    iid = create(client)["item_id"]
    client.post(f"{BASE}/items/{iid}/approve")
    _call(api, "When can I shower?", "Where is the cafeteria?")
    run(api.store.set("calls", CALL, {**run(api.store.get("calls", CALL))}))
    client.post("/tasks/knowledge-shadow", json={"call_id": CALL})
    qs = _questions(api)
    # The second question has no draft in this test.
    qs[1].pop("shadow", None)
    call = run(api.store.get("calls", CALL))
    call["survey_responses"]["patient_questions"] = qs
    run(api.store.set("calls", CALL, call))


NURSE = {"X-Acting-User": "rn.kim@example.org"}


def test_a_nurse_rates_the_draft(setup):
    client, api = setup
    _shadowed(client, api)
    url = f"/api/calls/{CALL}/questions/0/shadow-review"
    r = client.post(url, json={"verdict": "good"}, headers=NURSE)
    assert r.status_code == 200
    q = r.json()
    assert q["shadow_review"] == {
        "verdict": "good",
        "note": None,
        "by": "rn.kim@example.org",
        "at": "2026-03-03T15:00:00.000+00:00",
    }
    assert q["text"] == "When can I shower?" and q["shadow"]["outcome"] == "answered"
    r = client.post(url, json={"verdict": "not_right", "note": "  Say two  days. "}, headers=NURSE)
    assert r.json()["shadow_review"]["note"] == "Say two days."
    assert _questions(api)[0]["shadow_review"]["verdict"] == "not_right"
    assert pq.valid_entry(_questions(api)[0])
    entries = client.get("/api/audit").json()["entries"]
    reviews = [e for e in entries if e["action"].endswith("/shadow-review")]
    assert len(reviews) == 2 and reviews[0]["target"] == {"call_id": CALL, "index": "0"}
    assert "two days" not in str(entries).lower()


@pytest.mark.parametrize(
    ("path_index", "body", "status"),
    [
        ("1", {"verdict": "good"}, 404),  # no shadow on this question
        ("2", {"verdict": "good"}, 404),  # no such question
        ("-1", {"verdict": "good"}, 404),
        ("0", {"verdict": "great"}, 422),
        ("0", {}, 422),
        ("0", {"verdict": "good", "note": "x" * 301}, 422),
        ("0", {"verdict": "good", "note": "Called her on 202-555-0143"}, 422),
        ("0", {"verdict": "good", "note": "MRN 1234567"}, 422),
        ("0", {"verdict": "good", "extra": 1}, 422),
    ],
)
def test_bad_ratings_are_refused(setup, path_index, body, status):
    client, api = setup
    _shadowed(client, api)
    r = client.post(f"/api/calls/{CALL}/questions/{path_index}/shadow-review", json=body)
    assert r.status_code == status
    assert "shadow_review" not in _questions(api)[0]


def test_rating_an_unknown_call_is_404(setup):
    client, _ = setup
    url = f"/api/calls/{'CA' + 'c' * 32}/questions/0/shadow-review"
    assert client.post(url, json={"verdict": "good"}).status_code == 404


def test_a_filed_sms_report_in_shadow_mode_queues_the_task(setup):
    """The SMS path calls the same helper as the gateway (voa_core.knowledge)."""
    client, api = setup
    client.put(f"{BASE}/settings", json={"mode": "shadow", "channels": {"sms": True}})
    kb.forget(api.store)
    survey = {"patient_questions": [pq.record("When can I shower?")]}
    assert run(
        kb.enqueue_shadow(
            api.store, api.tasks, "voa-events", call_id=CALL, survey=survey, channel="sms"
        )
    )
    assert api.tasks.tasks[-1]["path"] == "/tasks/knowledge-shadow"


# --------------------------------------------------------------------------- care setting


def test_care_setting_on_the_call_list(setup):
    client, api = setup
    r = client.post("/api/call-list", json={**ENTRY, "care_setting": "Outpatient"})
    assert r.status_code == 201 and r.json()["care_setting"] == "outpatient"
    eid = r.json()["entry_id"]
    assert client.post("/api/call-list", json=ENTRY).json()["care_setting"] is None
    assert (
        client.post("/api/call-list", json={**ENTRY, "care_setting": ""}).json()["care_setting"]
        is None
    )
    assert client.post("/api/call-list", json={**ENTRY, "care_setting": "home"}).status_code == 422
    r = client.patch(f"/api/call-list/{eid}", json={**ENTRY, "care_setting": "inpatient"})
    assert r.json()["care_setting"] == "inpatient"
    bulk = client.post(
        "/api/call-list/bulk",
        json=[{**ENTRY, "patient_id": "P-009", "care_setting": "inpatient"}],
    ).json()
    assert bulk["entries"][0]["care_setting"] == "inpatient"


def test_care_setting_reaches_the_calls_patient_context(setup):
    client, api = setup
    r = client.post("/api/place-call", json={**ENTRY, "care_setting": "outpatient"})
    call_id = r.json()["call_sid"]
    assert run(api.store.get("pending_calls", call_id))["care_setting"] == "outpatient"
    assert run(api.store.get("calls", call_id))["patient_context"]["care_setting"] == "outpatient"
