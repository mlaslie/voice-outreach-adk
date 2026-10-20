"""The questionnaire of 19 Oct 2026: leg swelling, sleep, the patient's own blood
sugar reading, and questions for the care team. Old calls (without the new keys)
still render, with "Not asked". Fictional data only."""

import pytest
from nurse_fixtures import FakeBackend, check, configure_cards, make_turn
from voa_nurse import actions, cards, triage, turn
from voa_nurse import worklists as wl

OLD_CALL = {
    "doc_id": "CA" + "3" * 32,
    "to": "+12025550133",
    "initiated_at": "2026-10-07T14:00:00+00:00",
    "call_status": "success",
    "outcome": "questionnaire_completed",
    "patient_id": "P-3001",
    "patient_name": "Rosa Lindqvist",
    "follow_up": "urgent_same_day",
    "follow_up_reason": "Fever reported.",
    "patient_context": {"is_diabetic": False},
    "survey_responses": {
        "pain_present": "no",
        "fever_reported": "yes",
        "incision_concerns": "no",
        "overall_feeling": "tired",
    },
}
NEW_CALL = {
    **OLD_CALL,
    "doc_id": "CA" + "4" * 32,
    "patient_context": {"is_diabetic": True},
    "follow_up_reason": "Asked a medication question; leg swelling.",
    "survey_responses": {
        "pain_present": "yes",
        "pain_severity": 5,
        "pain_location": "throbbing behind the knee",
        "pain_trend": "same",
        "fever_reported": "no",
        "incision_concerns": "no",
        "legs_swollen": "yes",
        "swelling_description": "only the left leg, and it's getting worse",
        "sleeping_well": "no",
        "blood_sugar_status": "in_range",
        "blood_sugar_reading": "about 110 to 140",
        "has_questions": "yes",
        "patient_questions": [
            {
                "text": "Do I need new compression socks?",
                "category": "supplies",
                "priority": "routine_next_business_day",
            },
            {
                "text": "Should I keep taking the blood thinner?",
                "category": "medication",
                "priority": "urgent_same_day",
            },
            {
                "text": "When can I shower?",
                "category": "activity",
                "priority": "routine_next_business_day",
            },
        ],
        "overall_feeling": "okay",
    },
}


def _with(**responses) -> dict:
    return {**NEW_CALL, "survey_responses": {**NEW_CALL["survey_responses"], **responses}}


@pytest.fixture(autouse=True)
def _cards():
    configure_cards()


def _findings(call) -> dict:
    return {f["label"]: f for f in triage.findings_for(call)}


# --- findings ------------------------------------------------------------------------------


def test_findings_for_the_new_answers():
    f = _findings(NEW_CALL)
    assert f["Leg swelling"]["value"] == "Reports leg swelling"
    assert f["Leg swelling"]["level"] == "alert"  # one leg, getting worse
    assert "only the left leg" in f["Leg swelling"]["detail"]
    assert (f["Sleep"]["value"], f["Sleep"]["level"]) == ("Not sleeping well", "watch")
    assert f["Blood glucose"]["level"] == "normal"
    assert "about 110 to 140" in f["Blood glucose"]["detail"]
    assert "throbbing behind the knee" in f["Pain"]["detail"]
    q = f["Patient questions"]
    assert (q["value"], q["level"]) == ("3 questions · 1 immediate", "alert")


def test_swelling_in_both_legs_is_watch_not_alert():
    f = _findings(_with(swelling_description="both ankles a little, same as yesterday"))
    assert f["Leg swelling"]["level"] == "watch"


def test_denials_read_as_normal():
    f = _findings(
        _with(legs_swollen="no", sleeping_well="yes", has_questions="no", patient_questions=[])
    )
    assert (f["Leg swelling"]["value"], f["Leg swelling"]["level"]) == (
        "Denies leg swelling",
        "normal",
    )
    assert f["Sleep"]["level"] == "normal"
    assert (f["Patient questions"]["value"], f["Patient questions"]["level"]) == (
        "No questions",
        "normal",
    )


def test_an_old_call_reads_not_asked_for_the_new_questions():
    f = _findings(OLD_CALL)
    for label in ("Leg swelling", "Sleep", "Patient questions"):
        assert (f[label]["value"], f[label]["level"]) == ("Not asked", "unasked")
    assert "Blood glucose" not in f  # not diabetic, nothing reported: as before
    assert f["Fever"]["level"] == "alert"


def test_questions_are_immediate_first_and_labelled():
    qs = triage.patient_questions(NEW_CALL)
    assert [q["priority_label"] for q in qs] == [
        "Immediate",
        "Next business day",
        "Next business day",
    ]
    assert qs[0]["category_label"] == "Medication"
    # asked order kept within a priority
    assert [q["text"] for q in qs[1:]] == ["Do I need new compression socks?", "When can I shower?"]


def test_odd_question_entries_are_shown_safely():
    qs = triage.patient_questions(
        _with(patient_questions=[{"text": "Is this normal?"}, {"text": ""}, None, "Can I drive?"])
    )
    assert [(q["text"], q["category_label"], q["priority_label"]) for q in qs] == [
        ("Is this normal?", "Other", "Immediate"),
        ("Can I drive?", "Other", "Immediate"),
    ]


