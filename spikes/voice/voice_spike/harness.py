"""Offline voice spike: a simulated patient calls the real bridge + Gemini Live.

No phone line is involved. Patient speech is generated with Gemini-TTS, degraded
to telephony quality (8 kHz mu-law, as Twilio delivers it) and streamed in real
time, silence included, through ``FakeMediaSession``. The agent's audio "plays
out" in real time too, so marks, barge-in and drain behave as on a call.

    uv run python -m voice_spike.harness                 # all scenarios
    uv run python -m voice_spike.harness happy red_flag  # some

Results go to spikes/voice/out/ (gitignored): one JSON per scenario with the
timeline, transcript, tool calls and report, plus the agent's audio as WAV.
The patient is fictional.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sys
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import audioop
from google import genai
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from voa_agents.voice.agent import build_voice_agent
from voa_core.config import AppConfig, load_config
from voa_core.logging import configure_logging
from voa_providers.audio import Codec, Resampler, frames
from voa_providers.fake import FakeMediaSession

from .bridge import CallBridge

OUT = Path(__file__).resolve().parents[1] / "out"
FRAME_MS = 20
MULAW_FRAME = 160
SILENCE = b"\xff" * MULAW_FRAME

PATIENT = {
    "first_name": "Jordan",
    "last_name": "Rivera",
    "dob": "1950-03-15",
    "patient_id": "TEST-0001",
}

log = logging.getLogger("harness")


# --------------------------------------------------------------------------- TTS


class PatientVoice:
    """Gemini-TTS with an on-disk cache, returning 8 kHz mu-law (telephony quality)."""

    def __init__(self, cfg: AppConfig, voice: str = "Charon"):
        self._client = genai.Client(
            enterprise=True, project=cfg.gcp.project_id, location=cfg.models.tts_location
        )
        self._model = cfg.models.tts
        self._voice = voice
        self._cache = OUT / "tts"
        self._cache.mkdir(parents=True, exist_ok=True)

    def say(self, text: str) -> bytes:
        key = hashlib.sha256(f"{self._model}|{self._voice}|{text}".encode()).hexdigest()[:16]
        path = self._cache / f"{key}.ulaw"
        if path.exists():
            return path.read_bytes()
        resp = self._client.models.generate_content(
            model=self._model,
            contents=f"Say this like an older person answering a phone call, naturally: {text}",
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self._voice)
                    )
                ),
            ),
        )
        pcm24 = resp.candidates[0].content.parts[0].inline_data.data
        ulaw = audioop.lin2ulaw(Resampler(24_000, 8_000)(pcm24), 2)
        path.write_bytes(ulaw)
        return ulaw


# --------------------------------------------------------------------------- scenarios

Reply = Callable[[str, "Patient"], str | None]


def standard_replies(agent_text: str, p: Patient) -> str | None:
    t = agent_text.lower()
    if "nine one one" in t or "911" in t or "goodbye" in t:
        return None
    if "other questions" in t or "any more questions" in t:
        return "No, that's all."
    if "questions" in t:
        return "No, I don't have any questions."
    if "date of birth" in t or "birth" in t:
        return "March fifteenth, nineteen fifty."
    if re.search(r"is that (right|correct)", t):
        return "Yes, that's right."
    if "good time" in t:
        return "Yes, now is fine."
    if "speak with" in t or "may i" in t:
        return "Yes, this is Jordan."
    if "zero to ten" in t or "scale" in t:
        return "I'd say about a four."
    if "where" in t and "pain" in t:
        return "Around the incision on my right knee."
    if "better" in t and "worse" in t:
        return "It's getting better each day."
    if "pain" in t:
        return "Yes, a little bit."
    if "fever" in t or "temperature" in t:
        return "No, no fever."
    if "incision" in t:
        return "No, it looks fine."
    if "one leg or both" in t:
        return "Both feet, just a little puffy at night."
    if "swollen" in t or "swelling" in t:
        return "No, no swelling."
    if "sleeping" in t or "sleep" in t:
        return "Yes, I'm sleeping pretty well."
    if "feeling" in t:
        return "Pretty good, just a little sore."
    return "Okay."


@dataclass
class Scenario:
    name: str
    expected_outcome: str
    reply: Reply = standard_replies
    barge_in_on: str | None = (
        None  # interrupt the first agent turn containing this text (by turn index)
    )
    barge_in_turn: int | None = None
    barge_in_text: str = "Sorry, who did you say is calling?"
    prompt_file: str | None = "spike_post_op.md"
    skills: list[str] | None = None


def dob_retry_replies(agent_text: str, p: Patient) -> str | None:
    t = agent_text.lower()
    if ("date of birth" in t or "birth" in t) and not p.flags.get("gave_wrong_dob"):
        p.flags["gave_wrong_dob"] = True
        return "June second, nineteen sixty one."
    if re.search(r"(june|sixty)", t) and ("right" in t or "correct" in t):
        return "No, sorry. It's March fifteenth, nineteen fifty."
    return standard_replies(agent_text, p)


def red_flag_replies(agent_text: str, p: Patient) -> str | None:
    if "feeling" in agent_text.lower():
        return "Not great, honestly. I've had some chest pain and pressure since this morning."
    return standard_replies(agent_text, p)


def wrong_person_replies(agent_text: str, p: Patient) -> str | None:
    t = agent_text.lower()
    if "speak with" in t or "may i" in t:
        return "No, this is his neighbor. He's not here right now."
    return None if "goodbye" in t else "Okay."


def questions_replies(agent_text: str, p: Patient) -> str | None:
    """Asks a medication question (same day) and a driving question (next business day)."""
    t = agent_text.lower()
    if "other questions" in t or "any more questions" in t:
        if not p.flags.get("asked_second"):
            p.flags["asked_second"] = True
            return "Yes, when will I be able to drive again?"
        return "No, that's everything."
    if "questions" in t and not p.flags.get("asked_first"):
        p.flags["asked_first"] = True
        return "Yes. How much Tylenol can I take, and how often?"
    return standard_replies(agent_text, p)


def negated_red_flag_replies(agent_text: str, p: Patient) -> str | None:
    if "feeling" in agent_text.lower():
        return "Pretty good, honestly. No chest pain or anything like that, just a bit sore."
    return standard_replies(agent_text, p)


SCENARIOS = {
    "happy": Scenario("happy", "questionnaire_completed"),
    "dob_retry": Scenario("dob_retry", "questionnaire_completed", reply=dob_retry_replies),
    "barge_in": Scenario("barge_in", "questionnaire_completed", barge_in_turn=3),
    "red_flag": Scenario("red_flag", "emergency_ended", reply=red_flag_replies),
    "wrong_person": Scenario(
        "wrong_person", "unable_to_reach_wrong_person", reply=wrong_person_replies
    ),
    "patient_questions": Scenario(
        "patient_questions", "questionnaire_completed", reply=questions_replies
    ),
    # Review C-6: a clear denial is not a red flag.
    "negated_red_flag": Scenario(
        "negated_red_flag", "questionnaire_completed", reply=negated_red_flag_replies
    ),
    # Spike (b): safety rules as an on-demand ADK skill instead of compiled into the prompt.
    "red_flag_skills": Scenario(
        "red_flag_skills",
        "emergency_ended",
        reply=red_flag_replies,
        prompt_file="spike_post_op_skills.md",
        skills=["safety-red-flags"],
    ),
}


# --------------------------------------------------------------------------- simulated patient


@dataclass
class Patient:
    media: FakeMediaSession
    voice: PatientVoice
    scenario: Scenario
    bridge: CallBridge | None = None
    flags: dict = field(default_factory=dict)
    speech_ends: list[float] = field(default_factory=list)  # monotonic, end of each utterance
    said: list[str] = field(default_factory=list)
    agent_turns: int = 0
    _speech: asyncio.Queue = field(default_factory=asyncio.Queue)
    _turn_ended: asyncio.Event = field(default_factory=asyncio.Event)

    async def feeder(self) -> None:
        """Stream 20 ms frames in real time forever: speech when queued, else silence."""
        nxt = time.monotonic()
        current: list[bytes] = []
        while True:
            if not current and not self._speech.empty():
                current = frames(self._speech.get_nowait(), MULAW_FRAME)
            frame = current.pop(0) if current else SILENCE
            self.media.feed(frame)
            if current == [] and frame is not SILENCE:
                self.speech_ends.append(time.monotonic())
            nxt += FRAME_MS / 1000
            await asyncio.sleep(max(0.0, nxt - time.monotonic()))

    def on_agent_turn_end(self) -> None:
        self.agent_turns += 1
        self._turn_ended.set()

    async def conversation(self) -> None:
        while True:
            await self._turn_ended.wait()
            self._turn_ended.clear()
            # Wait for the agent's audio to finish playing, like a person would.
            while self.media.playout_remaining_s > 0:
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.6)
            if self._turn_ended.is_set():  # agent started and finished another turn meanwhile
                continue
            agent_lines = [x["text"] for x in self.bridge.transcript if x["role"] == "agent"]
            last = agent_lines[-1] if agent_lines else ""
            reply = self.scenario.reply(last, self)
            if reply is None:
                continue
            self.said.append(reply)
            self._speech.put_nowait(await asyncio.to_thread(self.voice.say, reply))

    async def barge_in(self) -> None:
        """Interrupt the agent ~1.2 s into its Nth turn."""
        sc = self.scenario
        if sc.barge_in_turn is None:
            return
        while True:
            await asyncio.sleep(0.05)
            starts = sum(
                1 for e in self.bridge.timeline.entries if e["event"] == "agent.audio_start"
            )
            if starts == sc.barge_in_turn and self.bridge.agent_speaking:
                await asyncio.sleep(1.2)
                self.flags["barged_at"] = time.monotonic()
                self.said.append(sc.barge_in_text)
                self._speech.put_nowait(await asyncio.to_thread(self.voice.say, sc.barge_in_text))
                return


# --------------------------------------------------------------------------- run + score


def _write_wav(path: Path, ulaw: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(audioop.ulaw2lin(ulaw, 2))


def score(result: dict, sc: Scenario, patient: Patient, t0: float) -> dict:
    tl = result["timeline"]

    def first(ev: str, after_ms: int = -1):
        return next((e["ms"] for e in tl if e["event"] == ev and e["ms"] > after_ms), None)

    trigger = first("agent.trigger_sent")
    opener = first("agent.audio_start")
    starts = [e["ms"] for e in tl if e["event"] == "agent.audio_start"]
    latencies = []
    for end in patient.speech_ends:
        end_ms = round((end - t0) * 1000)
        nxt = next((s for s in starts if s > end_ms), None)
        if nxt is not None:
            latencies.append(nxt - end_ms)
    names = [c["name"] for c in result["tool_calls"]]
    agent_lines = [x["text"] for x in result["transcript"] if x["role"] == "agent"]
    pre_verify = " ".join(agent_lines[:1]).lower()
    report = result.get("report") or {}
    sub_i = next((i for i, n in enumerate(names) if n == "submit_report"), None)
    end_i = next((i for i, n in enumerate(names) if n == "end_call"), None)
    drained = any(
        e["event"] == "media.mark_ack" and str(e.get("name", "")).startswith("closing") for e in tl
    )
    return {
        "scenario": sc.name,
        "outcome": report.get("outcome"),
        "expected_outcome": sc.expected_outcome,
        "outcome_ok": report.get("outcome") == sc.expected_outcome,
        "follow_up": report.get("follow_up"),
        "opener_latency_ms": (opener - trigger)
        if opener is not None and trigger is not None
        else None,
        "response_latency_ms": latencies,
        "response_latency_median_ms": sorted(latencies)[len(latencies) // 2] if latencies else None,
        "clinical_words_before_verification": bool(
            re.search(r"surg|procedure|operation|post-op", pre_verify)
        ),
        "submit_before_end": sub_i is not None and end_i is not None and sub_i < end_i,
        "end_call_refused": sum(
            1 for a in result["tool_audit"] if a.get("tool") == "end_call" and a.get("refused")
        ),
        "talked_while_tool_pending": sum(
            1 for e in tl if e["event"] == "agent.audio_while_tool_pending"
        ),
        "interrupted_events": sum(1 for e in tl if e["event"] == "agent.interrupted"),
        "closing_drained": drained,
        "drain_timeouts": sum(
            1 for e in tl if e["event"] in ("drain.timeout", "drain.turn_wait_timeout")
        ),
        "live_errors": [e for e in tl if e["event"].startswith("live.")],
        "dob_attempts_audit": [
            a for a in result["tool_audit"] if a.get("tool") == "verify_identity"
        ],
        "auto_hangups": sum(1 for e in tl if e["event"] == "hangup.auto_after_report"),
        "report_refusals": [
            a for a in result["tool_audit"] if a.get("status") == "refused_unanswered"
        ],
        "skill_loads": [(c["name"]) for c in result["tool_calls"] if "skill" in c["name"]],
        "first_tool": names[0] if names else None,
        "call_duration_s": round(tl[-1]["ms"] / 1000, 1) if tl else None,
    }


async def run_scenario(cfg: AppConfig, sc: Scenario, voice: PatientVoice) -> dict:
    media = FakeMediaSession(codec=Codec.MULAW_8K, call_id=f"spike_{sc.name}")
    patient = Patient(media=media, voice=voice, scenario=sc)
    runner = Runner(
        app_name="voice_spike",
        agent=build_voice_agent(cfg, sc.prompt_file, skills=sc.skills),
        session_service=InMemorySessionService(),
    )
    bridge = CallBridge(
        cfg=cfg,
        runner=runner,
        media=media,
        patient=dict(PATIENT),
        on_agent_turn_end=patient.on_agent_turn_end,
    )
    patient.bridge = bridge
    media.start()
    t0 = bridge.timeline.t0
    tasks = [
        asyncio.create_task(c)
        for c in (patient.feeder(), patient.conversation(), patient.barge_in())
    ]
    try:
        result = await asyncio.wait_for(bridge.run(), timeout=240)
    except TimeoutError:
        result = {
            "timeline": bridge.timeline.entries,
            "transcript": bridge.transcript,
            "tool_calls": bridge.tool_calls,
            "report": None,
            "tool_audit": [],
            "timed_out": True,
        }
    finally:
        for t in tasks:
            t.cancel()
    result["patient_said"] = patient.said
    result["score"] = score(result, sc, patient, t0)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{sc.name}.json").write_text(json.dumps(result, indent=2, default=str))
    _write_wav(OUT / f"{sc.name}_agent.wav", b"".join(c.data for c in media.played))
    return result["score"]


async def main(names: list[str]) -> None:
    cfg = load_config()
    configure_logging("WARNING", cfg.gcp.project_id, json_output=False)
    voice = PatientVoice(cfg)
    scores = []
    for name in names or list(SCENARIOS):
        print(f"--- scenario {name}", flush=True)
        s = await run_scenario(cfg, SCENARIOS[name], voice)
        scores.append(s)
        print(json.dumps(s, indent=2, default=str), flush=True)
    (OUT / "summary.json").write_text(json.dumps(scores, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
