"""One phone call: provider media session <-> ADK ``run_live``.

Production version of the phase-0 spike bridge (``spikes/voice``), plus:

* **AMD hold**: when the call was placed with answering-machine detection, the
  agent is not started until the verdict arrives (or ``hold_max_s`` passes).
  Meanwhile silence is streamed to Live (continuous audio) and the caller's audio
  is held back, so the model never hears a voicemail greeting.
* **Voicemail drop**: a machine verdict plays a pre-rendered, name-free clip,
  drains it, files ``voicemail_left`` and hangs up. A late verdict after release
  still drops if the caller has not spoken.
* **Persistence**: the report is saved the moment ``submit_report`` succeeds;
  transcript, timeline and a stereo recording are saved at the end.
* The spike's guards: exit gate (via ``CallFacts``) and auto-hangup after report.

Everything provider-specific stays behind :class:`voa_providers.base.MediaSession`;
everything storage-specific behind :class:`CallSink`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.genai import types
from voa_agents.voice import call_facts
from voa_core.config import AppConfig
from voa_core.logging import bind, safe_extra
from voa_providers.audio import (
    LIVE_INPUT_MIME,
    LIVE_OUTPUT_RATE,
    Codec,
    InboundTranscoder,
    OutboundTranscoder,
)
from voa_providers.base import Audio, MarkAck, MediaSession, StreamStart, StreamStop

from .recorder import StereoRecorder

log = logging.getLogger(__name__)

CONNECT_TRIGGER = (
    "[The phone call has just connected. The person who answered has not spoken yet. "
    "Begin now with your opener.]"
)
INPUT_BATCH_MS = 40
_MARKUP = re.compile(r"<[^>]{1,20}>|\{[a-z_]{1,20}\}")  # e.g. "<no speech>{pause}" (spike finding)


class CallSink(Protocol):
    """Where a call's data goes. Implemented over Firestore/GCS in ``persist.py``."""

    async def load_patient(self, call_id: str) -> dict[str, Any] | None: ...
    async def call_doc(self, call_id: str) -> dict[str, Any] | None: ...
    def watch_call(self, call_id: str) -> AsyncIterator[dict[str, Any] | None]: ...
    async def event(self, call_id: str, kind: str, **fields: Any) -> None: ...
    async def mark_amd_released(self, call_id: str) -> None: ...
    async def save_report(self, call_id: str, report: dict[str, Any]) -> None: ...
    async def finish(
        self,
        call_id: str,
        *,
        transcript: list[dict[str, str]],
        timeline: list[dict[str, Any]],
        recording_wav: bytes | None,
    ) -> None: ...


@dataclass
class AmdPolicy:
    enabled: bool = True
    hold_max_s: float = 4.0  # release the agent if no verdict by then
    late_window_s: float = 65.0  # keep watching for a late machine verdict
    vm_drop: bool = True
    vm_drop_verdicts: frozenset[str] = frozenset({"machine_end_beep"})
    hangup_verdicts: frozenset[str] = frozenset({"fax"})
    poll_interval_s: float = 0.4
    # "speak_first": the agent starts at once while AMD keeps running; a machine
    # verdict (DetectMessageEnd fires at the beep, and nothing before the beep is
    # recorded) clears the agent and drops the clip. Cuts ~4 s of silence after
    # pick-up. "hold": the agent waits for the verdict (the original F8 design).
    strategy: str = "speak_first"


@dataclass
class Timeline:
    t0: float = field(default_factory=time.monotonic)
    entries: list[dict[str, Any]] = field(default_factory=list)

    def add(self, event: str, **detail: Any) -> None:
        ms = round((time.monotonic() - self.t0) * 1000)
        self.entries.append({"ms": ms, "event": event, **detail})
        log.info(event, extra=safe_extra({"event": event, "t_ms": ms, **detail}))


def clean_transcript(text: str, latin_only: bool = False) -> str:
    """Strip transcription markup; for Latin-script languages also drop stray letters
    from other scripts (a real call produced "date of birth?িতার"). Accented Latin
    letters, digits and punctuation are kept."""
    text = _MARKUP.sub(" ", text)
    if latin_only:
        text = "".join(
            ch
            for ch in text
            if not unicodedata.category(ch).startswith(("L", "M"))
            or unicodedata.name(ch, "").startswith("LATIN")
            or 0x300 <= ord(ch) <= 0x36F  # combining accents used by Latin letters
        )
    return " ".join(text.split())


