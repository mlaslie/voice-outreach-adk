"""Run each simulated-patient persona N times against the production voice agent
and report pass rates (single runs hide model variance).

    uv run python evals/voice_personas.py --runs 3 --concurrency 3
    uv run python evals/voice_personas.py --runs 5 happy red_flag

Each run is a real Gemini Live session driven through the gateway's CallSession:
the simulated patient speaks Gemini-TTS audio at 8 kHz mu-law in real time, AMD is
requested and the verdict arrives as on a real call ("human" after ~2.5 s, or
"machine_end_beep" after the greeting for the voicemail persona). Data goes to an
in-memory store. The patient is fictional.

Writes docs/evals/voice-personas-<date>.md (committed) and evals/out/*.json (raw).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from voa_core.config import load_config
from voa_core.store import MemoryStore
from voa_gateway.app import CALL_ID_RE, Gateway
from voa_gateway.persist import StoreCallSink
from voa_gateway.session import AmdPolicy, CallSession
from voa_providers.audio import Codec
from voa_providers.fake import FakeMediaSession
from voice_spike.harness import SCENARIOS, Patient, PatientVoice

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "out"
DOCS = ROOT / "docs" / "evals"
PATIENT = {
    "first_name": "Jordan",
    "last_name": "Rivera",
    "dob": "1950-03-15",
    "patient_id": "TEST-0001",
    "is_diabetic": False,
}


@dataclass(frozen=True)
class Persona:
    scenario: str
    outcome: str
    verdict: str = "human"
    verdict_after_s: float = 2.5
    tier: str | None = None
    must_interrupt: bool = False
    forbid_tier: str | None = None


PERSONAS = {
    "happy": Persona("happy", "questionnaire_completed"),
    "dob_retry": Persona("dob_retry", "questionnaire_completed"),
    "barge_in": Persona("barge_in", "questionnaire_completed", must_interrupt=True),
    "red_flag": Persona("red_flag", "emergency_ended", tier="emergency"),
    "negated_red_flag": Persona(
        "negated_red_flag", "questionnaire_completed", forbid_tier="emergency"
    ),
    "wrong_person": Persona("wrong_person", "unable_to_reach_wrong_person"),
    "voicemail": Persona(
        "happy", "voicemail_left", verdict="machine_end_beep", verdict_after_s=8.0
    ),
}


async def run_once(cfg, gw: Gateway, voice: PatientVoice, name: str, p: Persona) -> dict:
    store = MemoryStore()
    sink = StoreCallSink(
        store, recordings_bucket=None, project_id=cfg.gcp.project_id, call_id_pattern=CALL_ID_RE
    )
    call_id = "local_" + uuid.uuid4().hex[:16]
    await store.set("pending_calls", call_id, dict(PATIENT))
    await store.set("calls", call_id, {"call_sid": call_id, "amd_requested": True})
    media = FakeMediaSession(codec=Codec.MULAW_8K, call_id=call_id)
    patient = Patient(media=media, voice=voice, scenario=SCENARIOS[p.scenario])
    session = CallSession(
        cfg=cfg,
        runner=gw.runner,
        media=media,
        sink=sink,
        amd=AmdPolicy(enabled=True, vm_drop=True, strategy=cfg.amd.strategy),
        voicemail_clip=gw.voicemail_clip,
        on_agent_turn_end=patient.on_agent_turn_end,
    )
    patient.bridge = session
    media.start()

    async def verdict():
        await asyncio.sleep(p.verdict_after_s)
        await store.merge("calls", call_id, {"answered_by": p.verdict})

    tasks = [
        asyncio.create_task(c)
        for c in (patient.feeder(), patient.conversation(), patient.barge_in(), verdict())
    ]
    t0 = time.monotonic()
    error = None
    try:
        await asyncio.wait_for(session.run(), 300)
    except Exception as exc:  # noqa: BLE001 - recorded as a failed run
        error = type(exc).__name__
    finally:
        for t in tasks:
            t.cancel()
    call = await store.get("calls", call_id) or {}
    tl = session.timeline.entries
    first = next((e["ms"] for e in tl if e["event"] == "agent.audio_start"), None)
    start = next((e["ms"] for e in tl if e["event"] == "media.start"), 0)
    starts = [e["ms"] for e in tl if e["event"] == "agent.audio_start"]
    lat = []
    for end in patient.speech_ends:
        end_ms = round((end - session.timeline.t0) * 1000)
        nxt = next((s for s in starts if s > end_ms), None)
        if nxt is not None:
            lat.append(nxt - end_ms)
    checks = {
        "outcome": call.get("outcome") == p.outcome,
        "tier": p.tier is None or call.get("follow_up") == p.tier,
        "forbid_tier": p.forbid_tier is None or call.get("follow_up") != p.forbid_tier,
        "interrupt": not p.must_interrupt or any(e["event"] == "agent.interrupted" for e in tl),
        "no_live_error": not any(e["event"].startswith("live.") for e in tl),
        "drained": any(e["event"] == "media.mark_ack" for e in tl) or p.outcome == "voicemail_left",
    }
    return {
        "persona": name,
        "pass": all(checks.values()) and error is None,
        "checks": checks,
        "error": error,
        "outcome": call.get("outcome"),
        "tier": call.get("follow_up"),
        "first_audio_ms": (first - start) if first is not None else None,
        "response_latency_ms": lat,
        "auto_hangup": any(e["event"] == "hangup.auto_after_report" for e in tl),
        "report_refusals": sum(
            1
            for e in tl
            if e["event"] == "tool.response"
            and e.get("name") == "submit_report"
            and e.get("status") == "refused"
        ),
        "duration_s": round(time.monotonic() - t0, 1),
    }


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("personas", nargs="*", default=list(PERSONAS))
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=3)
    a = ap.parse_args()
    cfg = load_config()
    gw = Gateway(cfg, store=MemoryStore())
    voice = PatientVoice(cfg)
    sem = asyncio.Semaphore(a.concurrency)
    jobs = [(n, PERSONAS[n]) for n in a.personas for _ in range(a.runs)]

    async def guarded(n, p):
        async with sem:
            r = await run_once(cfg, gw, voice, n, p)
            print(
                f"{n:18} {'PASS' if r['pass'] else 'FAIL'} outcome={r['outcome']} "
                f"first_audio={r['first_audio_ms']}ms",
                flush=True,
            )
            return r

    results = await asyncio.gather(*(guarded(n, p) for n, p in jobs))
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    (OUT / f"voice-personas-{stamp}.json").write_text(json.dumps(results, indent=2))

    rows = []
    for n in a.personas:
        rs = [r for r in results if r["persona"] == n]
        passed = sum(r["pass"] for r in rs)
        lat = [x for r in rs for x in r["response_latency_ms"]]
        fails = sorted(
            {k for r in rs if not r["pass"] for k, v in r["checks"].items() if not v}
            | {f"error:{r['error']}" for r in rs if r["error"]}
        )
        rows.append(
            f"| {n} | {passed}/{len(rs)} | {pct([r['first_audio_ms'] for r in rs], 0.5)} | "
            f"{pct(lat, 0.5)} | {pct(lat, 0.9)} | {sum(r['auto_hangup'] for r in rs)} | "
            f"{', '.join(fails) or '-'} |"
        )
    total = sum(r["pass"] for r in results)
    DOCS.mkdir(parents=True, exist_ok=True)
    md = DOCS / f"voice-personas-{stamp[:8]}.md"
    md.write_text(
        "\n".join(
            [
                f"# Voice persona eval, {stamp[:8]}",
                "",
                f"Model `{cfg.models.live}`, AMD strategy `{cfg.amd.strategy}`, {a.runs} runs per persona, "
                f"concurrency {a.concurrency}. Overall **{total}/{len(results)}** passed.",
                "",
                "Latencies in ms. *First audio* is from media start to the agent's first audio "
                "(includes the opener's generation; AMD verdict arrives at 2.5 s). *Response* is "
                "end of patient speech to agent audio (includes 700 ms end-of-speech detection).",
                "",
                "| Persona | Pass | First audio p50 | Response p50 | Response p90 | Auto-hangups | Failed checks |",
                "|---|---|---|---|---|---|---|",
                *rows,
                "",
                f"Mean call duration: {statistics.mean(r['duration_s'] for r in results):.0f} s. "
                "Raw results: `evals/out/` (gitignored).",
                "",
            ]
        )
    )
    print(f"\n{total}/{len(results)} passed -> {md.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
