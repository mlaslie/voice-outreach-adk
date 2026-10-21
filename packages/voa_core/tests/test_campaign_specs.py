"""Every use-case spec under config/campaigns: valid, and its own tests pass.

Plus the pre-op example end to end by text, to show a second use case runs on
the engine with no code of its own (docs/USE-CASES-PLAN.md, step 1).
"""

import copy
import datetime
from pathlib import Path

import pytest
from voa_core import copy as messages
from voa_core import reports, survey
from voa_core.campaign import UnknownCampaign, campaign_ids, load_campaign, validate_campaign_id
from voa_core.campaign_lint import campaign_problems
from voa_core.copy import SiteCopy
from voa_core.rules import spec_test_failures
from voa_core.sms import conversation
from voa_core.sms.conversation import ConversationPolicy, Effect

CONFIG = Path(__file__).parents[3] / "config"
IDS = campaign_ids(CONFIG)


def test_both_use_cases_are_present():
    assert {"post_op", "pre_op"} <= set(IDS)


@pytest.mark.parametrize("cid", IDS)
def test_every_spec_is_valid(cid):
    assert campaign_problems(load_campaign(cid, CONFIG)) == []


@pytest.mark.parametrize("cid", IDS)
def test_every_spec_runs_its_own_tests(cid):
    c = load_campaign(cid, CONFIG)
    assert c.tests, f"{cid}: a use case needs scripted tests"
    assert spec_test_failures(c) == []


def test_a_failing_spec_test_is_reported():
    c = load_campaign("pre_op", CONFIG)
    t = c.tests[0].model_copy(
        update={"expect": c.tests[0].expect.model_copy(update={"tier": "urgent_same_day"})}
    )
    bad = c.model_copy(update={"tests": [t]})
    assert any("expected tier urgent_same_day, got none" in p for p in campaign_problems(bad))


def test_campaign_ids_are_validated():
    assert validate_campaign_id(None) is None and validate_campaign_id("pre_op") == "pre_op"
    for bad in ("nope", "../post_op", "Post_Op", 7):
        with pytest.raises(ValueError):
            validate_campaign_id(bad)
    with pytest.raises(UnknownCampaign):
        load_campaign("../../etc/passwd")


# --- pre-op by text, end to end ------------------------------------------------------

SITE = SiteCopy(org_name="Acme Health", support_email="support@example.org")
NOW = datetime.datetime(2026, 8, 17, 10, 0, tzinfo=datetime.UTC)


def _walk(replies, campaign_id="pre_op"):
    s = {
        "state": "awaiting_choice",
        "campaign_id": campaign_id,
        "patient_context": {
            "patient_name": "Casey Morgan",
            "patient_id": "PT-9",
            "patient_dob": "1970-01-02",
            "is_diabetic": True,
        },
        "retry_count": 0,
    }
    out, last = [], None
    for body in replies:
        d = conversation.decide(
            copy.deepcopy(s), body, NOW, site=SITE, policy=ConversationPolicy(survey_enabled=True)
        )
        out += [t for t in (d.reply, *d.extra_replies) if t]
        if d.session_patch:
            s.update(d.session_patch)
        if d.retry is not None:
            s["retry_count"] = d.retry
        if d.state:
            s["state"] = d.state
        last = d
    return out, s, last