def run_config(cfg: AppConfig, vocabulary: list[str]) -> RunConfig:
    return RunConfig(
        response_modalities=["AUDIO"],
        session_resumption=types.SessionResumptionConfig()
        if cfg.voice.session_resumption
        else None,
        # Domain terms (procedures, drug names) bias caller transcription (gemini-3.8-live).
        input_audio_transcription=types.AudioTranscriptionConfig(
            custom_vocabulary=vocabulary or None
        ),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(silence_duration_ms=700)
        ),
    )


class CallSession:
    def __init__(
        self,
        *,
        cfg: AppConfig,
        runner: Runner,
        media: MediaSession,
        sink: CallSink,
        inbound_codec: Codec | None = None,
        amd: AmdPolicy | None = None,
        voicemail_clip: Callable[[], Awaitable[bytes | None]] | None = None,
        campaign_id: str = "post_op",
        patient_override: dict[str, Any] | None = None,
        drain_timeout_s: float = 30.0,
        record: bool = True,
        on_agent_turn_end: Callable[[], None] | None = None,
    ):
        self.cfg, self.runner, self.media, self.sink = cfg, runner, media, sink
        self.amd = amd or AmdPolicy(enabled=False)
        self.voicemail_clip = voicemail_clip
        self.campaign_id = campaign_id
        self.patient_override = patient_override
        self.drain_timeout_s = drain_timeout_s
        self.on_agent_turn_end = on_agent_turn_end
        self._latin_only = cfg.voice.language_code.lower().startswith("en")
        in_codec = inbound_codec or media.codec
        self.timeline = Timeline()
        self.queue = LiveRequestQueue()
        self._in = InboundTranscoder(in_codec)
        self._out = OutboundTranscoder(media.codec)
        self.recorder = StereoRecorder(16_000, LIVE_OUTPUT_RATE) if record else None
        self.transcript: list[dict[str, str]] = []
        self.call_id = ""
        self.session_id = ""
        self.facts = call_facts.CallFacts()
        self._phase = "hold"  # hold -> live -> closing ; or -> voicemail
        self._mark_waiters: dict[str, asyncio.Event] = {}
        self._done = asyncio.Event()
        self._turn_done = asyncio.Event()
        self._agent_audio_this_turn = False
        self.agent_speaking = False
        self._pending_tools: set[str] = set()
        self._report_filed = False
        self._bg: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ lifecycle
    async def run(self, start_timeout_s: float = 30.0) -> dict[str, Any]:
        frames = self.media.frames()
        start = await asyncio.wait_for(self._await_start(frames), start_timeout_s)
        self.call_id = start.call_id
        with bind(
            call_id=self.call_id, provider=type(self.media).__name__, campaign=self.campaign_id
        ):
            self.timeline.add("media.start")
            patient = self.patient_override or await self.sink.load_patient(self.call_id) or {}
            if self.patient_override and hasattr(self.sink, "remember_patient"):
                self.sink.remember_patient(self.call_id, self.patient_override)
            if not patient:
                self.timeline.add("call.no_patient_context")
            from voa_core import organization

            store = getattr(self.sink, "store", None)
            org = (
                await organization.org_name(store, self.cfg.site.org_name)
                if store is not None
                else self.cfg.site.org_name
            )
            session = await self.runner.session_service.create_session(
                app_name=self.runner.app_name,
                user_id=self.call_id,
                state={
                    "patient": patient,
                    "campaign_id": self.campaign_id,
                    "answers": {},
                    "org_name": org,  # the admin console's company name (or config)
                },
            )
            self.session_id = session.id
            self.facts = call_facts.register(session.id)
            with bind(session_id=session.id):
                await self._run_bound(frames)
        return {
            "call_id": self.call_id,
            "timeline": self.timeline.entries,
            "transcript": self.transcript,
        }

    async def _run_bound(self, frames) -> None:
        doc = await self.sink.call_doc(self.call_id) or {}
        amd = self.amd.enabled and bool(doc.get("amd_requested"))
        hold = amd and self.amd.strategy == "hold"
        self._phase = "hold" if hold else "live"
        self.facts.amd_pending = amd
        tasks = [
            asyncio.create_task(self._upstream(frames), name="upstream"),
            asyncio.create_task(self._downstream(), name="downstream"),
            asyncio.create_task(self._watchdog(), name="watchdog"),
        ]
        if amd:
            tasks.append(asyncio.create_task(self._amd_watch(), name="amd"))
        if not hold:
            self._release("speak_first" if amd else "no_amd")
        try:
            await self._done.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.queue.close()
            await self.media.close()
            call_facts.unregister(self.session_id)
            self.timeline.add("call.ended")
            await self._persist_final()

    async def _persist_final(self) -> None:
        try:
            await self.sink.finish(
                self.call_id,
                transcript=self.transcript,
                timeline=self.timeline.entries,
                recording_wav=self.recorder.wav()
                if self.recorder and self.recorder.seconds > 0.5
                else None,
            )
        except Exception:
            log.exception("persist failed", extra={"event": "persist.error"})

    # ------------------------------------------------------------------ AMD
    def _release(self, reason: str) -> None:
        if self._phase != "hold" and reason not in ("no_amd", "speak_first"):
            return
        self._phase = "live"
        self.timeline.add("agent.release", reason=reason)
        self.queue.send_content(
            types.Content(role="user", parts=[types.Part(text=CONNECT_TRIGGER)])
        )

    async def _amd_watch(self) -> None:
        """Hold the agent until the AMD verdict (or ``hold_max_s``), then keep watching
        for a late machine verdict until ``late_window_s``. Decisions come from
        ``lifecycle.verdict_action`` so voice and the status webhook agree.

        Polls the call doc (the verdict is *state* written by whichever instance got
        the AMD webhook). A Firestore listener was used first; on the first real call
        its stream was cancelled by a timeout wrapper / slow to connect and the
        verdict was missed, so the agent started on the hold timeout instead.
        """
        from voa_core import lifecycle

        release_at = time.monotonic() + self.amd.hold_max_s
        watch_until = time.monotonic() + self.amd.late_window_s
        while time.monotonic() < watch_until and not self._done.is_set():
            try:
                doc = await self.sink.call_doc(self.call_id)
            except Exception:
                log.warning("amd poll failed", extra={"event": "amd.poll_error"})
                doc = None
            verdict = (doc or {}).get("answered_by")
            action = lifecycle.verdict_action(verdict, self.amd.vm_drop, self.amd.vm_drop_verdicts)
            if action is lifecycle.VerdictAction.HOLD:
                if self._phase == "hold" and time.monotonic() >= release_at:
                    self._release("amd_timeout")
                await asyncio.sleep(self.amd.poll_interval_s)
                continue
            self.timeline.add("amd.verdict", verdict=verdict, action=action, phase=self._phase)
            self.facts.amd_pending = False
            if action is lifecycle.VerdictAction.HANGUP:
                self._phase = "voicemail"
                self._done.set()
            elif action is lifecycle.VerdictAction.DROP:
                # A verified identity proves a person; otherwise trust the detector
                # (speak-first expects the agent to have talked over a greeting).
                drop = self._phase == "hold" or (
                    not self.facts.identity_verified
                    and (self.amd.strategy == "speak_first" or self.facts.caller_turns == 0)
                )
                if drop:
                    await self._voicemail_drop()
                else:
                    self.timeline.add("amd.late_machine_ignored", reason="person_confirmed")
            else:
                if lifecycle.release_marks_amd_released(verdict):
                    await self.sink.mark_amd_released(self.call_id)
                self._release(f"amd_{verdict}")
            return
        self.facts.amd_pending = False  # watch window over without a verdict

    async def _voicemail_drop(self) -> None:
        self._phase = "voicemail"
        clip = await self.voicemail_clip() if self.voicemail_clip else None
        await self.media.clear()
        if self.recorder:
            self.recorder.clear()
        if not clip:
            self.timeline.add("vm.no_clip_hangup")
            self._done.set()
            return
        self.timeline.add("vm.play", seconds=round(len(clip) / 8000, 1))
        await self.media.send_audio(
            clip if self.media.codec is Codec.MULAW_8K else self._ulaw_to_native(clip)
        )
        await self.sink.save_report(
            self.call_id,
            {
                "outcome": "voicemail_left",
                "follow_up": "none",
                "follow_up_reason": "Voicemail left; no patient contact.",
                "dob_verification_result": "skipped",
                "survey_responses": {},
            },
        )
        await self._drain_then_hangup("vm")

    def _ulaw_to_native(self, clip: bytes) -> bytes:
        import audioop
        from voa_providers.audio import Resampler

        return Resampler(8_000, self.media.codec.rate)(audioop.ulaw2lin(clip, 2))

    # ------------------------------------------------------------------ audio pumps
    async def _await_start(self, frames) -> StreamStart:
        async for frame in frames:
            if isinstance(frame, StreamStart):
                return frame
            if isinstance(frame, StreamStop):
                raise ConnectionError("stream stopped before start")
        raise ConnectionError("stream ended before start")

    async def _upstream(self, frames) -> None:
        buf = bytearray()
        batch = 16 * INPUT_BATCH_MS * 2
        try:
            async for frame in frames:
                if isinstance(frame, Audio):
                    pcm = self._in(frame.data)
                    if self.recorder:
                        self.recorder.caller(pcm)
                    # During hold the model must not hear the caller (it may be a
                    # voicemail greeting): stream silence instead, keeping audio continuous.
                    buf += (
                        pcm
                        if self._phase == "live" or self._phase == "closing"
                        else bytes(len(pcm))
                    )
                    if len(buf) >= batch:
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
        from voa_core.campaign import load_campaign

        campaign_vocab = [
            *load_campaign(self.campaign_id).custom_vocabulary,
            *self.cfg.voice.custom_vocabulary,
        ]
        try:
            async for event in self.runner.run_live(
                user_id=self.call_id,
                session_id=self.session_id,
                live_request_queue=self.queue,
                run_config=run_config(self.cfg, campaign_vocab),
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
                if (
                    part.inline_data
                    and part.inline_data.data
                    and self._phase in ("live", "closing")
                ):
                    if self._pending_tools and not getattr(self, "_talked_during_tool", False):
                        self._talked_during_tool = True
                        self.timeline.add(
                            "agent.audio_while_tool_pending", tools=sorted(self._pending_tools)
                        )
                    if not self._agent_audio_this_turn:
                        self._agent_audio_this_turn = True
                        self.agent_speaking = True
                        self.timeline.add("agent.audio_start")
                    if self.recorder:
                        self.recorder.agent(part.inline_data.data)
                    await self.media.send_audio(self._out(part.inline_data.data))
        for call in event.get_function_calls():
            self.timeline.add("tool.call", name=call.name)
            self._pending_tools.add(call.name)
            self._talked_during_tool = False
        for resp in event.get_function_responses():
            body = resp.response or {}
            status = body.get("status") or body.get("result")
            self.timeline.add("tool.response", name=resp.name, status=status)
            self._pending_tools.discard(resp.name)
            if resp.name == "submit_report" and status == "ok":
                self._report_filed = True
                self._spawn(self._save_report())
            if resp.name == "end_call" and status == "ending" and self._phase != "closing":
                self._phase = "closing"
                self._spawn(self._finish_turn_then_drain())
        if (t := event.input_transcription) and t.finished and t.text:
            text = clean_transcript(t.text, self._latin_only)
            if text:
                self.transcript.append({"role": "patient", "text": text, "at_ms": self._ms()})
                self.facts.caller_spoke()
        if (t := event.output_transcription) and t.text:
            if t.finished:
                text = clean_transcript(t.text, self._latin_only)
                if text:
                    self.transcript.append({"role": "agent", "text": text, "at_ms": self._ms()})
            else:
                self.facts.agent_said(t.text)
        if event.interrupted:
            self.timeline.add("agent.interrupted")
            await self.media.clear()
            if self.recorder:
                self.recorder.clear()
            self._end_agent_turn()
        if event.turn_complete:
            self.timeline.add("agent.turn_complete")
            self._end_agent_turn()
            self._turn_done.set()
            if self._report_filed and self._phase == "live":
                self._spawn(self._auto_hangup_after_report())
        if event.error_code:
            self.timeline.add("live.event_error", code=event.error_code)

    def _ms(self) -> int:
        return round((time.monotonic() - self.timeline.t0) * 1000)

    def _end_agent_turn(self) -> None:
        if self._agent_audio_this_turn and self.on_agent_turn_end:
            self.on_agent_turn_end()
        self._agent_audio_this_turn = False
        self.agent_speaking = False

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _save_report(self) -> None:
        session = await self.runner.session_service.get_session(
            app_name=self.runner.app_name, user_id=self.call_id, session_id=self.session_id
        )
        report = (session.state if session else {}).get("report")
        if report:
            await self.sink.save_report(self.call_id, dict(report))
            self.timeline.add("report.saved", outcome=report.get("outcome"))

    # ------------------------------------------------------------------ ending
    async def _auto_hangup_after_report(self) -> None:
        grace_s = self.cfg.voice.auto_hangup_grace_s
        turns = self.facts.caller_turns
        await asyncio.sleep(grace_s)
        if self._phase != "live" or self.facts.caller_turns != turns or self.agent_speaking:
            return
        self._phase = "closing"
        self.timeline.add("hangup.auto_after_report")
        await self._drain_then_hangup("closing")

    async def _finish_turn_then_drain(self, grace_s: float = 6.0) -> None:
        self._turn_done.clear()
        try:
            await asyncio.wait_for(self._turn_done.wait(), grace_s)
        except TimeoutError:
            self.timeline.add("drain.turn_wait_timeout")
        await self._drain_then_hangup("closing")

    async def _drain_then_hangup(self, prefix: str) -> None:
        name = f"{prefix}-{uuid.uuid4().hex[:8]}"  # one mark + one Event per drain
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
