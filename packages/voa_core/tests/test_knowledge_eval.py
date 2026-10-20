"""The knowledge-base question test set (config/knowledge/eval_questions.yaml).

Runs every question through the real gate (``knowledge.answer``) against the
fictional sample Q&A, all approved, with the offline keyword matcher. The safety
bar is deferral precision = 1.0: no question labelled "defer" is ever answered,
whatever the matcher would pick. Coverage of the "answer" questions is reported
and has a floor so the check is not vacuous.
"""

from pathlib import Path

import pytest
import yaml
from voa_core import knowledge as kb
from voa_core import patient_questions as pq

ROOT = Path(__file__).resolve().parents[3]
SAMPLE = ROOT / "config/knowledge/sample_items.yaml"
EVAL = ROOT / "config/knowledge/eval_questions.yaml"


def sample_items():
    raw = yaml.safe_load(SAMPLE.read_text())["items"]
    return [
        {
            **kb.clean_fields(it),
            "item_id": it["key"],
            "status": kb.APPROVED,
            "version": 1,
        }
        for it in raw
    ]


def eval_questions():
    return yaml.safe_load(EVAL.read_text())["questions"]


def test_the_sample_items_are_valid_and_in_their_questions_category():
    raw = yaml.safe_load(SAMPLE.read_text())["items"]
    assert 8 <= len(raw) <= 20
    for it in raw:
        clean = kb.clean_fields(it)  # limits, allowed category and the PHI check
        assert pq.classify(clean["question"]).category == it["category"], it["key"]


def test_the_set_is_labelled_and_answer_questions_are_in_allowed_categories():
    qs = eval_questions()
    assert len(qs) >= 30
    keys = {it["item_id"] for it in sample_items()}
    for q in qs:
        assert q["expect"] in ("answer", "defer"), q
        if q["expect"] == "answer":
            assert pq.classify(q["q"]).category in kb.ALLOWABLE_CATEGORIES, q
            assert q["item"] in keys, q


@pytest.mark.parametrize(
    "settings",
    [
        {},  # the defaults
        # written straight into Firestore, bypassing the API: still never answered
        {"allowed_categories": ["medication", "symptom", "other", *kb.ALLOWABLE_CATEGORIES]},
        {"min_match": 0.5},
    ],
)
async def test_deferral_precision_is_one(settings):
    items = sample_items()
    answered_wrongly, answered_right, wrong_item = [], 0, []
    labelled_answer = 0
    for q in eval_questions():
        r = await kb.answer(q["q"], items=items, settings=settings, matcher=kb.keyword_matcher)
        if q["expect"] == "defer":
            if r.answered:
                answered_wrongly.append((q["q"], r.item_id))
            continue
        labelled_answer += 1
        if r.answered:
            if r.item_id == q["item"]:
                answered_right += 1
            else:
                wrong_item.append((q["q"], r.item_id))
    assert answered_wrongly == [], "deferral precision < 1.0"
    assert wrong_item == []
    coverage = answered_right / labelled_answer
    assert coverage >= 0.8, f"answer coverage {coverage:.2f}"


async def test_a_matcher_that_always_picks_something_still_never_answers_a_defer_question():
    """Worst case for the gate: the model picks the first candidate, fully sure.
    Only "defer" questions in allowed categories that no item covers may then be
    answered; everything locked, red-flagged or unclassified must still defer."""
    items = sample_items()

    async def reckless(question, candidates):
        return kb.Match(candidates[0].item_id, 1.0)

    for q in eval_questions():
        if q["expect"] != "defer":
            continue
        r = await kb.answer(q["q"], items=items, settings={}, matcher=reckless)
        if r.category in kb.NEVER_ANSWERED or r.reason == kb.R_RED_FLAG:
            assert not r.answered, q
