"""The knowledge agent: the classifier veto, the gates, assembly, draft, grounding,
the output veto and the time budget. Fakes only: nothing touches the network."""

import asyncio
import json

import httpx
import pytest
from voa_core import knowledge as kb
from voa_knowledge import KnowledgeAgent, output_veto
from voa_knowledge import classify as cl
from voa_knowledge.assemble import AllApprovedRetriever, guideline_material, split_text
from voa_knowledge.draft import Draft, DraftOut, build_prompt
from voa_knowledge.ground import Fact, VertexGroundingChecker, build_body, parse_response
from voa_knowledge.testing import (
    FixedClassifier,
    FixedGrounder,
    KeywordClassifier,
    OverlapDrafter,
    RecklessDrafter,
)

SHOWER = {
    "item_id": "i-shower",
    "question": "When can I shower?",
    "alt_questions": ["Is it okay to shower yet?"],
    "answer": "You can usually shower two days after surgery.",
    "category": "activity",
    "status": "approved",
    "version": 3,
}
LIFTING = {
    "doc_id": "d-lift",
    "title": "Lifting after outpatient knee arthroscopy",
    "body": "Lift nothing heavier than ten pounds for two weeks after the procedure.",
    "procedures": ["knee arthroscopy"],
    "care_settings": ["outpatient"],
    "status": "approved",
    "version": 2,
}
DRAFT_DOC = {**LIFTING, "doc_id": "d-draft", "status": "draft"}
SNAP = kb.KnowledgeSnapshot(examples=[SHOWER], guidelines=[LIFTING, DRAFT_DOC])
KNEE = {"procedure": "Left knee arthroscopy", "care_setting": "outpatient", "days_since_surgery": 4}


def agent(**kw):
    base = {
        "classifier": FixedClassifier("activity"),
        "drafter": OverlapDrafter(),
        "grounder": FixedGrounder(0.95),
    }
    return KnowledgeAgent(**{**base, **kw})


async def ask(question, a=None, *, settings=None, context=None, snapshot=SNAP):
    return await (a or agent()).answer(
        question, context or {}, settings=settings or {}, snapshot=snapshot
    )


# --------------------------------------------------------------------------- answered


async def test_an_example_is_answered_with_its_source_and_score():
    g = FixedGrounder(0.93)
    r = await ask("Is it okay to shower yet?", agent(grounder=g))
    assert (r.outcome, r.reason) == ("answered", "answered")
    assert r.answer == SHOWER["answer"]
    assert r.sources == (
        {"kind": "example", "id": "i-shower", "title": "When can I shower?", "version": 3},
    )
    assert r.support_score == 0.93 and r.latency_ms >= 0
    ((answer, facts),) = g.calls
    assert answer == SHOWER["answer"] and {f.source_id for f in facts} == {"ex:i-shower"}
    d = r.to_dict()
    assert set(d) == {
        "outcome",
        "reason",
        "classification",
        "answer",
        "sources",
        "support_score",
        "latency_ms",
    }
    assert d["classification"] == {
        "category": "activity",
        "category_label": "Activity",
        "llm_category": "activity",
        "keyword_category": "activity",
        "red_flag": False,
        "vetoed_by": None,
    }


async def test_a_guideline_that_applies_is_used_and_only_cited_sources_are_facts():
    g = FixedGrounder(0.9)
    r = await ask("Lifting after outpatient knee arthroscopy?", agent(grounder=g), context=KNEE)
    assert r.answered and r.sources[0]["kind"] == "guideline" and r.sources[0]["version"] == 2
    assert [f.source_id for f in g.calls[0][1]] == ["gl:d-lift"]


async def test_the_draft_sees_the_context_and_approved_applicable_material_only():
    d = OverlapDrafter()
    await ask("When can I shower?", agent(drafter=d), context=KNEE)
    ((_, ctx, refs),) = d.calls
    assert ctx == KNEE and refs == ["ex:i-shower", "gl:d-lift"]  # the draft doc is not there
    d = OverlapDrafter()
    await ask("When can I shower?", agent(drafter=d), context={"procedure": "hip replacement"})
    assert d.calls[0][2] == ["ex:i-shower"]  # the knee guideline does not apply


