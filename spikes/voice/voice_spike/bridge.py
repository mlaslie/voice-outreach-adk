"""Bridge one telephony media session to an ADK ``run_live`` session.

This is the spike version of the gateway's core loop. It is provider-neutral:
it only talks to :class:`voa_providers.base.MediaSession`.

Behaviours carried over from the CES proxy's hard-won lessons:

* audio flows continuously in both directions, silence included;
* barge-in: on ``event.interrupted`` the provider's playout buffer is cleared;
* closing drain: after ``end_call`` we wait for the model's turn to finish,
  send a mark, and hang up only when the provider echoes it (otherwise the
  goodbye is cut off). Each drain uses its own mark name and its own Event;
* every step lands on a timeline (ms since call start) for latency analysis.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.genai import types
from voa_agents.voice import call_facts
from voa_core.config import AppConfig
from voa_core.logging import bind, safe_extra
from voa_providers.audio import (
    LIVE_INPUT_MIME,
    Codec,
    InboundTranscoder,
    OutboundTranscoder,
)
from voa_providers.base import Audio, MarkAck, MediaSession, StreamStart, StreamStop

log = logging.getLogger(__name__)

CONNECT_TRIGGER = (
    "[The phone call has just connected. The person who answered has not spoken yet. "
    "Begin now with your opener.]"
)
INPUT_BATCH_MS = 40  # Live recommends 20-100 ms chunks


@dataclass
class Timeline:
    t0: float = field(default_factory=time.monotonic)
    entries: list[dict[str, Any]] = field(default_factory=list)

    def add(self, event: str, **detail: Any) -> None:
        ms = round((time.monotonic() - self.t0) * 1000)
        self.entries.append({"ms": ms, "event": event, **detail})
        log.info(event, extra=safe_extra({"event": event, "t_ms": ms, **detail}))


def build_run_config(cfg: AppConfig) -> RunConfig:
    return RunConfig(
        response_modalities=["AUDIO"],
        session_resumption=types.SessionResumptionConfig()
        if cfg.voice.session_resumption
        else None,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        # Telephony: be a little more patient before deciding the caller finished.
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                silence_duration_ms=700,
            )
        ),
    )


class CallBridge:
    def __init__(
        self,
        *,
        cfg: AppConfig,
        runner: Runner,
        media: MediaSession,
        inbound_codec: Codec | None = None,
        user_id: str = "callee",
        patient: dict[str, str],
        drain_timeout_s: float = 30.0,
        on_agent_turn_end: Callable[[], None] | None = None,
    ):
        self.cfg = cfg
        self.runner = runner
        self.media = media
        self.user_id = user_id
        self.patient = patient
        self.drain_timeout_s = drain_timeout_s
        self.on_agent_turn_end = on_agent_turn_end
        self.timeline = Timeline()
        self.queue = LiveRequestQueue()
        self._in = InboundTranscoder(inbound_codec or media.codec)
        self._out = OutboundTranscoder(media.codec)
        self._mark_waiters: dict[str, asyncio.Event] = {}
        self._closing = False
        self._done = asyncio.Event()
        self.transcript: list[dict[str, str]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.session_id: str | None = None
        self.agent_speaking = False
        self._agent_audio_this_turn = False
        self._turn_done = asyncio.Event()
        self._pending_tools: set[str] = set()
        self._report_filed = False
        self._talked_during_tool = False
        self._bg: set[asyncio.Task] = set()  # strong refs so background tasks aren't GC'd

    # ------------------------------------------------------------------ public
    async def run(self, start_timeout_s: float = 30.0) -> dict[str, Any]:
        frames = self.media.frames()
        start = await asyncio.wait_for(self._await_start(frames), start_timeout_s)
        session = await self.runner.session_service.create_session(
            app_name=self.runner.app_name,
            user_id=self.user_id,
            state={"patient": self.patient, "answers": {}},
        )
        self.session_id = session.id
        self.facts = call_facts.register(session.id)
        with bind(call_id=start.call_id, session_id=session.id, provider=type(self.media).__name__):
            self.timeline.add("media.start")
            up = asyncio.create_task(self._upstream(frames), name="upstream")
            down = asyncio.create_task(self._downstream(), name="downstream")
            # Kick off the opener. Proactive audio is always on in 3.8, so we tell the
            # model explicitly that it is its turn to speak.
            self.queue.send_content(
                types.Content(role="user", parts=[types.Part(text=CONNECT_TRIGGER)])
            )
            self.timeline.add("agent.trigger_sent")
            watchdog = asyncio.create_task(self._watchdog(), name="watchdog")
            await self._done.wait()
            for task in (up, down, watchdog):
                task.cancel()
            await asyncio.gather(up, down, watchdog, return_exceptions=True)
            self.queue.close()
            await self.media.close()
            call_facts.unregister(session.id)
            self.timeline.add("call.ended")
            final = await self.runner.session_service.get_session(
                app_name=self.runner.app_name, user_id=self.user_id, session_id=session.id
            )
        state = dict(final.state) if final else {}
        return {
            "timeline": self.timeline.entries,
            "transcript": self.transcript,
            "tool_calls": self.tool_calls,
            "report": state.get("report"),
            "tool_audit": state.get("tool_audit", []),
        }

    # ------------------------------------------------------------------ internals
    async def _await_start(self, frames) -> StreamStart:
        async for frame in frames:
            if isinstance(frame, StreamStart):
                return frame
            if isinstance(frame, StreamStop):
                raise ConnectionError("stream stopped before start")
        raise ConnectionError("stream ended before start")

    async def _upstream(self, frames) -> None:
        buf = bytearray()
        batch_bytes = int(16 * INPUT_BATCH_MS * 2)  # PCM16 16 kHz
        try:
            async for frame in frames:
                if isinstance(frame, Audio):
                    buf += self._in(frame.data)
                    if len(buf) >= batch_bytes:
                        self.queue.send_realtime(
                            types.Blob(mime_type=LIVE_INPUT_MIME, data=bytes(buf))
                        )
                        buf.clear()
                elif isinstance(frame, MarkAck):
                    self.timeline.add("media.mark_ack", name=frame.name)
                    if waiter := self._mark_waiters.pop(frame.name, None):
                        waiter.set()
                elif isinstance(frame, StreamStop):
                    self.timeline.add("media.stop", reason=frame.reason)
                    break
        finally:
            self._done.set()

    async def _downstream(self) -> None:
        try:
            async for event in self.runner.run_live(
                user_id=self.user_id,
                session_id=self.session_id,
                live_request_queue=self.queue,
                run_config=build_run_config(self.cfg),
            ):
                await self._handle(event)
        except Exception:
            log.exception("run_live failed", extra={"event": "live.error"})
            self.timeline.add("live.error")
        finally:
            self._done.set()

    async def _handle(self, event) -> None:
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.inline_data and part.inline_data.data:
                    if self._pending_tools and not self._talked_during_tool:
                        # BLOCKING should prevent this; record it (once) if the model talks anyway.
                        self._talked_during_tool = True
                        self.timeline.add(
                            "agent.audio_while_tool_pending", tools=sorted(self._pending_tools)
                        )
                    if not self._agent_audio_this_turn:
                        self._agent_audio_this_turn = True
                        self.agent_speaking = True
                        self.timeline.add("agent.audio_start")
                    await self.media.send_audio(self._out(part.inline_data.data))
        for call in event.get_function_calls():
            self.tool_calls.append(
                {"name": call.name, "args_keys": sorted((call.args or {}).keys())}
            )
            self.timeline.add("tool.call", name=call.name)
            self._pending_tools.add(call.name)
            self._talked_during_tool = False
        for resp in event.get_function_responses():
            status = (resp.response or {}).get("status") or (resp.response or {}).get("result")
            self.timeline.add("tool.response", name=resp.name, status=status)
            self._pending_tools.discard(resp.name)
            if resp.name == "submit_report" and status == "ok":
                self._report_filed = True
            if resp.name == "end_call" and status == "ending" and not self._closing:
                self._closing = True
                self._spawn(self._finish_turn_then_drain())
        if (t := event.input_transcription) and t.finished and t.text:
            self.transcript.append({"role": "caller", "text": t.text.strip()})
            self.timeline.add("transcript.caller", chars=len(t.text))
            self.facts.caller_spoke()
        if (t := event.output_transcription) and t.text:
            if t.finished:
                self.transcript.append({"role": "agent", "text": t.text.strip()})
            else:
                self.facts.agent_said(t.text)
        if event.interrupted:
            self.timeline.add("agent.interrupted")
            await self.media.clear()
            self._end_agent_turn()
        if event.turn_complete:
            self.timeline.add("agent.turn_complete")
            self._end_agent_turn()
            self._turn_done.set()
            if self._report_filed and not self._closing:
                self._spawn(self._auto_hangup_after_report())
        if event.error_code:
            self.timeline.add("live.event_error", code=event.error_code)

    def _end_agent_turn(self) -> None:
        if self._agent_audio_this_turn and self.on_agent_turn_end:
            self.on_agent_turn_end()
        self._agent_audio_this_turn = False
        self.agent_speaking = False

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _auto_hangup_after_report(self, grace_s: float = 5.0) -> None:
        """Safety net (spike finding): the model sometimes says goodbye but never calls
        end_call. Once the report is filed and the agent's turn ends, hang up ourselves
        unless the caller starts talking or the model ends the call first."""
        turns = self.facts.caller_turns
        await asyncio.sleep(grace_s)
        if self._closing or self.facts.caller_turns != turns or self.agent_speaking:
            return
        self._closing = True
        self.timeline.add("hangup.auto_after_report")
        await self._drain_then_hangup()

    async def _finish_turn_then_drain(self, grace_s: float = 6.0) -> None:
        # end_call can arrive before, during or after the goodbye audio. Wait for the
        # model's current turn to complete (bounded), then drain what's queued.
        self._turn_done.clear()
        try:
            await asyncio.wait_for(self._turn_done.wait(), grace_s)
        except TimeoutError:
            self.timeline.add("drain.turn_wait_timeout")
        await self._drain_then_hangup()

    async def _drain_then_hangup(self) -> None:
        name = f"closing-{uuid.uuid4().hex[:8]}"  # unique per drain (never share a waiter)
        waiter = asyncio.Event()
        self._mark_waiters[name] = waiter
        await self.media.mark(name)
        self.timeline.add("drain.mark_sent", name=name)
        try:
            await asyncio.wait_for(waiter.wait(), self.drain_timeout_s)
        except TimeoutError:
            self.timeline.add("drain.timeout")
        self._done.set()

    async def _watchdog(self) -> None:
        await asyncio.sleep(self.cfg.voice.max_call_seconds)
        self.timeline.add("call.max_duration")
        self._done.set()
