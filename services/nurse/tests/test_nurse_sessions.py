"""The DocStore-backed ADK session service: persistence across instances, bounded
history that never opens on a model turn or function response, and rollback."""

import json

from google.adk.events import Event, EventActions
from google.genai import types
from voa_core.store import MemoryStore
from voa_nurse.sessions import DocStoreSessionService, is_user_text, trim

APP, USER = "voa_nurse", "ge_conversation"


def U(text: str) -> Event:  # noqa: N802
    return Event(
        author="user",
        invocation_id="i",
        content=types.Content(role="user", parts=[types.Part(text=text)]),
    )


def M(text: str) -> Event:  # noqa: N802
    return Event(
        author="nurse_assistant",
        invocation_id="i",
        content=types.Content(role="model", parts=[types.Part(text=text)]),
    )


def FC() -> Event:  # noqa: N802
    return Event(
        author="nurse_assistant",
        invocation_id="i",
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(name="x", args={"a": [[1]]}))],
        ),
    )


def FR() -> Event:  # noqa: N802
    return Event(
        author="user",
        invocation_id="i",
        content=types.Content(
            role="user",
            parts=[types.Part.from_function_response(name="x", response={"result": {}})],
        ),
    )


def enc(e: Event) -> str:
    return e.model_dump_json(exclude_none=True)


async def test_a_session_survives_another_instance():
    store = MemoryStore()
    a, b = DocStoreSessionService(store), DocStoreSessionService(store)
    s = await a.create_session(
        app_name=APP, user_id=USER, session_id="conv-1", state={"text_mode": True}
    )
    await a.append_event(s, U("who needs me?"))
    await a.append_event(s, FC())
    await a.append_event(s, FR())
    await a.append_event(
        s,
        Event(
            author="nurse_assistant",
            invocation_id="i",
            actions=EventActions(state_delta={"seen": 1, "temp:x": 2}),
        ),
    )
    got = await b.get_session(app_name=APP, user_id=USER, session_id="conv-1")
    assert [e.id for e in got.events] == [e.id for e in s.events]
    assert got.state == {"text_mode": True, "seen": 1}  # temp: state is never persisted
    assert got.events[1].content.parts[0].function_call.args == {"a": [[1]]}


async def test_missing_session_is_none_and_list_works():
    svc = DocStoreSessionService(MemoryStore())
    assert await svc.get_session(app_name=APP, user_id=USER, session_id="nope") is None
    await svc.create_session(app_name=APP, user_id=USER, session_id="conv-1")
    assert [s.id for s in (await svc.list_sessions(app_name=APP)).sessions] == ["conv-1"]
    await svc.delete_session(app_name=APP, user_id=USER, session_id="conv-1")
    assert await svc.get_session(app_name=APP, user_id=USER, session_id="conv-1") is None


def test_trim_never_starts_on_a_model_turn_or_function_response():
    """Review A-3."""
    hist = [enc(U("who needs me?"))] + [enc(FC()), enc(FR())] * 30
    out = trim(hist, max_events=40)
    assert out and is_user_text(json.loads(out[0]))
    assert trim([enc(FC()), enc(FR())] * 30, max_events=40) == []
    hi = enc(U("hi"))
    assert trim([enc(M("orphan")), hi], max_events=40) == [hi]


def test_trim_cuts_at_the_earliest_user_text_that_fits():
    hist = [enc(U("one")), enc(M("a")), enc(U("two")), enc(M("b")), enc(U("three")), enc(M("c"))]
    out = trim(hist, max_events=4)
    assert [json.loads(e)["content"]["parts"][0]["text"] for e in out] == ["two", "b", "three", "c"]


async def test_history_is_bounded_in_the_store():
    store = MemoryStore()
    svc = DocStoreSessionService(store, max_events=10)
    s = await svc.create_session(app_name=APP, user_id=USER, session_id="conv-1")
    for i in range(20):
        await svc.append_event(s, U(f"q{i}"))
        await svc.append_event(s, M(f"a{i}"))
    got = await svc.get_session(app_name=APP, user_id=USER, session_id="conv-1")
    assert len(got.events) == 10 and got.events[0].content.parts[0].text == "q15"


async def test_rollback_drops_a_half_finished_turn():
    svc = DocStoreSessionService(MemoryStore())
    s = await svc.create_session(app_name=APP, user_id=USER, session_id="conv-1")
    await svc.append_event(s, U("earlier"))
    await svc.append_event(s, M("answer"))
    keep = {e.id for e in s.events}
    await svc.append_event(s, U("slow question"))
    await svc.append_event(s, FC())  # the function response never came
    assert await svc.rollback(app_name=APP, user_id=USER, session_id="conv-1", keep_ids=keep) == 2
    got = await svc.get_session(app_name=APP, user_id=USER, session_id="conv-1")
    assert {e.id for e in got.events} == keep and len(got.events) == 2


async def test_documents_carry_a_ttl_field():
    store = MemoryStore()
    svc = DocStoreSessionService(store)
    await svc.create_session(app_name=APP, user_id=USER, session_id="conv-1")
    ((_, doc),) = await store.query("nurse_sessions")
    assert doc["expire_at"].tzinfo is not None
