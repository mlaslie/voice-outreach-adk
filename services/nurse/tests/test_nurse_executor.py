"""The A2A executor end to end in process: pre-routing, the confirm-only-by-id rule
(review C-5), stale-proposal clearing, text mode, the model path through a real
ADK Runner with a scripted model, turn deadlines and heartbeats, and sessions and
proposals that survive a second instance (two executors over one MemoryStore).
No network, no Gemini."""

import asyncio
import base64
import inspect

import pytest
from a2a.server.events import EventQueue
from a2a.types import (
    DataPart,
    FilePart,
    FileWithBytes,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
    TextPart,
)
from nurse_fixtures import (
    FakeBackend,
    RecordingQueue,
    ScriptedLlm,
    buttons,
    call_response,
    click,
    make_cfg,
    make_settings,
    reply_parts,
    reply_text,
    request,
    say,
    text_response,
)
from voa_core.store import MemoryStore
from voa_nurse.app import build_deps
from voa_nurse.executor import (
    APP_NAME,
    SESSION_USER,
    NurseExecutor,
    is_greeting,
    read_message,
)
from voa_nurse.identity import IdentityResolver
from voa_nurse.proposals import COLLECTION as PROPOSALS
from voa_nurse.sessions import COLLECTION as SESSIONS

PATIENT = dict(
    phone="+12025550101",
    patient_name="Jordan Rivera",
    patient_id="P-1001",
    patient_dob="1950-03-15",
)


class Instance:
    """One Cloud Run instance: its own executor, runner and session service, over a
    store and API shared with every other instance."""

    def __init__(self, store, backend, model=None, **settings):
        self.model = model or ScriptedLlm()
        self.deps = build_deps(
            make_cfg(), make_settings(**settings), store=store, backend=backend, model=self.model
        )
        self.executor = NurseExecutor(self.deps)

    async def send(self, ctx) -> RecordingQueue:
        q = RecordingQueue()
        await self.executor.execute(ctx, q)
        return q


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def backend():
    b = FakeBackend("nurse_day")
    b.state["allowlist"] = [{"phone": "+12025550101"}]
    return b


@pytest.fixture
def a(store, backend):
    return Instance(store, backend)


@pytest.fixture
def b(store, backend):
    return Instance(store, backend)


async def _propose(inst: Instance, context_id: str = "conv-1") -> str:
    """Propose a call through the model path (the model only ever proposes)."""
    inst.model.script = [
        call_response("propose_place_call", **PATIENT),
        text_response("Needs your OK."),
    ]
    q = await inst.send(say("call Jordan Rivera now", context_id=context_id))
    (confirm,) = [x for x in buttons(q) if x["event"] == "confirm"]
    return confirm["context"]["proposal_id"]


def test_enqueue_event_is_a_coroutine_function():
    """An un-awaited enqueue silently drops events; fakes must not hide that."""
    assert inspect.iscoroutinefunction(EventQueue.enqueue_event)


# --- streaming shape ----------------------------------------------------------------------


async def test_greeting_streams_submitted_working_one_artifact_completed(a):
    q = await a.send(say("hello"))
    kinds = [type(e).__name__ for e in q.events]
    assert kinds[:2] == ["TaskStatusUpdateEvent", "TaskStatusUpdateEvent"]
    assert [e.status.state for e in q.events[:2]] == [TaskState.submitted, TaskState.working]
    assert kinds[-2:] == ["TaskArtifactUpdateEvent", "TaskStatusUpdateEvent"]
    assert q.events[-1].status.state == TaskState.completed and q.events[-1].final
    assert not any(isinstance(e, Message) for e in q.events)
    parts = reply_parts(q)
    assert all(isinstance(p.root, DataPart) for p in parts)
    assert {p.root.metadata["mimeType"] for p in parts} == {"application/json+a2ui"}
    assert "Post-op Calling Agent (ADK)" in reply_text(q)
    assert a.model.requests == []  # deterministic: no model call


async def test_without_the_a2ui_extension_the_reply_is_text(a):
    q = await a.send(say("hello", a2ui=False))
    (part,) = reply_parts(q)
    assert isinstance(part.root, TextPart) and "[Needs you now" in part.root.text


