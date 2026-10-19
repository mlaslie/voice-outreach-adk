"""Patient questions: deterministic category and follow-up priority, and triage."""

import pytest
from voa_core import patient_questions as pq
from voa_core import reports, survey

NOW, NEXT = pq.PRIORITY_IMMEDIATE, pq.PRIORITY_NEXT_DAY


@pytest.mark.parametrize(
    ("text", "category", "priority"),
    [
        # Medication: which, when, how much -> immediate (may affect their health).
        ("Which pain medicine should I be taking?", "medication", NOW),
        ("When do I take my antibiotic?", "medication", NOW),
        ("How much Tylenol can I take?", "medication", NOW),
        ("I missed a dose last night, what do I do", "medication", NOW),
        ("Should I stop taking my blood thinner?", "medication", NOW),
        ("Can I take ibuprofen before I drive?", "medication", NOW),  # immediate wins
        ("I need a refill on my oxycodone", "medication", NOW),
        # Symptoms and complications -> immediate.
        ("Is it normal for the incision to ooze a little?", "symptom", NOW),
        ("Why is my ankle swollen?", "symptom", NOW),
        ("When does the drain come out?", "symptom", NOW),
        ("I can't keep food down, is that okay?", "symptom", NOW),
        # Next business day.
        ("When is my follow-up appointment?", "appointment", NEXT),
        ("When can I drive again?", "activity", NEXT),
        ("Can I shower yet?", "activity", NEXT),
        ("Can I have a glass of wine with dinner? Any foods to avoid?", "diet", NEXT),
        ("I need a note for my employer for FMLA", "paperwork_billing", NEXT),
        ("Where do I get a walker?", "supplies", NEXT),
        ("How long until I'm back to normal?", "recovery", NEXT),
        # Unclear -> immediate (err toward a same-day nurse).
        ("What about the thing we talked about?", "other", NOW),
        ("", "other", NOW),
    ],
)
def test_classify(text, category, priority):
    c = pq.classify(text)
    assert (c.category, c.priority) == (category, priority)


def test_record_normalises_and_bounds_the_text():
    r = pq.record("  When   do I take\nmy meds?  " + "x" * 400)
    assert r["text"].startswith("When do I take my meds?")
    assert len(r["text"]) == pq.MAX_QUESTION_CHARS
    assert r["priority"] == NOW and pq.valid_entry(r)


BASE = {
    "overall_feeling": "good",
    "pain_present": "no",
    "fever_reported": "no",
    "incision_concerns": "no",
    "legs_swollen": "no",
    "sleeping_well": "yes",
    "has_questions": "no",
}


def test_a_clean_call_needs_nothing():
    assert survey.triage(BASE).follow_up == "none"


def test_a_medication_question_is_same_day():
    t = survey.triage(
        {**BASE, "has_questions": "yes", "patient_questions": [pq.record("How much Tylenol?")]}
    )
    assert t.follow_up == "urgent_same_day"
    assert "question asked (medication)" in t.reason and "patient_questions" in t.triggers


def test_a_routine_question_is_next_business_day():
    t = survey.triage(
        {**BASE, "has_questions": "yes", "patient_questions": [pq.record("Can I shower?")]}
    )
    assert t.follow_up == "routine_next_business_day"
    assert t.reason == "Patient reports question asked (activity)."


def test_yes_to_questions_with_none_captured_still_gets_a_nurse():
    t = survey.triage({**BASE, "has_questions": "yes"})
    assert t.follow_up == "routine_next_business_day"


def test_a_question_mentioning_a_doctor_is_not_a_request_to_speak_to_someone():
    # Scanned by the classifier, not the "wants a human" prose rule.
    t = survey.triage(
        {
            **BASE,
            "has_questions": "yes",
            "patient_questions": [pq.record("When will I see my doctor again?")],
            "_raw": {"has_questions": "yes, when will I see my doctor again"},
        }
    )
    assert t.follow_up == "routine_next_business_day"


@pytest.mark.parametrize(
    ("desc", "tier"),
    [
        ("both feet, a little puffy at night", "routine_next_business_day"),
        ("just the left leg", "urgent_same_day"),
        ("both, and it's getting worse", "urgent_same_day"),
        ("my calf is swollen and tender", "urgent_same_day"),
    ],
)
def test_swelling(desc, tier):
    t = survey.triage({**BASE, "legs_swollen": "yes", "swelling_description": desc})
    assert t.follow_up == tier


def test_poor_sleep_alone_is_informational():
    t = survey.triage({**BASE, "sleeping_well": "no"})
    assert t.follow_up == "informational" and "not sleeping well" in t.reason


def test_completed_report_validates_questions_and_swelling():
    def errs(answers):
        _, e = reports.validate_report(
            {
                "patient_id": "P-1",
                "follow_up": "none",
                "follow_up_reason": "x",
                "outcome": "questionnaire_completed",
                "survey_responses": answers,
            }
        )
        return " ".join(e)

    assert errs(BASE) == ""
    assert "swelling_description" in errs({**BASE, "legs_swollen": "yes"})
    assert "patient_questions" in errs({**BASE, "patient_questions": [{"text": "hi"}]})
    too_many = [pq.record("Can I shower?")] * (pq.MAX_QUESTIONS + 1)
    assert "at most" in errs({**BASE, "patient_questions": too_many})


@pytest.mark.parametrize(
    ("desc", "urgent"),
    [
        ("only my left leg", True),
        ("both, not painful, no redness", False),
        ("both ankles, getting worse", True),
        ("both feet a bit puffy", False),
    ],
)
def test_swelling_is_urgent_ignores_negated_words(desc, urgent):
    assert survey.swelling_is_urgent(desc) is urgent
