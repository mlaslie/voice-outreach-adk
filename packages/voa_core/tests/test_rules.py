"""The triage rule language: a tiny hand-written parser, no eval."""

from pathlib import Path

import pytest
from voa_core import rules
from voa_core.campaign import Campaign, Question, Rule, load_campaign
from voa_core.parsers import parse_choice, parse_date, parse_number, parse_scale, parse_time
from voa_core.rules import RuleSyntaxError, compile_rule_when, evaluate

CONFIG = Path(__file__).parents[3] / "config"


def spec(rules_, questions=None, patient_fields=None) -> Campaign:
    qs = questions or [
        Question(id="fever", ask="Fever?", type="yes_no_unknown"),
        Question(id="pain", ask="Pain?", type="scale", min=0, max=10),
        Question(id="trend", ask="Trend?", type="choice", choices=["better", "worse"]),
        Question(id="notes", ask="Anything else?", type="text"),
        Question(id="weight", ask="Weight?", type="number", unit="kg"),
    ]
    return Campaign(
        id="t",
        name="t",
        scripts={},
        questions=qs,
        rules=[Rule(**r) for r in rules_],
        patient_fields=patient_fields or [{"id": "is_diabetic", "type": "bool"}],
    )


def tier(c, answers, patient=None):
    return evaluate(c, answers, patient).follow_up


def texts(c, answers, patient=None):
    return [f["text"] for f in evaluate(c, answers, patient).findings]


# --- the language -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "answers", "want"),
    [
        ("fever == yes", {"fever": "yes"}, True),
        ("fever == yes", {"fever": "Yes"}, False),  # exact, as the pre-engine code
        ("fever != no", {}, True),
        ("fever in [yes, unknown]", {"fever": "unknown"}, True),
        ("fever not in [yes, unknown]", {"fever": "no"}, True),
        ("pain >= 8", {"pain": 9}, True),
        ("pain >= 8", {"pain": "9"}, False),  # a scale answer counts only as a number
        ("pain >= 8", {"pain": 8.5}, False),  # ... a whole number
        ("pain > 3 and pain < 7", {"pain": 5}, True),
        ("not (pain > 3 and pain < 7)", {"pain": 5}, False),
        ("pain == 0 or fever == yes", {"pain": 0}, True),
        ("answered(pain)", {"pain": 0}, True),
        ("answered(notes)", {"notes": "   "}, False),
        ("not answered(notes)", {}, True),
        ("mentions(notes, [cough, 'short of breath'])", {"notes": "a dry cough"}, True),
        ("mentions(notes, [cough])", {"notes": "no cough at all"}, False),  # negated
        ("mentions(notes, ['short of breath'])", {"notes": "I am short of breath"}, True),
        ("weight > 100", {"weight": 120.5}, True),
        ("trend == 'worse'", {"trend": "worse"}, True),
        ("patient.is_diabetic", {}, False),
        ("patient.is_diabetic and fever == yes", {"fever": "yes"}, True),
    ],
)
def test_expressions(expr, answers, want):
    c = spec([{"when": expr, "tier": "urgent_same_day", "reason": "x"}])
    patient = {"is_diabetic": "true"} if "patient." in expr and "fever" in expr else {}
    assert (tier(c, answers, patient) == "urgent_same_day") is want


@pytest.mark.parametrize(
    ("expr", "problem"),
    [
        ("fevr == yes", "neither side refers to an answer"),
        ("fever ==", "expected a value"),
        ("fever == yes and", "expected a value"),
        ("(fever == yes", "expected ')'"),
        ("fever in yes", "expected '['"),
        ("__import__('os').system('x')", "cannot read"),  # no attribute access at all
        ("__import__(fever)", "unknown function '__import__'"),
        ("eval(fever)", "unknown function 'eval'"),
        ("fever == yes; 1", "cannot read '; 1'"),
        ("answered(nope)", "unknown question 'nope'"),
        ("check(no_such_check)", "unknown named check 'no_such_check'"),
        ("check(swelling_is_urgent)", "takes 1 argument"),
        ("patient.age > 3", "unknown patient field 'age'"),
        ("_ == yes", "only allowed in a rule with any_of"),
        ("x" * 600, "longer than"),
    ],
)
def test_bad_expressions_are_readable_errors(expr, problem):
    c = spec([])
    with pytest.raises(RuleSyntaxError) as e:
        compile_rule_when(c, expr)
    assert problem in str(e.value)


def test_named_checks_reasons_tiers_and_order():
    c = spec(
        [
            {"when": "fever == yes", "tier": "informational", "reason": "fever"},
            {"when": "pain >= 1", "tier": "routine_next_business_day", "reason": "pain {pain}/10"},
            {"check": "asks_for_a_person", "tier": "urgent_same_day", "reason": "asked"},
            {
                "any_of": ["fever", "trend"],
                "when": "_ == unknown",
                "tier": "routine_next_business_day",
                "reason": "unclear",
            },
        ]
    )
    t = evaluate(c, {"fever": "yes", "pain": 4, "notes": "please have a nurse call me"})
    # Most urgent first; rule order within a tier.
    assert [f["text"] for f in t.findings] == ["asked", "pain 4/10", "fever"]
    assert t.follow_up == "urgent_same_day" and t.triggers == ("notes",)
    assert (
        t.reason
        == "Patient reports asked. Also noted: pain 4/10 (next business day); fever (noted)."
    )
    u = evaluate(c, {"fever": "unknown", "trend": "unknown"})
    assert u.findings[0]["fields"] == ["fever", "trend"]
    assert evaluate(c, {}).reason == "No concerning symptoms reported."


def test_a_reason_is_cut_to_fit_a_report():
    c = spec([{"when": "answered(notes)", "tier": "urgent_same_day", "reason": "said: {notes}"}])
    (text,) = texts(c, {"notes": "x" * 500})
    assert len(text) == rules.MAX_FINDING_CHARS


def test_the_engine_never_evaluates_python():
    # A string that would be dangerous under eval is just a word that matches nothing.
    c = spec([{"when": "notes == '__import__(\"os\")'", "tier": "urgent_same_day", "reason": "x"}])
    assert tier(c, {"notes": "hello"}) == "none"


def test_post_op_rules_are_the_post_op_triage():
    # The parity suite proves equality over thousands of cases; this is the smoke test.
    c = load_campaign("post_op", CONFIG)
    assert texts(c, {"pain_present": "yes", "pain_severity": 9}) == ["pain 9/10"]
    assert texts(c, {"legs_swollen": "yes", "swelling_description": "both, no redness"}) == [
        "leg or foot swelling"
    ]


# --- generic parsers ----------------------------------------------------------------


def test_generic_parsers():
    assert parse_scale("7 out of 10") == 7
    assert parse_scale("4", 1, 5) == 4 and parse_scale("9", 1, 5) is None
    assert parse_scale("3 of 5", 1, 5) == 3
    assert parse_choice("Getting WORSE", ["better", "worse"]) == "worse"
    assert parse_choice("not worse", ["better", "worse"]) is None
    assert parse_choice("better or worse", ["better", "worse"]) is None
    assert parse_number("about 98.6 degrees") == 98.6
    assert parse_number("two") == 2 and parse_number("1 or 2") is None
    assert parse_number("500", hi=300) is None
    assert parse_date("10/21/2026") == "2026-10-21" and parse_date("2026-02-30") is None
    assert parse_time("3:30 pm") == "15:30" and parse_time("noon") == "12:00"
    assert parse_time("3") is None and parse_time("25:00") is None
