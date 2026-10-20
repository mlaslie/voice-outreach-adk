"""config/campaigns/post_op.yaml is edited by non-developers. Catch bad edits here.

`uv run pytest -q -p no:warnings packages/voa_core packages/voa_agents` is the
check docs/EDITING-QUESTIONS.md tells an editor to run; the messages below are
what they will read, so they say what is wrong in the file's own terms.
"""

from pathlib import Path

import pytest
from voa_core import copy as messages
from voa_core import survey
from voa_core.campaign import load_campaign

CONFIG = Path(__file__).parents[3] / "config"
VOICE_PLACEHOLDERS = {"org_name", "patient_first_name", "patient_last_name"}


@pytest.fixture
def c():
    return load_campaign(survey.SMS_CAMPAIGN_ID, CONFIG)


def _with_question(c, qid, **update):
    return c.model_copy(
        update={
            "questions": [q.model_copy(update=update) if q.id == qid else q for q in c.questions]
        }
    )


def _with_script(c, key, text):
    return c.model_copy(update={"sms_scripts": {**c.sms_scripts, key: text}})


# --- the file as committed ------------------------------------------------------


def test_every_question_can_be_parsed_and_triaged(c):
    assert survey.plan_problems(c) == []


def test_all_sms_copy_is_present_and_sendable(c):
    assert messages.sms_copy_problems(c) == []


def test_voice_scripts_use_only_known_placeholders(c):
    for key, text in c.scripts.items():
        unknown = messages.placeholders(text) - VOICE_PLACEHOLDERS
        assert not unknown, f"scripts.{key}: unknown placeholder(s) {sorted(unknown)}"
    for q in c.questions:
        # `ask` and `note` are not filled in: a {name} would reach the model as is.
        for field in ("ask", "note"):
            assert not messages.placeholders(getattr(q, field)), f"{q.id}.{field}: no placeholders"


def test_red_flags_are_listed(c):
    assert c.red_flags and all(f.strip() for f in c.red_flags)


def test_the_runtime_reads_the_same_file(c):
    assert survey.sms_campaign() == c


# --- the checks catch what an editor could get wrong -----------------------------


def test_a_question_without_sms_copy_fails(c):
    bad = _with_question(c, "sleeping_well", sms="")
    assert any("sleeping_well: missing `sms`" in p for p in messages.sms_copy_problems(bad))
    bad = _with_question(c, "pain_trend", sms="  ")  # a follow-up too
    assert any("pain_trend: missing `sms`" in p for p in messages.sms_copy_problems(bad))


def test_an_unknown_question_id_fails(c):
    extra = c.questions[0].model_copy(update={"id": "appetite"})
    bad = c.model_copy(update={"questions": [*c.questions, extra]})
    assert any("appetite: unknown question id" in p for p in survey.plan_problems(bad))


def test_a_removed_question_fails(c):
    bad = c.model_copy(update={"questions": [q for q in c.questions if q.id != "pain_trend"]})
    assert any("pain_trend: missing" in p for p in survey.plan_problems(bad))


def test_a_when_on_an_unknown_id_fails(c):
    bad = _with_question(c, "pain_trend", when={"pain_presnt": "yes"})
    assert any("unknown question 'pain_presnt'" in p for p in survey.plan_problems(bad))


def test_a_when_on_a_later_question_fails(c):
    bad = _with_question(c, "pain_trend", when={"legs_swollen": "yes"})
    assert any("not asked before it" in p for p in survey.plan_problems(bad))


def test_a_follow_up_of_a_follow_up_fails(c):
    bad = _with_question(c, "pain_trend", when={"pain_severity": "yes"})
    assert any("itself a follow-up" in p for p in survey.plan_problems(bad))


def test_an_unknown_patient_flag_fails(c):
    bad = _with_question(c, "blood_sugar_status", when_patient={"is_diabetc": True})
    assert any("unknown flag 'is_diabetc'" in p for p in survey.plan_problems(bad))


@pytest.mark.parametrize(
    ("key", "text", "expect"),
    [
        ("preamble", "I'll send {count} questions.", "unknown placeholder {count}"),
        ("numbered_question", "({number}/{total})", "must keep the {question}"),
        ("closing_routine", "Thanks. {questions_follow_up}Bye.", "{medication_reminder}"),
        ("question_noted", 'Got it: "{question}". {other}', "unknown placeholder {other}"),
        ("ask_question", "What is your {question?", "bad braces"),
        ("another_question", "Any other questions…", "not plain GSM-7"),
        ("file_failed", "", "sms_scripts.file_failed: missing"),
    ],
)
def test_bad_sms_scripts_fail(c, key, text, expect):
    problems = messages.sms_copy_problems(_with_script(c, key, text))
    assert any(expect in p for p in problems), problems


def test_an_unknown_sms_script_fails(c):
    problems = messages.sms_copy_problems(_with_script(c, "closing_rutine", "Bye."))
    assert any("sms_scripts.closing_rutine: unknown line" in p for p in problems)


def test_question_copy_placeholders_and_quotes_are_checked(c):
    bad = _with_question(c, "pain_location", sms_if_known="Pain at your {place}?")
    assert any("unknown placeholder {place}" in p for p in messages.sms_copy_problems(bad))
    bad = _with_question(c, "legs_swollen", sms="Are your legs swollen, {patient_first_name}?")
    assert any("unknown placeholder" in p for p in messages.sms_copy_problems(bad))
    bad = _with_question(c, "sleeping_well", sms="Are you sleeping “well”?")
    assert any("not plain GSM-7" in p for p in messages.sms_copy_problems(bad))


def test_a_variant_on_the_wrong_question_fails(c):
    bad = _with_question(c, "pain_trend", sms_if_known="You said {site}.")
    assert any("not used for this question" in p for p in messages.sms_copy_problems(bad))
    bad = _with_question(c, "swelling_description", sms_if_side_known="")
    assert any("missing `sms_if_side_known`" in p for p in messages.sms_copy_problems(bad))


# --- the plan follows the file ----------------------------------------------------


def test_the_plan_follows_the_campaign_order(c):
    qs = list(c.questions)
    i, j = [q.id for q in qs].index("fever_reported"), [q.id for q in qs].index("sleeping_well")
    qs[i], qs[j] = qs[j], qs[i]
    reordered = c.model_copy(update={"questions": qs})
    plan = survey.build_plan({"is_diabetic": False}, reordered)
    assert plan.index("sleeping_well") < plan.index("fever_reported")


def test_follow_ups_follow_the_campaign(c):
    assert survey.followups_for("pain_present", "yes", c) == (
        "pain_severity",
        "pain_location",
        "pain_trend",
    )
    moved = _with_question(c, "incision_description", when={"incision_concerns": "no"})
    assert survey.followups_for("incision_concerns", "yes", moved) == ()
    assert survey.followups_for("incision_concerns", "no", moved) == ("incision_description",)


def test_patient_text_with_braces_is_echoed_literally():
    assert '"what is {this}"' in messages.question_noted("what is {this}")
