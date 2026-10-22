"""22 Oct: after the voicemail drop the agent's own report must not land.

On 20 Oct two calls were answered by a mailbox: AMD said machine_end_beep, the
gateway played the clip and filed voicemail_left, and the model, still running
behind the muted line, filed "unable_to_reach_wrong_person" from the greeting it
had heard. That report overwrote the outcome. Fictional data only.
"""

import asyncio
from types import SimpleNamespace

from voa_core.config import load_config
from voa_core.store import MemoryStore
from voa_gateway.app import CALL_ID_RE
from voa_gateway.persist import StoreCallSink
from voa_gateway.session import CallSession
from voa_providers.audio import Codec
from voa_providers.fake import FakeMediaSession

CALL_ID = "CA" + "c" * 32
AGENT_REPORT = {
    "outcome": "unable_to_reach_wrong_person",
    "follow_up": "none",
    "follow_up_reason": "unable to reach wrong person",
    "follow_up_reasons": [],
    "dob_verification_result": "skipped",
    "survey_responses": {},
}


def _session(store):
    async def get_session(**_):
        return SimpleNamespace(state={"report": AGENT_REPORT})

    runner = SimpleNamespace(
        app_name="voa_voice", session_service=SimpleNamespace(get_session=get_session)
    )
    cfg = load_config(
        environ={"VOA__GCP__PROJECT_ID": "test-project", "VOA__SERVICES__STORE": "memory"}
    )
    sink = StoreCallSink(
        store, recordings_bucket=None, project_id="test-project", call_id_pattern=CALL_ID_RE
    )
    s = CallSession(
        cfg=cfg,
        runner=runner,
        media=FakeMediaSession(codec=Codec.MULAW_8K, call_id=CALL_ID),
        sink=sink,
        record=False,
    )
    s.call_id = CALL_ID
    return s


def _tool_response(name, status):
    return SimpleNamespace(
        get_function_calls=lambda: [],
        get_function_responses=lambda: [SimpleNamespace(name=name, response={"status": status})],
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=False,
        content=None,
        error_code=None,
    )


async def _settle(s):
    while s._bg:
        await asyncio.gather(*list(s._bg), return_exceptions=True)


async def test_agent_report_after_the_drop_is_ignored_and_end_call_stays_muted():
    store = MemoryStore()
    # The doc as the drop left it (answered, AMD beep, the drop's own report).
    await store.set(
        "calls",
        CALL_ID,
        {
            "call_sid": CALL_ID,
            "answered_by": "machine_end_beep",
            "call_status": "left_vmail",
            "outcome": "voicemail_left",
        },
    )
    s = _session(store)
    s._phase = "voicemail"
    await s._handle(_tool_response("submit_report", "ok"))
    await _settle(s)
    call = await store.get("calls", CALL_ID)
    assert call["outcome"] == "voicemail_left" and call["call_status"] == "left_vmail"
    assert await store.query("reports") == []
    assert {"event": "report.ignored", "reason": "voicemail_dropped"}.items() <= next(
        e for e in s.timeline.entries if e["event"] == "report.ignored"
    ).items()
    await s._handle(_tool_response("end_call", "ending"))
    assert s._phase == "voicemail"  # the agent is never unmuted over the clip


async def test_agent_report_on_a_live_call_is_saved():
    store = MemoryStore()
    await store.set("calls", CALL_ID, {"call_sid": CALL_ID, "answered_at": "2026-10-20T17:45:39Z"})
    s = _session(store)
    s._phase = "live"
    await s._handle(_tool_response("submit_report", "ok"))
    await _settle(s)
    call = await store.get("calls", CALL_ID)
    assert call["outcome"] == "unable_to_reach_wrong_person"
    assert call.get("call_status") is None  # a non-reaching report settles nothing


async def test_a_spanish_call_drops_the_spanish_clip():
    """Step 4: a call spoken in Spanish asks for the clip in its language; an
    English call asks exactly as before (no language argument)."""
    asked = []

    async def clip(campaign_id, **kw):
        asked.append((campaign_id, kw))
        return None  # no clip: hang up (enough to see what was asked)

    for language in (None, "es-US"):
        s = _session(MemoryStore())
        s.voicemail_clip = clip
        s.call_language = language
        await s._voicemail_drop()
    assert asked == [("post_op", {}), ("post_op", {"language": "es-US"})]
