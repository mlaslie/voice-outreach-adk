"""Knowledge v2 core: settings and their migration, item and guideline lifecycle,
PHI, applicability, patient context, the shadow enqueue and the question record."""

from datetime import date

import pytest
from voa_core import knowledge as kb
from voa_core import patient_questions as pq
from voa_core import survey
from voa_core.store import MemoryStore

AT, LATER = "2026-10-20T10:00:00.000+00:00", "2026-10-21T10:00:00.000+00:00"


def item(item_id, question, answer, category, status=kb.APPROVED, version=1, alts=()):
    return {
        "item_id": item_id,
        "question": question,
        "alt_questions": list(alts),
        "answer": answer,
        "category": category,
        "status": status,
        "version": version,
    }


SHOWER = item("i-shower", "When can I shower?", "Two days after surgery.", "activity", version=3)
PARKING = item("i-park", "Where do I park?", "The north garage.", "appointment")
# Written straight into the store, bypassing the API: still never said.
MEDS = item("i-meds", "How much Tylenol can I take?", "Ask a nurse.", "medication")
SYMPTOM = item("i-wound", "Is my wound infected?", "Ask a nurse.", "symptom")
ITEMS = [SHOWER, PARKING, MEDS, SYMPTOM]
GUIDE = {
    "title": "Lifting after outpatient knee arthroscopy",
    "body": "Lift nothing heavier than ten pounds for two weeks.",
    "procedures": ["knee arthroscopy"],
    "care_settings": ["outpatient"],
    "status": "approved",
    "version": 1,
}


# --------------------------------------------------------------------------- settings


def test_settings_defaults_and_locked_categories_are_dropped():
    s = kb.normalize_settings(None)
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
    }
    s = kb.normalize_settings(
        {"allowed_categories": ["medication", "symptom", "other", "diet"], "min_support": 0.1}
    )
    assert s["allowed_categories"] == ["diet"] and s["min_support"] == 0.5
    assert kb.normalize_settings({"min_support": 3})["min_support"] == 1.0
    assert not kb.applies(kb.normalize_settings(None), "voice")


def test_v1_settings_migrate_on_read():
    on = kb.normalize_settings({"enabled": True, "min_match": 0.9, "channels": {"sms": True}})
    assert on["mode"] == "live" and "enabled" not in on and "min_match" not in on
    assert kb.normalize_settings({"enabled": False})["mode"] == "off"
    # an explicit v2 mode wins over a leftover flag
    assert kb.normalize_settings({"enabled": True, "mode": "shadow"})["mode"] == "shadow"
    assert kb.normalize_settings({"mode": "loud"})["mode"] == "off"


def test_mode_per_channel():
    live = {"mode": "live", "channels": {"voice": True, "sms": False}}
    assert kb.applies(live, "voice") and not kb.applies(live, "sms")
    assert kb.mode_for(live, "sms") == "off"
    shadow = {"mode": "shadow"}
    assert kb.shadows(shadow, "voice") and not kb.applies(shadow, "voice")
    assert not kb.shadows(shadow, "sms")  # sms is off by default


async def test_settings_are_cached_and_forgotten():
    store = MemoryStore()
    assert (await kb.load_settings(store))["mode"] == "off"
    await store.set("settings", "knowledge", {"enabled": True})
    assert (await kb.load_settings(store))["mode"] == "off"  # cached
    kb.forget(store)
    assert (await kb.load_settings(store))["mode"] == "live"
    await store.set("knowledge_items", "a", {**SHOWER, "item_id": "a"})
    await store.set("knowledge_items", "b", {**PARKING, "item_id": "b", "status": "draft"})
    await store.set("knowledge_documents", "d", {**GUIDE, "doc_id": "d"})
    await store.set("knowledge_documents", "e", {**GUIDE, "status": "retired"})
    snap = await kb.load_snapshot(store, fresh=True)
    assert [i["item_id"] for i in snap.examples] == ["a"]
    assert [d["doc_id"] for d in snap.guidelines] == ["d"]


# --------------------------------------------------------------------------- lifecycle