def test_the_prompt_never_holds_identifiers():
    m = guideline_material(LIFTING)
    p = build_prompt(
        "can I lift my grandkid",
        {**KNEE, "first_name": "Jordan", "dob": "1950-03-15", "phone": "+12025550143"},
        [m],
    )
    assert "Jordan" not in p and "1950" not in p and "555" not in p
    assert "[gl:d-lift]" in p and "knee arthroscopy" in p.lower()


# --------------------------------------------------------------------------- the veto table


@pytest.mark.parametrize(
    ("question", "llm", "reason", "vetoed_by"),
    [
        # keyword red flag or locked category: the LLM is never asked
        ("I have chest pain, can I shower?", "activity", "red_flag", "keywords"),
        ("How much Tylenol can I take?", "diet", "locked_category", "keywords"),
        ("Is my incision red?", "activity", "locked_category", "keywords"),
        # the LLM is stricter than the keywords: it wins
        (
            "Can I have a glass of wine with my blood pressure pill?",
            "medication",
            "locked_category",
            "keywords",
        ),
        ("Is it normal to feel like this?", "symptom", "locked_category", "llm"),
    ],
)
async def test_either_side_can_veto(question, llm, reason, vetoed_by):
    c = FixedClassifier(llm)
    r = await ask(question, agent(classifier=c))
    assert (r.outcome, r.reason, r.classification.vetoed_by) == ("deferred", reason, vetoed_by)
    assert (c.calls == []) == (vetoed_by == "keywords")


async def test_an_llm_red_flag_defers_and_is_immediate():
    r = await ask(
        "When can I shower?", agent(classifier=FixedClassifier("activity", red_flag=True))
    )
    assert (r.reason, r.classification.vetoed_by, r.priority) == (
        "red_flag",
        "llm",
        "urgent_same_day",
    )


async def test_the_keywords_never_loosen_a_decision():
    # The keywords call it activity; the LLM says it is unclear: it defers.
    r = await ask("When can I shower?", agent(classifier=FixedClassifier("other")))
    assert r.reason == "unclassified" and r.priority == "urgent_same_day"
    # The keywords cannot tell (other); the LLM understands it: answered.
    r = await ask(
        "Can I hop under the water yet?",
        agent(classifier=FixedClassifier("activity"), drafter=RecklessDrafter(SHOWER["answer"])),
    )
    assert r.answered and r.classification.keyword_category == "other"


@pytest.mark.parametrize("settings_cats", [["medication", "symptom", "other", "activity"]])
async def test_locked_categories_defer_whatever_the_settings_say(settings_cats):
    for llm in ("medication", "symptom"):
        r = await ask(
            "When can I shower?",
            agent(classifier=FixedClassifier(llm), drafter=RecklessDrafter()),
            settings={"allowed_categories": settings_cats},
        )
        assert r.reason == "locked_category"


async def test_low_confidence_and_not_allowed_defer():
    r = await ask(
        "When can I shower?", agent(classifier=FixedClassifier("activity", confidence=0.59))
    )
    assert r.reason == "low_confidence"
    r = await ask("When can I shower?", settings={"allowed_categories": ["diet"]})
    assert r.reason == "category_not_allowed"
    r = await ask("When can I shower?", agent(classifier=FixedClassifier("nonsense")))
    assert r.reason == "unclassified"


def test_priority_is_the_more_urgent_of_the_two():
    c = cl.Classification("diet", "other", "diet")
    assert c.priority == "urgent_same_day"
    c = cl.Classification("diet", "diet", "diet")
    assert c.priority == "routine_next_business_day"


# --------------------------------------------------------------------------- the later gates


async def test_no_knowledge_defers_without_a_draft():
    d = RecklessDrafter()
    r = await ask("When can I shower?", agent(drafter=d), snapshot=kb.KnowledgeSnapshot())
    assert r.reason == "no_knowledge" and d.calls == []