async def test_heartbeats_while_slow_and_none_after_the_artifact(store, backend):
    slow = Instance(store, backend, model=ScriptedLlm(delay_s=0.7), heartbeat_s=0.2)
    slow.model.script = [text_response("Nobody right now.")]
    q = await slow.send(say("anything urgent?"))
    beats = [
        i
        for i, e in enumerate(q.events)
        if isinstance(e, TaskStatusUpdateEvent) and e.status.message is not None
    ]
    art = next(i for i, e in enumerate(q.events) if isinstance(e, TaskArtifactUpdateEvent))
    assert beats and max(beats) < art
    assert "Nobody right now." in reply_text(q)


# --- C-5: only an explicit confirmation by id runs anything ---------------------------------


@pytest.mark.parametrize("word", ["ok", "okay", "y", "yes", "confirm", "go ahead", "do it"])
async def test_a_bare_acknowledgement_never_executes(a, backend, word):
    pid = await _propose(a)
    q = await a.send(say(word))
    assert backend.writes == []
    assert f"confirm {pid}" in reply_text(q) and "Nothing was done" in reply_text(q)


async def test_a_bare_yes_in_text_mode_never_executes_either(a, backend):
    await a.send(say("text only"))
    a.model.script = [
        call_response("propose_place_call", **PATIENT),
        text_response("Needs your OK."),
    ]
    q = await a.send(say("call Jordan Rivera now"))
    text = reply_text(q)
    pid = text.split("reply: confirm ")[1].split()[0]
    for word in ("yes", "ok", "confirm"):
        await a.send(say(word))
    assert backend.writes == []
    q = await a.send(say(f"confirm {pid}"))
    assert len(backend.writes) == 1 and "placed" in reply_text(q)


async def test_typed_confirm_with_the_id_runs_once(a, backend):
    pid = await _propose(a)
    q = await a.send(say(f"Confirm {pid}"))
    assert [w[0] for w in backend.writes] == ["place_call"] and "placed" in reply_text(q)
    q = await a.send(say(f"confirm {pid}"))
    assert len(backend.writes) == 1 and "expired" in reply_text(q)


async def test_a_confirm_click_runs_once_even_on_another_instance(a, b, backend):
    pid = await _propose(a)
    q = await b.send(click("confirm", proposal_id=pid))
    assert len(backend.writes) == 1 and "placed" in reply_text(q)
    q = await a.send(click("confirm", proposal_id=pid))
    assert len(backend.writes) == 1 and "expired" in reply_text(q)


async def test_a_proposal_cannot_be_confirmed_from_another_conversation(a, backend):
    pid = await _propose(a, context_id="conv-1")
    await a.send(click("confirm", context_id="conv-2", proposal_id=pid))
    await a.send(say(f"confirm {pid}", context_id="conv-2"))
    assert backend.writes == []


async def test_the_model_never_confirms(a, backend):
    """Even a model that 'decides' the user agreed can only propose again."""
    await _propose(a)
    a.model.script = [call_response("propose_place_call", **PATIENT), text_response("Placed it!")]
    await a.send(say("yes please place it"))
    assert backend.writes == []


async def test_stale_proposals_are_cleared_at_the_top_of_every_turn(a, store):
    await store.set(PROPOSALS, "x" * 32 + "_deadbeef", {"conversation": "y", "used": True})
    pid = await _propose(a)
    key = next(i for i, d in await store.query(PROPOSALS) if i.endswith(pid))
    doc = await store.get(PROPOSALS, key)
    await store.set(PROPOSALS, key, {**doc, "expires": 0})  # it expired
    await a.send(say("hello"))
    assert await store.get(PROPOSALS, key) is None
    q = await a.send(click("confirm", proposal_id=pid))
    assert "expired" in reply_text(q)


async def test_dismiss_cancels(a, backend):
    pid = await _propose(a)
    q = await a.send(click("dismiss", proposal_id=pid))
    assert "Canceled" in reply_text(q)
    await a.send(click("confirm", proposal_id=pid))
    assert backend.writes == []


# --- sessions survive instances -----------------------------------------------------------


