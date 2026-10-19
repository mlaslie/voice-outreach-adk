from pathlib import Path

from voa_core.campaign import load_campaign

CONFIG = Path(__file__).parents[3] / "config"


def test_post_op_applicability_and_missing():
    c = load_campaign("post_op", CONFIG)
    base = [q.id for q in c.applicable({"is_diabetic": False}, {})]
    assert base == [
        "overall_feeling",
        "pain_present",
        "fever_reported",
        "incision_concerns",
        "legs_swollen",
        "sleeping_well",
        "has_questions",
    ]
    diabetic = [q.id for q in c.applicable({"is_diabetic": "true"}, {"pain_present": "Yes"})]
    assert "blood_sugar_status" in diabetic and "pain_severity" in diabetic
    answers = {"overall_feeling": "ok", "pain_present": "yes", "pain_severity": "4"}
    assert c.missing({"is_diabetic": False}, answers) == [
        "pain_location",
        "pain_trend",
        "fever_reported",
        "incision_concerns",
        "legs_swollen",
        "sleeping_well",
        "has_questions",
    ]


def test_scripts_render_and_tiers():
    c = load_campaign("post_op", CONFIG)
    assert "Acme" in c.script(
        "opener", org_name="Acme", patient_first_name="J", patient_last_name="R"
    )
    assert c.follow_up_tiers[0] == "emergency" and "voicemail_left" in c.outcomes
