"""The by-text questionnaire: plan, parsers and triage.

The tests that matter most are the ones with clinical consequences: a patient
who needs a human quietly classified as needing nothing, and a negation read
as its opposite. Those are pinned first.
"""

import pytest
from voa_core import survey


def _ctx(**over):
    ctx = {
        "patient_name": "Jordan Avery",
        "patient_id": "PT-1",
        "patient_dob": "1960-06-15",
        "is_diabetic": False,
    }
    ctx.update(over)
    return ctx


CLEAN = {
    "overall_feeling": "ok",
    "pain_present": "no",
    "fever_reported": "no",
    "incision_concerns": "no",
}

# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------


def test_clean_answers_need_no_follow_up():
    t = survey.triage({**CLEAN, "overall_feeling": "pretty good"})
    assert t.follow_up == survey.FOLLOW_UP_NONE


@pytest.mark.parametrize(
    "answers,why",
    [
        ({"pain_present": "yes", "pain_severity": 8}, "pain 8/10"),
        ({"pain_present": "yes", "pain_severity": 10}, "pain 10/10"),
        ({"pain_trend": "worse"}, "worsening"),
        ({"fever_reported": "yes"}, "fever"),
        ({"incision_concerns": "yes", "incision_description": "red and leaking"}, "incision"),
        ({"incision_concerns": "yes"}, "incision, no description"),
        ({"blood_sugar_status": "high"}, "sugar high"),
        ({"blood_sugar_status": "low"}, "sugar low"),
    ],
)
def test_urgent_triggers(answers, why):
    assert survey.triage(answers).follow_up == survey.FOLLOW_UP_URGENT, why


@pytest.mark.parametrize(
    "text",
    ["I'd like a nurse to call me", "can I speak to someone", "please have the doctor call me"],
)
def test_asking_for_a_human_is_urgent(text):
    assert survey.triage({**CLEAN, "overall_feeling": text}).follow_up == survey.FOLLOW_UP_URGENT


@pytest.mark.parametrize("text", ["I'm really worried", "I don't understand what's happening"])
def test_expressed_worry_is_urgent(text):
    assert survey.triage({**CLEAN, "overall_feeling": text}).follow_up == survey.FOLLOW_UP_URGENT


@pytest.mark.parametrize(
    "word",
    [
        "terrible",
        "awful",
        "unbearable",
        "excruciating",
        "worst",
        "severe",
        "really bad",
        "very bad",
    ],
)
def test_severe_language_without_a_number_is_urgent(word):
    answers = {**CLEAN, "overall_feeling": f"the pain is {word}", "pain_present": "yes"}
    assert survey.triage(answers).follow_up == survey.FOLLOW_UP_URGENT


def test_pain_without_a_number_is_never_none():
    t = survey.triage({**CLEAN, "pain_present": "yes", "overall_feeling": "sore"})
    assert t.follow_up == survey.FOLLOW_UP_ROUTINE


@pytest.mark.parametrize("field", ["fever_reported", "incision_concerns", "blood_sugar_status"])
def test_unknown_findings_are_never_none(field):
    assert survey.triage({**CLEAN, field: "unknown"}).follow_up == survey.FOLLOW_UP_ROUTINE


@pytest.mark.parametrize(
    "sev,expected",
    [
        (0, survey.FOLLOW_UP_NONE),
        (1, survey.FOLLOW_UP_INFO),
        (3, survey.FOLLOW_UP_INFO),
        (4, survey.FOLLOW_UP_ROUTINE),
        (7, survey.FOLLOW_UP_ROUTINE),
        (8, survey.FOLLOW_UP_URGENT),
    ],
)
def test_severity_boundaries(sev, expected):
    answers = {
        **CLEAN,
        "pain_present": "yes",
        "pain_severity": sev,
        "pain_location": "knee",
        "pain_trend": "same",
    }
    assert survey.triage(answers).follow_up == expected


def test_reason_never_leaks_the_procedure_or_identity():
    reason = survey.triage(
        {
            "overall_feeling": "sore after the knee replacement on my birthday 1960-06-15",
            "pain_present": "yes",
            "pain_severity": 9,
            "pain_location": "knee",
            "pain_trend": "worse",
        }
    ).reason
    for leak in ("knee replacement", "1960", "diabet", "birthday"):
        assert leak.lower() not in reason.lower()