async def test_history_survives_a_second_instance(a, b, store):
    a.model.script = [
        call_response("needs_follow_up", group="now"),
        text_response("Three need you now."),
    ]
    q = await a.send(say("who needs me now?"))
    assert "Three need you now." in reply_text(q) and "Needs you now" in reply_text(q)
    b.model.script = [text_response("Dolores is emergent.")]
    q = await b.send(say("which one is worst?"))
    assert "Dolores is emergent." in reply_text(q)
    contents = b.model.requests[0].contents
    said = [p.text for c in contents for p in c.parts or [] if p.text]
    assert "who needs me now?" in said and "which one is worst?" in said
    assert any(p.function_response for c in contents for p in c.parts or [])
    assert len(await store.query(SESSIONS)) == 1


async def test_text_mode_survives_a_second_instance(a, b):
    await a.send(say("text only"))
    q = await b.send(say("hello"))
    (part,) = reply_parts(q)
    assert isinstance(part.root, TextPart)
    await b.send(say("cards"))
    q = await a.send(say("hello"))
    assert all(isinstance(p.root, DataPart) for p in reply_parts(q))


async def test_a_click_is_remembered_for_the_model(a, b, backend):
    sid = next(c for c in backend.state["calls"] if c["patient_name"] == "Marcus Ellery")["doc_id"]
    await a.send(click("view_call", call_sid=sid))
    b.model.script = [text_response("Marcus reports a fever.")]
    await b.send(say("does he have a fever?"))
    said = [p.text for c in b.model.requests[0].contents for p in c.parts or [] if p.text]
    assert any(s.startswith("[Opened a call") and sid in s for s in said)


# --- deadlines -----------------------------------------------------------------------------


async def test_a_slow_model_turn_gets_an_honest_card_and_is_rolled_back(store, backend):
    """Review A-5 (deadline) and A-3 (no half-finished turn left in the history)."""
    slow = Instance(store, backend, model=ScriptedLlm(delay_s=5), turn_budget_s=1, heartbeat_s=0.3)
    slow.model.script = [text_response("too late")]
    q = await slow.send(say("who needs me?"))
    text = reply_text(q)
    assert "Still working on that" in text and "Nothing was changed" in text
    session = await slow.deps.sessions.get_session(
        app_name=APP_NAME, user_id=SESSION_USER, session_id="conv-1"
    )
    assert session.events == []


async def test_a_model_error_is_rolled_back_too(store, backend):
    class Boom(ScriptedLlm):
        async def generate_content_async(self, llm_request, stream=False):
            raise RuntimeError("model unavailable")
            yield  # pragma: no cover

    inst = Instance(store, backend, model=Boom())
    q = await inst.send(say("who needs me?"))
    assert "Unable to answer" in reply_text(q)
    session = await inst.deps.sessions.get_session(
        app_name=APP_NAME, user_id=SESSION_USER, session_id="conv-1"
    )
    assert session.events == []


# --- pre-routing ------------------------------------------------------------------------------


async def test_unknown_or_incomplete_buttons_get_an_error_card(a):
    q = await a.send(click("launch_missiles"))
    assert "Unknown button" in reply_text(q)
    q = await a.send(click("propose_redial"))  # no call_sid
    assert "Unable to complete request" in reply_text(q)


async def test_csv_attachment_proposes_and_never_adds(a, backend):
    raw = (
        b"phone,patient_name,patient_id,patient_dob\n+12025550120,Casey Morgan,P-3001,1961-02-03\n"
    )
    ctx = request(
        Part(root=TextPart(text="add these")),
        Part(root=TextPart(text="\n<start_of_user_uploaded_file: list.csv>")),
        Part(
            root=FilePart(
                file=FileWithBytes(
                    bytes=base64.b64encode(raw).decode(), name="list.csv", mime_type="text/csv"
                )
            )
        ),
        Part(root=TextPart(text="<end_of_user_uploaded_file: list.csv>\n")),
    )
    q = await a.send(ctx)
    assert "Add 1 patient to the outreach worklist from list.csv?" in reply_text(q)
    assert backend.writes == []


async def test_allowed_emails_gate_every_turn_including_reads(store, backend):
    """Review A-4: the allowlist is not just for confirmations."""
    inst = Instance(store, backend, allowed_emails="rn.lee@example.org")
    q = await inst.send(say("who needs me now?"))
    assert "Not authorized" in reply_text(q) and inst.model.requests == []