def test_question_summary():
    assert triage.questions_summary(NEW_CALL) == "3 questions · 1 immediate"
    one = _with(patient_questions=[NEW_CALL["survey_responses"]["patient_questions"][0]])
    assert triage.questions_summary(one) == "1 question"
    assert triage.questions_summary(_with(patient_questions=[])) == "Has questions, none captured"
    assert triage.questions_summary(OLD_CALL) is None


# --- detail card ---------------------------------------------------------------------------


def test_detail_card_lists_questions_immediate_first():
    card = cards.call_detail_card(NEW_CALL)
    check(card)
    text = card.as_text()
    head = "Patient questions — nurse follow-up required"
    assert head in text
    block = text[text.index(head) :]
    first = block.index("“Should I keep taking the blood thinner?”")
    assert first < block.index("“Do I need new compression socks?”")
    assert first < block.index("“When can I shower?”")
    assert "Medication · Immediate" in block
    assert "Supplies · Next business day" in block
    # listed once, in the block, not again as a symptom-review line
    assert "Patient questions: 3 questions" not in text
    assert "Leg swelling: Reports leg swelling" in text and "Sleep: Not sleeping well" in text


def test_detail_card_says_to_call_when_questions_were_not_captured():
    card = cards.call_detail_card(_with(patient_questions=[]))
    check(card)
    text = card.as_text()
    assert "Patient questions — nurse follow-up required" in text
    assert "Patient has questions — none captured; call to ask." in text


def test_an_old_call_still_renders_without_the_questions_block():
    card = cards.call_detail_card(OLD_CALL)
    check(card)
    text = card.as_text()
    assert "Symptom review" in text and "Fever: Reports fever" in text
    assert "nurse follow-up required" not in text
    assert "Leg swelling: Not asked" in text


def test_list_rows_show_the_question_count():
    assert "3 questions · 1 immediate" in cards.calls_card([NEW_CALL], "Calls").as_text()
    assert "3 questions · 1 immediate" in cards.follow_up_card([NEW_CALL]).as_text()
    assert "question" not in cards.follow_up_card([OLD_CALL]).as_text()


# --- worklists and the model's data ---------------------------------------------------------


@pytest.fixture
def t():
    with turn.active(make_turn(backend=FakeBackend("nurse_day"))) as current:
        yield current


def _sid(t, name):
    return next(c for c in t.backend.state["calls"] if c["patient_name"] == name)["doc_id"]


async def test_worklists_count_questions(t):
    card, data = await wl.view_needs("now")
    check(card)
    assert "2 questions · 1 immediate" in card.as_text()  # Priya
    priya = next(p for p in data["patients"] if p["patient"] == "Priya Raman")
    assert priya["questions_summary"] == "2 questions · 1 immediate"
    card, _ = await wl.view_needs("next_day")
    text = card.as_text()
    assert "2 questions" in text  # Lucia, both next business day
    assert "Has questions, none captured" in text  # Harold


async def test_get_call_returns_the_questions_to_the_model(t):
    card, data = await actions.view_call(call_sid=_sid(t, "Priya Raman"))
    check(card)
    assert data["patient_questions"] == [
        {
            "text": "I ran out of my pain pills, can I get a refill?",
            "category_label": "Medication",
            "priority_label": "Immediate",
        },
        {
            "text": "When is my follow-up appointment with the surgeon?",
            "category_label": "Appointment",
            "priority_label": "Next business day",
        },
    ]
    assert data["has_questions_none_captured"] is False
    assert data["questions_summary"] == "2 questions · 1 immediate"


async def test_follow_up_queue_data_carries_the_count(t):
    _, data = await actions.view_follow_up_queue()
    by_name = {p["patient"]: p for p in data["outstanding"]}
    assert by_name["Priya Raman"]["questions_summary"] == "2 questions · 1 immediate"
    assert "questions_summary" not in by_name["Marcus Ellery"]


# --- knowledge base: questions the agent answered from the clinic's Q&A -----------------------


def test_detail_card_shows_questions_answered_from_the_clinics_qa():
    answered = {
        "text": "When can I shower?",
        "category": "activity",
        "priority": "routine_next_business_day",
        "outcome": "answered",
        "item_id": "i-001",
        "item_version": 3,
        "answer": "You can usually shower two days after surgery.",
        "satisfied": True,
    }
    unhappy = {
        **answered,
        "text": "Where do I park?",
        "category": "appointment",
        "outcome": "deferred",
        "item_version": 2,
        "satisfied": False,
    }
    plain = {
        "text": "Do I need new compression socks?",
        "category": "supplies",
        "priority": "routine_next_business_day",
        "outcome": "deferred",
    }
    card = cards.call_detail_card(_with(patient_questions=[answered, unhappy, plain]))
    check(card)
    text = card.as_text()
    assert "Answered by the agent from the clinic's Q&A (v3)" in text
    assert (
        "Answered by the agent from the clinic's Q&A (v2); patient said it didn't answer it" in text
    )
    assert text.count("Answered by the agent") == 2  # the deferred one has no line
    qs = triage.patient_questions(_with(patient_questions=[answered, plain]))
    assert qs[0]["item_version"] == 3 and qs[0]["satisfied"] is True
    assert "item_version" not in qs[1]
