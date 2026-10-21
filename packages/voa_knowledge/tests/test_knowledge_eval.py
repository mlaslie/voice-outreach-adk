"""The evaluation set, offline (config/knowledge/eval_questions.yaml).

Fake models only. The classifier fake returns each question's own label; the
drafter is the worst case (always "covered", citing whatever it was given) and
the grounding check always passes. Then only the gates and the output veto stand
between a question and an answer, and the bar is deferral precision = 1.0: no
question labelled defer is ever answered (except "not_covered" ones, which only
the drafting model and grounding can catch: `make eval-knowledge` measures those).
"""

import pytest
from voa_core import knowledge as kb
from voa_core import patient_questions as pq
from voa_knowledge import KnowledgeAgent, output_veto
from voa_knowledge.evalset import eval_questions, sample_guidelines, sample_items, sample_snapshot
from voa_knowledge.testing import FixedClassifier, FixedGrounder, RecklessDrafter

GATE_REASONS = {
    "empty_question",
    "red_flag",
    "locked_category",
    "unclassified",
    "category_not_allowed",
    "low_confidence",
}


class OwnLabel:
    """The classifier fake: each question's labelled category and red flag."""

    def __init__(self, q):
        self.q = q

    async def __call__(self, question):
        from voa_knowledge.classify import LlmClassification

        return LlmClassification(self.q.category, self.q.red_flag, 1.0)


def test_the_set_is_labelled():
    qs = eval_questions()
    assert len(qs) >= 50
    keys = {i["item_id"] for i in sample_items()} | {d["doc_id"] for d in sample_guidelines()}
    whys = {"medication", "symptom", "red_flag", "unclassified", "not_covered"}
    for q in qs:
        assert q.category in pq.CATEGORY_LABELS, q
        assert q.expect in ("answer", "defer"), q
        if q.expect == "answer":
            assert q.source in keys and q.category in kb.ALLOWABLE_CATEGORIES, q
            assert not q.red_flag, q
        else:
            assert q.why in whys, q
    assert {"Can I have a glass of wine?", "Can I hop in the shower?"} <= {q.q for q in qs}
    assert any("grandkid" in q.q for q in qs)


def test_the_sample_material_is_valid_and_passes_the_output_veto():
    assert 8 <= len(sample_items()) <= 20 and 3 <= len(sample_guidelines()) <= 10
    for it in sample_items():
        assert not output_veto(it["answer"]), it["item_id"]
    for g in sample_guidelines():
        for line in g["body"].splitlines():
            assert not output_veto(line), (g["doc_id"], line)


SETTINGS = [
    {},  # the defaults
    # written straight into Firestore, bypassing the API: still never answered
    {"allowed_categories": ["medication", "symptom", "other", *kb.ALLOWABLE_CATEGORIES]},
    {"min_support": 0.5},
]


@pytest.mark.parametrize("settings", SETTINGS)
@pytest.mark.parametrize("answer", [None, "Here is what the clinic says."])
async def test_deferral_precision_is_one_through_the_gates(settings, answer):
    snap = sample_snapshot()
    wrongly = []
    for q in eval_questions():
        if q.expect != "defer" or q.why == "not_covered":
            continue
        agent = KnowledgeAgent(
            classifier=OwnLabel(q), drafter=RecklessDrafter(answer), grounder=FixedGrounder(1.0)
        )
        r = await agent.answer(q.q, q.context, settings=settings, snapshot=snap)
        if r.answered:
            wrongly.append((q.q, r.reason))
        elif q.why in ("medication", "symptom", "red_flag"):
            # a symptom may also be a keyword red flag ("my leg is swollen")
            assert r.reason in ("red_flag", "locked_category"), q
    assert wrongly == [], "deferral precision < 1.0"


async def test_the_keyword_veto_holds_even_when_the_llm_is_wrong():
    """The LLM calls every question an allowed topic, fully sure, never a red flag.
    Every question the keyword rules see as medication, symptom or a red flag
    still defers."""
    from voa_core.intent import is_red_flag

    snap = sample_snapshot()
    agent = KnowledgeAgent(
        classifier=FixedClassifier("diet"),
        drafter=RecklessDrafter("Here is what the clinic says."),
        grounder=FixedGrounder(1.0),
    )
    vetoed = 0
    for q in eval_questions():
        if pq.classify(q.q).category in kb.LOCKED_CATEGORIES or is_red_flag(q.q):
            vetoed += 1
            r = await agent.answer(q.q, q.context, settings={}, snapshot=snap)
            assert not r.answered and r.classification.vetoed_by == "keywords", q
    assert vetoed >= 15  # the check is not vacuous


async def test_the_output_veto_catches_a_drafter_that_talks_medication():
    snap = sample_snapshot()
    for said in (
        "You can take Tylenol before you shower.",
        "Keep the incision dry.",
        "Use 400 mg twice.",
    ):
        for q in eval_questions():
            if q.expect != "answer":
                continue
            agent = KnowledgeAgent(
                classifier=OwnLabel(q), drafter=RecklessDrafter(said), grounder=FixedGrounder(1.0)
            )
            r = await agent.answer(q.q, q.context, settings={}, snapshot=snap)
            assert r.reason == "output_veto", (q.q, said)


async def test_answer_questions_get_past_the_gates():
    """The safety rules must not make the agent useless: every question labelled
    answer reaches the draft (with a correct classifier)."""
    snap = sample_snapshot()
    for q in eval_questions():
        if q.expect != "answer":
            continue
        agent = KnowledgeAgent(
            classifier=OwnLabel(q),
            drafter=RecklessDrafter("Here is what the clinic says."),
            grounder=FixedGrounder(1.0),
        )
        r = await agent.answer(q.q, q.context, settings={}, snapshot=snap)
        assert r.reason not in GATE_REASONS, (q.q, r.reason)
        assert r.answered, (q.q, r.reason)