async def test_identity_reaches_the_api_as_acting_user(store, backend):
    class Fixed(IdentityResolver):
        async def resolve(self, headers):
            return "rn.lee@example.org" if headers.get("authorization") else None

    inst = Instance(store, backend)
    inst.deps.identity = Fixed("client")
    seen = []
    orig = backend.place_call

    async def place_call(body):
        from voa_nurse import turn

        seen.append(turn.current().user)
        return await orig(body)

    backend.place_call = place_call
    pid = await _propose(inst)
    await inst.send(
        request(
            Part(
                root=DataPart(data={"action": {"name": "confirm", "context": {"proposal_id": pid}}})
            ),
            headers={"authorization": "Bearer ya29.x"},
        )
    )
    assert seen == ["rn.lee@example.org"]


async def test_ui_check_is_off_unless_enabled(store, backend):
    off = Instance(store, backend)
    off.model.script = [text_response("I can't do that.")]
    q = await off.send(say("ui check"))
    assert "A2UI component check" not in reply_text(q)
    on = Instance(store, backend, enable_ui_check=True)
    q = await on.send(say("ui check"))
    assert "A2UI component check" in reply_text(q)


def test_click_drops_the_user_action_text():
    msg = Message(
        role=Role.user,
        message_id="m",
        parts=[
            Part(root=TextPart(text="User action triggered.")),
            Part(
                root=DataPart(
                    data={
                        "action": {
                            "name": "view_call",
                            "context": {"call_sid": "CA1"},
                            "surfaceId": "s",
                            "sourceComponentId": "c",
                        }
                    }
                )
            ),
        ],
    )
    action, texts, files = read_message(msg)
    assert action == {"name": "view_call", "context": {"call_sid": "CA1"}} and texts == []


def test_v08_click_context_list_is_normalised():
    msg = Message(
        role=Role.user,
        message_id="m",
        parts=[
            Part(
                root=DataPart(
                    data={
                        "userAction": {
                            "name": "confirm",
                            "context": [{"key": "proposal_id", "value": {"literalString": "abc"}}],
                        }
                    }
                )
            )
        ],
    )
    action, _, _ = read_message(msg)
    assert action == {"name": "confirm", "context": {"proposal_id": "abc"}}


def test_duplicate_text_parts_are_collapsed():
    msg = Message(
        role=Role.user,
        message_id="m",
        parts=[Part(root=TextPart(text="hi")), Part(root=TextPart(text="hi"))],
    )
    assert read_message(msg)[1] == ["hi"]


@pytest.mark.parametrize(
    "msg",
    [
        "hello",
        "Hey",
        "hi there!",
        "Good morning",
        "hey team",
        "what can you do?",
        "Help",
        "main menu",
        "back to the menu",
        "hey, what can you do?",
        "Hello! How can you help me?",
    ],
)
def test_greetings_get_the_home_card(msg):
    assert is_greeting(msg)


@pytest.mark.parametrize(
    "msg",
    [
        "hey can you call Jordan Rivera",
        "hello, who needs a callback today",
        "help me place a call to Casey",
        "show the call list",
        "hi, show Jordan Rivera",
    ],
)
def test_real_requests_go_to_the_model(msg):
    assert not is_greeting(msg)


async def test_every_reply_ends_with_a_way_home(a, backend):
    sid = backend.state["calls"][0]["doc_id"]
    q = await a.send(click("view_call", call_sid=sid))
    assert buttons(q)[-1]["event"] == "welcome"
    q = await a.send(say("hello"))
    assert all(x["event"] != "welcome" for x in buttons(q))


async def test_concurrent_conversations_do_not_mix(store, backend):
    inst = Instance(store, backend)
    q1, q2 = await asyncio.gather(
        inst.send(say("text only", context_id="conv-a")),
        inst.send(say("hello", context_id="conv-b")),
    )
    assert isinstance(reply_parts(q1)[0].root, TextPart)  # conv-a switched to text
    assert all(isinstance(p.root, DataPart) for p in reply_parts(q2))
    q = await inst.send(say("hello", context_id="conv-b"))
    assert all(isinstance(p.root, DataPart) for p in reply_parts(q))
    q = await inst.send(say("hello", context_id="conv-a"))
    assert isinstance(reply_parts(q)[0].root, TextPart)