def test_item_lifecycle_and_versioning():
    fields = kb.clean_fields(
        {"question": " When can  I shower? ", "answer": "Two days.", "category": "activity"}
    )
    doc = kb.new_item("x", {**fields, "alt_questions": []}, by="a@example.org", at=AT)
    assert (
        doc["status"] == "draft" and doc["version"] == 1 and doc["question"] == "When can I shower?"
    )

    # editing a draft keeps its version
    d2 = kb.edit_item(doc, {"answer": "Three days."}, by="b@example.org", at=LATER)
    assert d2["version"] == 1 and d2["status"] == "draft" and d2["updated_by"] == "b@example.org"

    ok = kb.approve_item(d2, by="dr@example.org", at=LATER)
    assert ok["status"] == "approved" and ok["approved_by"] == "dr@example.org"
    assert kb.approve_item(ok, by="dr@example.org", at=LATER) is None  # already approved

    # editing nothing changes nothing
    assert kb.edit_item(ok, {"answer": "Three days."}, by="c@example.org", at=LATER) is None
    # editing content: new version, back to draft, approval cleared
    d3 = kb.edit_item(ok, {"alt_questions": ["Shower yet?"]}, by="c@example.org", at=LATER)
    assert d3["version"] == 2 and d3["status"] == "draft" and d3["approved_by"] is None

    gone = kb.retire_item(kb.approve_item(d3, by="dr@example.org", at=LATER), by="x", at=LATER)
    assert gone["status"] == "retired" and kb.retire_item(gone, by="x", at=LATER) is None
    pub = kb.public_item(gone)
    assert set(pub) == set(kb.ITEM_KEYS) and pub["category_label"] == "Activity"


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"question": "x" * 301}, "at most 300"),
        ({"answer": "x" * 601}, "at most 600"),
        ({"alt_questions": ["q"] * 11}, "At most 10"),
        ({"alt_questions": ["x" * 301]}, "at most 300"),
        ({"category": "medication"}, "category must be one of"),
        ({"category": "symptom"}, "category must be one of"),
        ({"category": "other"}, "category must be one of"),
        ({"question": "  "}, "required"),
    ],
)
def test_field_limits(fields, message):
    with pytest.raises(ValueError, match=message):
        kb.clean_fields(fields)


@pytest.mark.parametrize(
    "text",
    [
        "Call Jordan at 202-555-0143 about it",
        "Born 03/15/1950",
        "Email jordan.rivera@example.org",
        "MRN 1234567",
        "Call (202) 555-0143",
    ],
)
def test_phi_is_rejected(text):
    with pytest.raises(kb.PhiError):
        kb.clean_fields({"answer": text})
    with pytest.raises(kb.PhiError):
        kb.clean_fields({"question": text})


def test_clinic_material_is_not_phi():
    for text in [
        "Two to four weeks.",
        "Park on level 2.",
        "Monday to Friday, 8 to 5.",
        "Room 12345",
    ]:
        assert kb.phi_problems(text) == [], text


# --------------------------------------------------------------------------- recording and triage


def test_valid_entry_accepts_old_and_new_entries():
    old = pq.record("When can I shower?")
    assert pq.valid_entry(old)
    new = {
        **old,
        "outcome": "answered",
        "item_id": "i-shower",
        "item_version": 3,
        "answer": "Two days.",
        "satisfied": None,
    }
    assert pq.valid_entry(new)
    assert pq.valid_entry({**new, "satisfied": False, "outcome": "deferred"})
    for bad in (
        {"outcome": "maybe"},
        {"item_version": 0},
        {"item_version": "3"},
        {"satisfied": "no"},
        {"answer": "x" * 601},
        {"item_id": ""},
    ):
        assert not pq.valid_entry({**new, **bad}), bad


def _answers(*qs):
    return {survey.Q_HAS_QUESTIONS: "yes", survey.F_PATIENT_QUESTIONS: list(qs)}


def test_answered_and_satisfied_questions_do_not_raise_triage():
    answered = {
        **pq.record("When can I shower?"),
        "outcome": "answered",
        "item_id": "i",
        "item_version": 1,
        "answer": "Two days.",
        "satisfied": True,
    }
    t = survey.triage(_answers(answered))
    assert t.follow_up == survey.FOLLOW_UP_NONE
    assert not any("question" in r["text"] for r in t.reasons)
    # not asked whether it helped (ask_satisfied off): still no follow-up
    t = survey.triage(_answers({**answered, "satisfied": None}))
    assert not any("question" in r["text"] for r in t.reasons)