@pytest.mark.parametrize(
    "answers,expected",
    [
        ({"pain_present": "yes", "pain_severity": 9}, "pain_severity"),
        ({"fever_reported": "yes"}, "fever_reported"),
        ({"pain_trend": "worse"}, "pain_trend"),
        ({"blood_sugar_status": "high"}, "blood_sugar_status"),
    ],
)
def test_the_triggering_field_is_named(answers, expected):
    assert expected in survey.triage(answers).triggers


def test_prose_triggers_point_at_the_answer_they_were_typed_into():
    answers = {
        **CLEAN,
        "overall_feeling": "fine",
        survey.RAW_KEY: {
            "overall_feeling": "fine",
            "incision_concerns": "no, but please have a nurse call me",
        },
    }
    t = survey.triage(answers)
    assert t.follow_up == survey.FOLLOW_UP_URGENT
    assert "incision_concerns" in t.triggers


def test_a_clean_survey_names_nothing():
    t = survey.triage({**CLEAN, "overall_feeling": "good"})
    assert t.follow_up == survey.FOLLOW_UP_NONE
    assert t.triggers == ()


def test_legacy_list_shaped_raw_still_scans_for_prose():
    answers = {**CLEAN, survey.RAW_KEY: ["ok", "no", "I'm really worried", "no"]}
    assert survey.triage(answers).follow_up == survey.FOLLOW_UP_URGENT
    assert survey.raw_answers(answers) == {}  # no field attribution available


def test_has_urgent_finding():
    assert survey.has_urgent_finding({"fever_reported": "yes"})
    assert not survey.has_urgent_finding({"pain_present": "no"})


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


def test_non_diabetic_gets_four_questions():
    assert survey.build_plan(_ctx()) == [
        "overall_feeling",
        "pain_present",
        "fever_reported",
        "incision_concerns",
    ]


def test_diabetic_gets_the_blood_sugar_question():
    assert survey.build_plan(_ctx(is_diabetic=True))[-1] == "blood_sugar_status"


@pytest.mark.parametrize("value", ["false", "False", "", "0", "no", None])
def test_string_false_does_not_make_a_diabetic(value):
    assert "blood_sugar_status" not in survey.build_plan(_ctx(is_diabetic=value))


@pytest.mark.parametrize("value", [True, "true", "True", "1", "yes"])
def test_truthy_forms_do_make_a_diabetic(value):
    assert "blood_sugar_status" in survey.build_plan(_ctx(is_diabetic=value))


def test_no_context_is_the_four_question_plan():
    assert len(survey.build_plan(None)) == 4


def test_pain_yes_triggers_exactly_the_required_followups():
    assert survey.followups_for("pain_present", "yes") == (
        "pain_severity",
        "pain_location",
        "pain_trend",
    )
    assert survey.followups_for("incision_concerns", "yes") == ("incision_description",)


def test_pain_no_triggers_nothing():
    assert survey.followups_for("pain_present", "no") == ()


# ---------------------------------------------------------------------------
# Parsers -- what a handset actually sends
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["yes", "Y", "yeah", "yep", "a little", "kind of", "mild"])
def test_hedged_yes_is_yes(text):
    assert survey.parse_yes_no(text) == "yes"


@pytest.mark.parametrize("text", ["no", "N", "nope", "none", "not really", "no pain"])
def test_no_variants(text):
    assert survey.parse_yes_no(text) == "no"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("8", 8),
        ("eight", 8),
        ("about a 4", 4),
        ("0", 0),
        ("10", 10),
        ("4-5", 5),
        ("4 or 5", 5),  # a range takes the higher end, per voice
    ],
)
def test_scale_parsing(text, expected):
    assert survey.parse_scale(text) == expected


@pytest.mark.parametrize("text", ["", "no idea", "hurts", "100", "9" * 5000])
def test_unparseable_scale(text):
    # The last one is review S-10: int() on >4300 digits used to raise.
    assert survey.parse_scale(text) is None


@pytest.mark.parametrize(
    "text,expected",
    [("better", "better"), ("W", "worse"), ("about the same", "same"), ("getting worse", "worse")],
)
def test_trend_parsing(text, expected):
    assert survey.parse_trend(text) == expected