async def test_not_covered_defers():
    class Says:
        def __init__(self, draft):
            self.draft = draft

        async def __call__(self, *a):
            return self.draft

    for draft in (
        Draft(False, "Something", ("ex:i-shower",)),
        Draft(True, "Something", ()),
        Draft(True, "Something", ("ex:made-up", "gl:d-draft")),  # not in the material
        Draft(True, "  ", ("ex:i-shower",)),
    ):
        r = await ask("When can I shower?", agent(drafter=Says(draft)), context=KNEE)
        assert r.reason == "not_covered", draft


async def test_low_support_and_uncited_claims_defer():
    r = await ask("When can I shower?", agent(grounder=FixedGrounder(0.84)))
    assert r.reason == "low_support" and r.support_score == 0.84 and r.answer is None
    r = await ask(
        "When can I shower?", agent(grounder=FixedGrounder(0.8)), settings={"min_support": 0.8}
    )
    assert r.answered
    r = await ask("When can I shower?", agent(grounder=FixedGrounder(1.0, uncited=1)))
    assert r.reason == "low_support"


@pytest.mark.parametrize(
    "answer",
    [
        "Take two tablets of Tylenol before you shower.",
        "You can shower once the incision is dry.",
        "Use 500 mg of it twice.",
        "Take it three times a day.",
        "If you have chest pain, call 911.",
        "x" * 601,
    ],
)
async def test_the_output_veto(answer):
    assert output_veto(answer)
    r = await ask("When can I shower?", agent(drafter=RecklessDrafter(answer)))
    assert r.reason == "output_veto" and r.answer is None


def test_clinic_answers_pass_the_output_veto():
    for a in (SHOWER["answer"], "Park in the visitor garage on the north side.", LIFTING["body"]):
        assert not output_veto(a), a


async def test_timeouts_and_errors_defer():
    class Slow:
        async def __call__(self, *a):
            await asyncio.sleep(1)

    class Broken:
        async def __call__(self, *a):
            raise RuntimeError("down")

    for part in ("classifier", "drafter", "grounder"):
        r = await ask("When can I shower?", agent(**{part: Slow()}, budget_s=0.05))
        assert r.reason == "timeout", part
        r = await ask("When can I shower?", agent(**{part: Broken()}))
        assert r.reason == "error", part
    assert (await ask("   ")).reason == "empty_question"


async def test_the_store_is_read_when_no_snapshot_is_given():
    from voa_core.store import MemoryStore

    store = MemoryStore()
    await store.set(kb.ITEMS, "i-shower", SHOWER)
    kb.forget(store)
    r = await agent().answer("When can I shower?", {}, settings={}, store=store)
    assert r.answered


async def test_warm_reaches_every_part():
    warmed = []

    class W:
        def __init__(self, name):
            self.name = name

        async def warm(self):
            warmed.append(self.name)

    await KnowledgeAgent(classifier=W("c"), drafter=W("d"), grounder=W("g")).warm()
    assert sorted(warmed) == ["c", "d", "g"]


# --------------------------------------------------------------------------- assembly


def test_long_guidelines_split_into_facts_under_the_api_limit():
    body = "\n".join(["x" * 4000] * 6)
    parts = split_text(body, 10_000)
    assert all(len(p) <= 10_000 for p in parts) and "".join(parts).count("x") == 24_000
    assert split_text("y" * 25_000, 10_000) == ["y" * 10_000, "y" * 10_000, "y" * 5_000]


async def test_retriever_uses_applicability():
    r = AllApprovedRetriever()
    refs = lambda ms: [m.ref for m in ms]  # noqa: E731
    assert refs(await r.retrieve("q", KNEE, SNAP)) == ["ex:i-shower", "gl:d-lift"]
    inpatient = {**KNEE, "care_setting": "inpatient"}
    assert refs(await r.retrieve("q", inpatient, SNAP)) == ["ex:i-shower"]
    assert refs(await r.retrieve("q", {}, SNAP)) == ["ex:i-shower"]