def test_unsatisfied_or_deferred_questions_are_triaged_as_today():
    base = pq.record("When can I shower?")
    unhappy = {
        **base,
        "outcome": "deferred",
        "item_id": "i",
        "item_version": 1,
        "answer": "Two days.",
        "satisfied": False,
    }
    today = survey.triage(_answers(base))
    t = survey.triage(_answers(unhappy))
    assert t.follow_up == today.follow_up == "routine_next_business_day"
    assert t.reasons == today.reasons
    # a medication question next to an answered one keeps the same-day tier
    med = {**pq.record("How much Tylenol can I take?"), "outcome": "deferred"}
    ok = {**base, "outcome": "answered", "item_id": "i", "item_version": 1, "satisfied": True}
    t = survey.triage(_answers(ok, med))
    assert t.follow_up == "urgent_same_day"
    assert [r["text"] for r in t.reasons if "question" in r["text"]] == [
        "question asked (medication)"
    ]


def test_example_wordings_that_read_as_medication_or_symptom_are_refused():
    from pathlib import Path

    import yaml

    for fields in (
        {"question": "Can I take ibuprofen?"},
        {"question": "When can I shower?", "alt_questions": ["Is my wound okay to wet?"]},
    ):
        with pytest.raises(ValueError, match="never answers"):
            kb.clean_fields(fields)
    # The category no longer has to match how the question is classified.
    assert kb.clean_fields({"question": "Where do I park?", "category": "appointment"})
    assert kb.clean_fields({"question": "When can I shower?", "category": "diet"})
    root = Path(__file__).resolve().parents[3]
    data = yaml.safe_load((root / "config/knowledge/sample_items.yaml").read_text())
    for it in data["items"]:
        kb.clean_fields(it)


# --------------------------------------------------------------------------- guidelines


def test_guideline_fields_and_limits():
    f = kb.clean_document_fields(
        {
            "title": "  Lifting  after knee surgery ",
            "body": "Line one.\n\n  Line   two.",
            "procedures": ["Knee arthroscopy", "knee ARTHROSCOPY", " "],
            "care_settings": ["outpatient", "inpatient"],
        }
    )
    assert f == {
        "title": "Lifting after knee surgery",
        "body": "Line one.\n\nLine two.",
        "procedures": ["Knee arthroscopy"],
        "care_settings": ["inpatient", "outpatient"],
    }
    for bad, msg in (
        ({"title": "x" * 121}, "at most 120"),
        ({"title": " "}, "required"),
        ({"body": "x" * 20_001}, "at most 20,000"),
        ({"procedures": ["p"] * 21}, "At most 20"),
        ({"procedures": ["p" * 121]}, "at most 120"),
        ({"care_settings": ["home"]}, "Care settings"),
    ):
        with pytest.raises(ValueError, match=msg):
            kb.clean_document_fields(bad)
    for phi in ("Call 202-555-0143", "MRN 1234567", "jordan@example.org", "Born 03/15/1950"):
        with pytest.raises(kb.PhiError):
            kb.clean_document_fields({"body": phi})
        with pytest.raises(kb.PhiError):
            kb.clean_document_fields({"procedures": [phi]})


@pytest.mark.parametrize(
    ("procedures", "settings", "procedure", "setting", "applies"),
    [
        ([], [], None, None, True),  # applies to everyone
        (["knee arthroscopy"], [], "Left Knee Arthroscopy", None, True),
        (["Total knee replacement"], [], "knee replacement", None, True),  # substring either way
        (["knee arthroscopy"], [], "hip replacement", None, False),
        (["knee arthroscopy"], [], None, None, False),  # unknown procedure
        ([], ["outpatient"], "anything", "outpatient", True),
        ([], ["outpatient"], "anything", "inpatient", False),
        ([], ["outpatient"], "anything", None, False),  # unknown setting
        (["hip", "knee"], ["inpatient"], "KNEE scope", "inpatient", True),
    ],
)
def test_guideline_applicability(procedures, settings, procedure, setting, applies):
    doc = {"procedures": procedures, "care_settings": settings}
    assert kb.document_applies(doc, procedure=procedure, care_setting=setting) is applies


