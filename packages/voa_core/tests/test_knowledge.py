"""Knowledge base step 1: the category gate, the matcher rules, item lifecycle, PHI."""

import asyncio

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


def always(item_id, confidence=1.0):
    calls = []

    async def matcher(question, candidates):
        calls.append((question, [c.item_id for c in candidates]))
        return kb.Match(item_id, confidence)

    matcher.calls = calls
    return matcher


async def ask(question, *, items=ITEMS, settings=None, matcher=None, **kw):
    return await kb.answer(
        question,
        items=items,
        settings=settings if settings is not None else {},
        matcher=matcher or always("i-shower"),
        **kw,
    )


# --------------------------------------------------------------------------- the gate


async def test_an_allowed_question_gets_the_clinics_exact_answer():
    r = await ask("When can I shower?")
    assert r.outcome == "answered" and r.reason == "matched"
    assert r.answer == "Two days after surgery." and r.item_id == "i-shower"
    assert r.item_version == 3 and r.confidence == 1.0 and r.category == "activity"


@pytest.mark.parametrize(
    "question",
    ["How much Tylenol can I take?", "Can I take ibuprofen before I drive?", "Is my wound red?"],
)
async def test_medication_and_symptom_always_defer_even_if_settings_allow_them(question):
    # Someone wrote the locked categories into settings/knowledge directly.
    settings = {"allowed_categories": ["medication", "symptom", "other", "activity"]}
    target = "i-meds" if "wound" not in question else "i-wound"
    m = always(target)
    r = await ask(question, settings=settings, matcher=m)
    assert r.outcome == "deferred" and r.reason == "locked_category"
    assert m.calls == []  # no model call


async def test_other_defers_and_never_calls_the_matcher():
    m = always("i-shower")
    r = await ask(
        "What about the thing we talked about?",
        settings={"allowed_categories": ["other"]},
        matcher=m,
    )
    assert (r.outcome, r.category, r.reason) == ("deferred", "other", "unclassified")
    assert m.calls == []


async def test_a_category_the_clinic_switched_off_defers():
    r = await ask("When can I shower?", settings={"allowed_categories": ["appointment"]})
    assert r.outcome == "deferred" and r.reason == "category_not_allowed"


async def test_a_red_flag_defers_before_anything_else():
    m = always("i-shower")
    r = await ask("I have chest pain, can I shower?", matcher=m)
    assert r.outcome == "deferred" and r.reason == "red_flag" and m.calls == []


async def test_no_candidates_defers_without_a_model_call():
    m = always("i-shower")
    r = await ask("Where can I get a walker?", matcher=m)  # supplies: no item
    assert r.reason == "no_approved_items" and m.calls == []


# --------------------------------------------------------------------------- the matcher


async def test_only_approved_items_in_the_category_are_candidates_and_answers_never_sent():
    draft = item("i-draft", "Can I shower now?", "Draft text", "activity", status=kb.DRAFT)
    retired = item("i-old", "Shower?", "Old text", "activity", status=kb.RETIRED)
    seen = {}

    async def matcher(question, candidates):
        seen["ids"] = [c.item_id for c in candidates]
        seen["text"] = repr(candidates)
        return kb.Match("i-shower", 0.95)

    r = await ask("When can I shower?", items=[*ITEMS, draft, retired], matcher=matcher)
    assert r.outcome == "answered"
    assert seen["ids"] == ["i-shower"]
    assert "Two days" not in seen["text"]  # the matcher never sees an answer


async def test_a_retired_or_draft_item_is_never_returned_even_if_the_matcher_names_it():
    retired = item("i-old", "Shower?", "Old text", "activity", status=kb.RETIRED)
    draft = item("i-draft", "Shower now?", "Draft", "activity", status=kb.DRAFT)
    for target in ("i-old", "i-draft", "i-park", "made-up"):
        r = await ask(
            "When can I shower?", items=[SHOWER, PARKING, retired, draft], matcher=always(target)
        )
        assert r.outcome == "deferred" and r.reason == "no_match", target