# --------------------------------------------------------------------------- grounding REST


def test_grounding_body_and_parse():
    body = build_body("You can shower.", [Fact("Shower after two days.", "ex:i")], 0.6)
    assert body == {
        "answerCandidate": "You can shower.",
        "facts": [{"factText": "Shower after two days.", "attributes": {"source_id": "ex:i"}}],
        "groundingSpec": {"citationThreshold": 0.6},
    }
    g = parse_response(
        {
            "supportScore": 0.97,
            "claims": [
                {"claimText": "Here is what I found.", "groundingCheckRequired": False},
                {"claimText": "You can shower.", "citationIndices": [0]},
                {"claimText": "Then swim.", "citationIndices": []},
            ],
        }
    )
    assert (g.support_score, g.claims, g.uncited_claims) == (0.97, 2, 1)
    with pytest.raises(ValueError):
        parse_response({"claims": []})


class _Creds:
    valid, token = True, "tok"


async def test_vertex_grounding_checker_posts_the_documented_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["project"] = request.headers["x-goog-user-project"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"supportScore": 0.91, "claims": []})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    g = VertexGroundingChecker("demo-project", http=http, credentials=_Creds())
    out = await g("You can shower.", [Fact("Shower after two days.", "ex:i")])
    assert out.support_score == 0.91
    assert seen["url"] == (
        "https://discoveryengine.googleapis.com/v1/projects/demo-project/locations/global/"
        "groundingConfigs/default_grounding_config:check"
    )
    assert seen["auth"] == "Bearer tok" and seen["project"] == "demo-project"
    assert seen["body"]["facts"][0]["attributes"] == {"source_id": "ex:i"}

    bad = VertexGroundingChecker(
        "demo-project",
        http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403))),
        credentials=_Creds(),
    )
    with pytest.raises(RuntimeError):
        await bad("x", [])


# --------------------------------------------------------------------------- the ADK drafter


async def test_the_adk_drafter_runs_the_knowledge_agent_with_structured_output():
    from google.adk.models import BaseLlm, LlmResponse
    from google.genai import types
    from voa_knowledge.draft import AdkDrafter

    prompts = []

    class FakeLlm(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            prompts.append(llm_request)
            out = DraftOut(covered=True, answer="Two days.", source_ids=["ex:i-shower"])
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text=out.model_dump_json())])
            )

    d = AdkDrafter(model=FakeLlm(model="fake"))
    assert d.agent.name == "knowledge_agent" and d.agent.output_schema is DraftOut
    m = guideline_material(LIFTING)
    out = await d("can I lift my grandkid", KNEE, [m])
    assert out == Draft(True, "Two days.", ("ex:i-shower",))
    sent = json.dumps([c.model_dump(mode="json") for c in prompts[0].contents])
    assert "grandkid" in sent and "gl:d-lift" in sent
    assert "Use only the material" in str(prompts[0].config.system_instruction)
    # the session is deleted after each question
    assert await d._sessions.list_sessions(app_name="voa_knowledge") is not None


async def test_the_gemini_classifier_parses_structured_output():
    class Models:
        async def generate_content(self, **kw):
            self.kw = kw

            class R:
                parsed = cl.ClassifyOut(category="diet", red_flag=False, confidence=0.9)
                text = ""

            return R()

        async def get(self, model):
            return None

    class Client:
        def __init__(self):
            self.aio = type("A", (), {})()
            self.aio.models = Models()

    client = Client()
    c = cl.GeminiClassifier(model="gemini-test", client=client)
    out = await c("Can I have a glass of wine?")
    assert out == cl.LlmClassification("diet", False, 0.9)
    assert client.aio.models.kw["config"].response_schema is cl.ClassifyOut
    assert "Topics" in client.aio.models.kw["config"].system_instruction
    await c.warm()


async def test_keyword_classifier_fake():
    assert (await KeywordClassifier()("Where do I park?")).category == "other"