def test_free_text_is_capped():
    assert len(survey.parse_free_text("x" * 900)) == survey.MAX_FREE_TEXT
    assert survey.parse_free_text("   ") is None


@pytest.mark.parametrize(
    "text", ["no fever but some pain", "yes and no", "a little but no not really"]
)
def test_an_answer_carrying_both_signals_is_unclear(text):
    assert survey.parse_yes_no(text) is None


def test_word_boundaries_are_respected():
    assert survey.parse_yes_no("nothing") == "no"
    assert survey.parse_yes_no("synonymous") is None


@pytest.mark.parametrize(
    "text,expected",
    [("0/10", 0), ("2/10", 2), ("3/10", 3), ("10/10", 10), ("3 out of 10", 3), ("7 of 10", 7)],
)
def test_clinical_slash_notation(text, expected):
    """ "0/10" normalised to "0 10" and took the max: every score became a 10."""
    assert survey.parse_scale(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [("none", 0), ("no pain", 0), ("zero", 0), ("no idea", None)],
)
def test_zero_is_an_exact_whitelist(text, expected):
    assert survey.parse_scale(text) == expected


# --- Negation (review finding C-6) -------------------------------------------


@pytest.mark.parametrize("text", ["not worse", "no worse", "not better"])
def test_negated_trend_is_unclear_not_inverted(text):
    assert survey.parse_trend(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "not getting worse",  # C-6 pin: used to escalate as urgent_same_day
        "it is not any worse",
        "no it's not worse",
        "not really getting worse",
    ],
)
def test_negation_reaches_past_the_first_word(text):
    assert survey.parse_trend(text) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("no pain but getting worse", "worse"),  # two clauses; the second stands
        ("no, getting worse", "worse"),
        ("not better, worse", "worse"),
    ],
)
def test_negation_stops_at_the_clause(text, expected):
    assert survey.parse_trend(text) == expected


@pytest.mark.parametrize("text", ["not in range", "not good", "not ok"])
def test_negated_sugar_is_not_in_range(text):
    assert survey.parse_sugar(text) != "in_range"


@pytest.mark.parametrize(
    "text",
    [
        "not really high",  # C-6 pin: used to file "high" -> urgent
        "not high at all",
        "never been high",
    ],
)
def test_negated_high_sugar_is_unclear_not_urgent(text):
    assert survey.parse_sugar(text) is None


@pytest.mark.parametrize("text", ["not sure", "I am not sure", "im not sure"])
def test_not_sure_is_not_a_yes(text):
    """C-6 pin: "not sure" on the pain question fabricated a pain report."""
    assert survey.parse_yes_no(text) is None


def test_bare_sure_and_i_am_still_mean_yes():
    assert survey.parse_yes_no("sure") == "yes"
    assert survey.parse_yes_no("I am") == "yes"
    assert survey.parse_yes_no("I have some pain") == "yes"


# --- Blood sugar -------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("out of range", None),  # abnormal, but which way? ask again
        ("ok a bit high", "high"),
        ("in range", "in_range"),
        ("good", "in_range"),
        ("steady", "in_range"),
        ("not good", None),
    ],
)
def test_sugar_words(text, expected):
    assert survey.parse_sugar(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("250", "high"),  # review S-10 pin: used to be unparseable
        ("it was 250 this morning", "high"),
        ("181", "high"),
        ("180", "in_range"),
        ("110", "in_range"),
        ("70", "in_range"),
        ("69", "low"),
        ("55", "low"),
        ("fine, 250", "high"),  # an abnormal number beats reassuring words
        ("130, a bit high for me", "high"),  # abnormal words beat an in-range number
        ("120 then 250", "high"),
        ("65 this morning, 250 at night", None),  # both directions: ask again
        ("5.6", None),  # a decimal is probably mmol/L, not mg/dL: not a reading
        ("1000", None),  # outside what a meter displays
        ("9" * 5000, None),
    ],
)
def test_sugar_readings(text, expected):
    assert survey.parse_sugar(text) == expected


def test_parse_answer_dispatches_by_field():
    assert survey.parse_answer("pain_severity", "7") == 7
    assert survey.parse_answer("pain_present", "yep") == "yes"
    assert survey.parse_answer("blood_sugar_status", "250") == "high"
    assert survey.parse_answer("overall_feeling", " fine ") == "fine"