def test_guideline_lifecycle_and_shape():
    f = kb.clean_document_fields({"title": "Driving", "body": "Wait six weeks."})
    doc = kb.new_document("d1", f, by="a@example.org", at=AT)
    pub = kb.public_document(doc)
    assert set(pub) == set(kb.DOCUMENT_KEYS) and pub["chars"] == 15 and pub["version"] == 1
    ok = kb.approve_item(doc, by="dr@example.org", at=LATER)
    assert kb.edit_document(ok, {"title": "Driving"}, by="x", at=LATER) is None
    v2 = kb.edit_document(ok, {"care_settings": ["inpatient"]}, by="x", at=LATER)
    assert v2["version"] == 2 and v2["status"] == "draft" and v2["approved_by"] is None


# --------------------------------------------------------------------------- patient context


def test_patient_context_never_holds_identifiers():
    patient = {
        "first_name": "Jordan",
        "last_name": "Rivera",
        "patient_name": "Jordan Rivera",
        "patient_id": "A1234567",
        "dob": "1950-03-15",
        "phone": "+12025550143",
        "operation_name": "  Total knee  replacement ",
        "operation_date": "2026-10-16",
        "is_diabetic": True,
        "care_setting": "outpatient",
    }
    ctx = kb.patient_context(patient, today=date(2026, 10, 20))
    assert ctx == {
        "procedure": "Total knee replacement",
        "days_since_surgery": 4,
        "care_setting": "outpatient",
        "is_diabetic": True,
    }
    assert (
        kb.patient_context(
            {"operation_date": "soon", "care_setting": "home"}, today=date(2026, 1, 1)
        )
        == {}
    )
    assert kb.patient_context({"operation_date": "2027-01-01"}, today=date(2026, 1, 1)) == {}


# --------------------------------------------------------------------------- shadow enqueue


async def test_shadow_is_enqueued_only_in_shadow_mode_with_questions():
    from voa_core.tasks import MemoryTaskQueue

    store, tasks = MemoryStore(), MemoryTaskQueue()
    survey_ = {"patient_questions": [pq.record("When can I shower?")]}
    go = lambda **kw: kb.enqueue_shadow(  # noqa: E731
        store, tasks, "voa-events", call_id="CA1", survey=survey_, channel="voice", **kw
    )
    assert not await go()  # off
    await store.set("settings", "knowledge", {"mode": "live"})
    kb.forget(store)
    assert not await go()  # live: answered during the call instead
    await store.set("settings", "knowledge", {"mode": "shadow"})
    kb.forget(store)
    assert await go(report_id="r1")
    assert not await go(report_id="r1")  # the named task de-duplicates
    assert tasks.tasks == [
        {
            "queue": "voa-events",
            "path": "/tasks/knowledge-shadow",
            "payload": {"call_id": "CA1"},
            "name": "kshadow-CA1-r1",
            "delay_s": 0,
        }
    ]
    assert not await kb.enqueue_shadow(
        store, tasks, "q", call_id="CA1", survey=survey_, channel="sms"
    )  # sms is off by default
    assert not await kb.enqueue_shadow(
        store, tasks, "q", call_id="CA1", survey={"patient_questions": []}, channel="voice"
    )


def test_valid_entry_accepts_v2_answers_shadow_and_review():
    base = pq.record("Can I lift my grandkid?")
    src = [{"kind": "guideline", "id": "d1", "title": "Lifting", "version": 2}]
    live = {
        **base,
        "outcome": "answered",
        "answer": "Not yet.",
        "sources": src,
        "support_score": 0.91,
        "satisfied": None,
    }
    assert pq.valid_entry(live)
    shadow = {
        "outcome": "deferred",
        "reason": "not_covered",
        "answer": None,
        "sources": [],
        "support_score": None,
        "at": AT,
    }
    review = {"verdict": "good", "note": None, "by": "rn@example.org", "at": AT}
    assert pq.valid_entry({**base, "shadow": shadow, "shadow_review": review})
    for bad in (
        {"sources": [{"kind": "web", "id": "x"}]},
        {"sources": "d1"},
        {"support_score": 1.5},
        {"support_score": True},
        {"shadow": {"outcome": "maybe", "reason": "x"}},
        {"shadow_review": {"verdict": "great"}},
        {"shadow_review": {"verdict": "good", "note": "x" * 301}},
    ):
        assert not pq.valid_entry({**live, **bad}), bad