def test_pre_op_by_text_asks_its_own_questions_and_triages_with_its_rules():
    c = load_campaign("pre_op", CONFIG)
    texts, s, last = _walk(
        [
            "3",
            "01/02/1970",
            "yes",
            "yes",
            "no",
            "not sure",
            "maybe",
            "yes",
            "a cough",
            "yes",
            "can I take my blood thinner?",
            "no",
        ]
    )
    assert texts[1] == (
        "Thank you, that's verified. I'll send 6 short questions, one at a time. "
        "Just reply to each one. Reply C at any point to stop."
    )
    assert texts[2].startswith("(1/6) Do you know what time to arrive")
    assert "Can you tell us a little about that?" in texts
    assert not any("pain" in t.lower() or "incision" in t.lower() for t in texts)
    assert last.effect is Effect.SUBMIT_SURVEY
    a = s["survey_answers"]
    assert a["ride_home"] == "no" and a["stop_meds_instructions"] == "unknown"
    assert a["unwell_description"] == "a cough" and a["patient_questions"]
    t = survey.triage(a, c)
    assert t.follow_up == "urgent_same_day"
    assert [f["text"] for f in t.findings][:2] == [
        "unwell before surgery",
        "unsure which medicines to stop",
    ]
    assert "no ride home arranged" in t.reason
    # The closing has no medication-list reminder (pre-op has no closing_extra).
    closing = messages.survey_done_urgent(asked_questions=True, campaign=c)
    assert "medications" not in closing and "Your care team will follow up" in closing


def test_pre_op_unwell_description_is_strict_by_text():
    c = load_campaign("pre_op", CONFIG)
    assert survey.strict_fields(c) == {"unwell_description"}
    assert survey.unknownable(c) == {
        "arrival_confirmed",
        "fasting_understood",
        "ride_home",
        "stop_meds_instructions",
        "feeling_unwell",
    }


def test_a_session_with_an_unknown_use_case_stops_without_asking_anything():
    texts, s, last = _walk(["3", "01/02/1970"], campaign_id="retired_use_case")
    assert texts[-1] == messages.survey_cut_short() and s["state"] == "closed"
    assert last.note == "survey-unknown-campaign"


def test_post_op_sessions_without_a_campaign_id_are_post_op():
    texts, _s, _ = _walk(["3", "01/02/1970"], campaign_id=None)
    assert texts[-1] == "(1/8) How are you feeling overall since your procedure?"  # diabetic


# --- reports: completeness from the spec ------------------------------------------------

PRE_OP_DONE = {
    "arrival_confirmed": "yes",
    "fasting_understood": "yes",
    "ride_home": "yes",
    "stop_meds_instructions": "yes",
    "feeling_unwell": "no",
    "has_questions": "no",
}


def _report(survey_responses, **kw):
    return {
        "patient_id": "PT-9",
        "follow_up": "none",
        "follow_up_reason": "x",
        "outcome": "questionnaire_completed",
        "survey_responses": survey_responses,
        **kw,
    }


def test_a_completed_pre_op_report_is_checked_against_pre_op():
    clean, errors = reports.validate_report(
        _report(PRE_OP_DONE, campaign_id="pre_op", spec_version=1)
    )
    assert errors == [] and clean["campaign_id"] == "pre_op" and clean["spec_version"] == 1
    # Against post-op, the same answers are incomplete (and vice versa).
    _, errors = reports.validate_report(_report(PRE_OP_DONE))
    assert "missing required survey_responses fields: overall_feeling" in errors[0]
    _, errors = reports.validate_report(_report({"overall_feeling": "ok"}, campaign_id="pre_op"))
    assert errors == [
        "outcome=questionnaire_completed missing required survey_responses fields: "
        "arrival_confirmed, fasting_understood, ride_home, stop_meds_instructions, "
        "feeling_unwell, has_questions. Ask each unanswered readiness question and re-submit."
    ]
    _, errors = reports.validate_report(
        _report({**PRE_OP_DONE, "feeling_unwell": "yes"}, campaign_id="pre_op")
    )
    assert errors == [
        "feeling_unwell=yes requires unwell_description. "
        "Ask the patient to tell you a little about it."
    ]


def test_report_campaign_fields_are_checked():
    _, errors = reports.validate_report(_report(PRE_OP_DONE, campaign_id="nope"))
    assert "'campaign_id' is not a known use case." in errors
    _, errors = reports.validate_report(_report(PRE_OP_DONE, campaign_id="pre_op", spec_version=0))
    assert "'spec_version' must be a positive integer." in errors