async def test_threshold():
    r = await ask("When can I shower?", matcher=always("i-shower", 0.79))
    assert r.outcome == "deferred" and r.reason == "low_confidence" and r.confidence == 0.79
    r = await ask("When can I shower?", matcher=always("i-shower", 0.8))
    assert r.outcome == "answered"
    r = await ask(
        "When can I shower?", settings={"min_match": 0.95}, matcher=always("i-shower", 0.9)
    )
    assert r.reason == "low_confidence"
    r = await ask("When can I shower?", matcher=always(None, 0.99))
    assert r.reason == "no_match"


async def test_timeout_and_errors_defer():
    async def slow(question, candidates):
        await asyncio.sleep(1)
        return kb.Match("i-shower", 1.0)

    async def broken(question, candidates):
        raise RuntimeError("model unavailable")

    async def nonsense(question, candidates):
        return kb.Match("i-shower", "very")  # type: ignore[arg-type]

    r = await ask("When can I shower?", matcher=slow, timeout_s=0.05)
    assert r.outcome == "deferred" and r.reason == "timeout"
    assert (await ask("When can I shower?", matcher=broken)).reason == "error"
    assert (await ask("When can I shower?", matcher=nonsense)).reason == "error"
    assert (await ask("   ")).reason == "empty_question"


def test_result_dict_shape():
    r = kb.KnowledgeResult("deferred", "activity", "no_match", confidence=0.4)
    assert r.to_dict() == {
        "outcome": "deferred",
        "category": "activity",
        "category_label": "Activity",
        "reason": "no_match",
        "confidence": 0.4,
    }


# --------------------------------------------------------------------------- settings


def test_settings_defaults_and_locked_categories_are_dropped():
    s = kb.normalize_settings(None)
    assert s == {
        "enabled": False,
        "channels": {"voice": True, "sms": True},
        "allowed_categories": [
            "appointment",
            "activity",
            "diet",
            "paperwork_billing",
            "supplies",
            "recovery",
        ],
        "min_match": 0.8,
        "ask_satisfied": True,
    }
    s = kb.normalize_settings(
        {"allowed_categories": ["medication", "symptom", "other", "diet"], "min_match": 0.1}
    )
    assert s["allowed_categories"] == ["diet"] and s["min_match"] == 0.5
    assert not kb.applies(kb.normalize_settings(None), "voice")
    on = kb.normalize_settings({"enabled": True, "channels": {"voice": False}})
    assert not kb.applies(on, "voice") and kb.applies(on, "sms")


async def test_settings_are_cached_and_forgotten():
    store = MemoryStore()
    assert (await kb.load_settings(store))["enabled"] is False
    await store.set("settings", "knowledge", {"enabled": True})
    assert (await kb.load_settings(store))["enabled"] is False  # cached
    kb.forget(store)
    assert (await kb.load_settings(store))["enabled"] is True
    await store.set("knowledge_items", "a", {**SHOWER, "item_id": "a"})
    await store.set("knowledge_items", "b", {**PARKING, "item_id": "b", "status": "draft"})
    assert [i["item_id"] for i in await kb.load_approved(store, fresh=True)] == ["a"]


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


def test_check_topic_refuses_items_that_could_never_be_used():
    from pathlib import Path

    import yaml

    kb.check_topic("When can I shower?", "activity")
    with pytest.raises(ValueError, match="reads as activity"):
        kb.check_topic("When can I shower?", "diet")
    with pytest.raises(ValueError, match="never answers"):
        kb.check_topic("Can I take ibuprofen?", "activity")
    with pytest.raises(ValueError, match="can't tell what topic"):
        kb.check_topic("Where do I park?", "appointment")
    root = Path(__file__).resolve().parents[3]
    data = yaml.safe_load((root / "config/knowledge/sample_items.yaml").read_text())
    for it in data.get("items", data) if isinstance(data, dict) else data:
        kb.check_topic(it["question"], it["category"])
