"""End-to-end CallSession against real Gemini Live (VOA_RUN_LIVE=1).

A simulated patient (Gemini-TTS, 8 kHz mu-law, real time) talks to the real
CallSession through the fake provider; data lands in an in-memory store.
"""

import asyncio

import pytest
from voa_core.config import load_config
from voa_core.store import MemoryStore
from voa_gateway.app import CALL_ID_RE, Gateway
from voa_gateway.persist import StoreCallSink
from voa_gateway.session import AmdPolicy, CallSession
from voa_providers.audio import Codec
from voa_providers.fake import FakeMediaSession

pytestmark = pytest.mark.live
CALL_ID = "local_" + "e" * 16
PATIENT = {
    "first_name": "Jordan",
    "last_name": "Rivera",
    "dob": "1950-03-15",
    "patient_id": "TEST-0001",
    "is_diabetic": False,
}


async def _run(amd_verdict: str | None, scenario: str = "happy"):
    from voice_spike.harness import SCENARIOS, Patient, PatientVoice

    cfg = load_config()
    store = MemoryStore()
    gw = Gateway(
        cfg.model_copy(update={"services": cfg.services.model_copy(update={"store": "memory"})}),
        store=store,
    )
    sink = StoreCallSink(
        store, recordings_bucket=None, project_id=cfg.gcp.project_id, call_id_pattern=CALL_ID_RE
    )
    await store.set("pending_calls", CALL_ID, PATIENT)
    await store.set(
        "calls", CALL_ID, {"call_sid": CALL_ID, "amd_requested": amd_verdict is not None}
    )
    media = FakeMediaSession(codec=Codec.MULAW_8K, call_id=CALL_ID)
    patient = Patient(media=media, voice=PatientVoice(cfg), scenario=SCENARIOS[scenario])
    session = CallSession(
        cfg=cfg,
        runner=gw.runner,
        media=media,
        sink=sink,
        amd=AmdPolicy(enabled=True, hold_max_s=8.0, vm_drop=True),
        voicemail_clip=gw.voicemail_clip,
        on_agent_turn_end=patient.on_agent_turn_end,
    )
    patient.bridge = session
    media.start()
    coros = [patient.feeder(), patient.conversation(), patient.barge_in()]
    tasks = [asyncio.create_task(c) for c in coros]
    if amd_verdict:

        async def verdict():
            await asyncio.sleep(1.5)
            await store.merge("calls", CALL_ID, {"answered_by": amd_verdict})

        tasks.append(asyncio.create_task(verdict()))
    try:
        await asyncio.wait_for(session.run(), 240)
    finally:
        for t in tasks:
            t.cancel()
    return store, session


async def test_happy_path_persists_report_transcript_and_events():
    store, session = await _run(None)
    call = await store.get("calls", CALL_ID)
    assert call["outcome"] == "questionnaire_completed" and call["call_status"] == "success"
    assert call["follow_up"] in {"routine_next_business_day", "none"}
    assert await store.get("pending_calls", CALL_ID) is None  # read-once
    turns = (await store.get("transcripts", CALL_ID))["turns"]
    assert any(t["role"] == "patient" for t in turns)
    assert not any("<" in t["text"] for t in turns)
    events = [e["event"] for e in (await store.get("call_events", CALL_ID))["timeline"]]
    assert "report.saved" in events and "drain.mark_sent" in events


async def test_machine_verdict_drops_voicemail_without_starting_agent():
    store, session = await _run("machine_end_beep")
    call = await store.get("calls", CALL_ID)
    assert call["outcome"] == "voicemail_left" and call["call_status"] == "left_vmail"
    events = [e["event"] for e in session.timeline.entries]
    assert (
        "vm.play" in events and "agent.release" not in events and "agent.audio_start" not in events
    )


@pytest.mark.parametrize(
    ("scenario", "outcome", "tier"),
    [
        ("dob_retry", "questionnaire_completed", None),
        ("barge_in", "questionnaire_completed", None),
        ("red_flag", "emergency_ended", "emergency"),
        ("wrong_person", "unable_to_reach_wrong_person", None),
    ],
)
async def test_personas_with_production_agent(scenario, outcome, tier):
    """Persona evals against the campaign-driven agent (not the spike prompt)."""
    store, session = await _run(None, scenario)
    call = await store.get("calls", CALL_ID)
    assert call.get("outcome") == outcome, [e["event"] for e in session.timeline.entries]
    if tier:
        assert call["follow_up"] == tier
    if scenario == "barge_in":
        assert any(e["event"] == "agent.interrupted" for e in session.timeline.entries)
    if outcome == "unable_to_reach_wrong_person":
        assert call.get("call_status") in (
            None,
            "",
        )  # a non-reaching report must not settle the call
