"""Run each simulated-patient persona N times against the production voice agent
and report pass rates (single runs hide model variance).

    uv run python evals/voice_personas.py --runs 3 --concurrency 3
    uv run python evals/voice_personas.py --runs 5 happy red_flag

Spanish (docs/LANGUAGES-PLAN.md step 4; the pack is a draft, so evals only):

    VOA_ENV=dev VOA_ALLOW_DRAFT_PACKS=1 uv run python evals/voice_personas.py --set es --runs 1

Each run is a real Gemini Live session driven through the gateway's CallSession:
the simulated patient speaks Gemini-TTS audio at 8 kHz mu-law in real time, AMD is
requested and the verdict arrives as on a real call ("human" after ~2.5 s, or
"machine_end_beep" after the greeting for the voicemail persona). Data goes to an
in-memory store. The patient is fictional.

Writes docs/evals/voice-personas-<date>[-<tag>].md (committed) and evals/out/*.json (raw).
The Spanish personas speak Spanish TTS; their extra checks: the agent's turns are
Spanish, the recorded answers are English typed values, and the closing carries
the Spanish medication-list reminder.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import statistics
import time
import unicodedata
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
from voice_spike.harness import (
    OUT as TTS_OUT,
)
from voice_spike.harness import (
    SCENARIOS,
    Patient,
    PatientVoice,
    Scenario,
    standard_replies,
)

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
    questions: int | None = None  # patient_questions recorded
    #: The patient's language (pending_calls.language); None: English as before.
    language: str | None = None
    #: The caller asks for English part-way (switch_to_english).
    switches_to_english: bool = False


# --------------------------------------------------------------------------- Spanish


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def es_standard(agent_text: str, p: Patient) -> str | None:
    """The happy-path patient, in Spanish. Keyed on the pack's wording (folded)."""
    t = _fold(agent_text)
    if "nueve uno uno" in t or "911" in t or "adios" in t:
        return None
    if "otra pregunta" in t or "otras preguntas" in t or "mas preguntas" in t:
        return "No, eso es todo."
    if "pregunta" in t:
        return "No, no tengo preguntas."
    if "nacimiento" in t:
        return "El quince de marzo de mil novecientos cincuenta."
    if re.search(r"(es correcto|esta bien|es asi|cierto)\s*\?", t):
        return "Sí, es correcto."
    if "buen momento" in t:
        return "Sí, ahora está bien."
    if "hablar con" in t:
        return "Sí, habla Jordan."
    if "cero al diez" in t or "escala" in t:
        return "Como un cuatro."
    if "mejorando" in t and "empeorando" in t:
        return "Está mejorando cada día."
    if "dolor" in t and ("donde" in t or "describir" in t or "otro lugar" in t):
        return "Alrededor de la herida, en la rodilla derecha."
    if "dolor" in t:
        return "Sí, un poco."
    if "fiebre" in t or "temperatura" in t:
        return "No, no he tenido fiebre."
    if "incision" in t or "herida" in t:
        return "No, se ve bien."
    if "una pierna" in t or "las dos" in t:
        return "Los dos pies, un poquito hinchados en la noche."
    if "hinchad" in t or "hinchazon" in t:
        return "No, no tengo hinchazón."
    if "durmiendo" in t or "dormir" in t or "duerme" in t:
        return "Sí, estoy durmiendo bastante bien."
    if "siente" in t or "encuentra" in t:
        return "Bastante bien, solo un poco adolorido."
    return "Está bien."


def es_dob_retry(agent_text: str, p: Patient) -> str | None:
    t = _fold(agent_text)
    if "nacimiento" in t and not p.flags.get("gave_wrong_dob"):
        p.flags["gave_wrong_dob"] = True
        return "El dos de junio de mil novecientos sesenta y uno."
    if ("junio" in t or "sesenta" in t) and re.search(r"(correct|cierto|bien|asi)", t):
        return "No, perdón. Es el quince de marzo de mil novecientos cincuenta."
    return es_standard(agent_text, p)


def es_red_flag(agent_text: str, p: Patient) -> str | None:
    t = _fold(agent_text)
    if ("siente" in t or "encuentra" in t) and "buen momento" not in t:
        return "No muy bien, la verdad. Tengo dolor en el pecho y me falta el aire."
    return es_standard(agent_text, p)


def es_negated_red_flag(agent_text: str, p: Patient) -> str | None:
    t = _fold(agent_text)
    if ("siente" in t or "encuentra" in t) and "buen momento" not in t:
        return "Bastante bien, la verdad. No tengo dolor en el pecho ni nada de eso, solo un poco adolorido."
    return es_standard(agent_text, p)


def es_questions(agent_text: str, p: Patient) -> str | None:
    """A medication question (same day) and a driving question, in Spanish."""
    t = _fold(agent_text)
    if "otra pregunta" in t or "otras preguntas" in t or "mas preguntas" in t:
        if not p.flags.get("asked_second"):
            p.flags["asked_second"] = True
            return "Sí, ¿cuándo voy a poder volver a manejar?"
        return "No, eso es todo."
    if "pregunta" in t and not p.flags.get("asked_first"):
        p.flags["asked_first"] = True
        return "Sí. ¿Cuánto Tylenol puedo tomar, y cada cuánto?"
    return es_standard(agent_text, p)


def es_switch(agent_text: str, p: Patient) -> str | None:
    """Starts in Spanish; asks for English at the first health question; then
    answers in English when the agent speaks English."""
    t = _fold(agent_text)
    if not p.flags.get("switched"):
        if ("siente" in t or "encuentra" in t) and "buen momento" not in t:
            p.flags["switched"] = True
            # How many agent lines were said before the request (transcript index).
            p.flags["switch_turn"] = sum(1 for x in p.bridge.transcript if x["role"] == "agent")
            return "Perdón, ¿podemos hablar en inglés, por favor? I prefer English."
        return es_standard(agent_text, p)
    if spanish_score(agent_text) > 0:
        return "In English, please."
    return standard_replies(agent_text, p)


ES_SCENARIOS = {
    "happy_es": Scenario("happy_es", "questionnaire_completed", reply=es_standard),
    "dob_retry_es": Scenario("dob_retry_es", "questionnaire_completed", reply=es_dob_retry),
    "red_flag_es": Scenario("red_flag_es", "emergency_ended", reply=es_red_flag),
    "negated_red_flag_es": Scenario(
        "negated_red_flag_es", "questionnaire_completed", reply=es_negated_red_flag
    ),
    "patient_questions_es": Scenario(
        "patient_questions_es", "questionnaire_completed", reply=es_questions
    ),
    "switch_to_english": Scenario("switch_to_english", "questionnaire_completed", reply=es_switch),
}

_ES_WORDS = frozenset(
    "el la los las de del que y en usted su sus por favor gracias para con es esta tiene "
    "como se lo una un me le algun alguna dolor pregunta preguntas hola adios bien cuidese "
    "fecha nacimiento verificada momento llamada equipo".split()
)
_EN_WORDS = frozenset(
    "the you your and to is are have how thank thanks please that it any of with for this "
    "what do does feeling pain questions date birth verified good time call team".split()
)


def spanish_score(text: str) -> int:
    """Spanish minus English common words in one turn: > 0 reads as Spanish."""
    words = re.findall(r"[a-z]+", _fold(text))
    return sum(w in _ES_WORDS for w in words) - sum(w in _EN_WORDS for w in words)


def spanish_share(turns: list[str]) -> float | None:
    scored = [spanish_score(x) for x in turns if x.strip()]
    scored = [x for x in scored if x != 0]
    return (sum(x > 0 for x in scored) / len(scored)) if scored else None


def english_answers(responses: dict) -> list[str]:
    """Recorded answers that are not plain English (should be none)."""
    from voa_core.translate import looks_english

    bad = []
    for k, v in (responses or {}).items():
        if k.startswith("_"):
            continue
        if k == "patient_questions":
            bad += [
                f"{k}: {q.get('text')}"
                for q in v or []
                if not looks_english(str(q.get("text", "")))
            ]
        elif isinstance(v, str) and v and not looks_english(v):
            bad.append(f"{k}: {v}")
    return bad


class SpanishPatientVoice(PatientVoice):
    """The simulated patient in Spanish (Gemini-TTS with language_code es-US)."""

    def say(self, text: str) -> bytes:
        import audioop
        from google.genai import types
        from voa_providers.audio import Resampler

        key = hashlib.sha256(f"{self._model}|{self._voice}|es-US|{text}".encode()).hexdigest()[:16]
        path = TTS_OUT / "tts" / f"{key}.ulaw"
        if path.exists():
            return path.read_bytes()
        resp = self._client.models.generate_content(
            model=self._model,
            contents=f"Dilo como una persona mayor que contesta el teléfono, con naturalidad: {text}",
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self._voice)
                    ),
                    language_code="es-US",
                ),
            ),
        )
        pcm24 = resp.candidates[0].content.parts[0].inline_data.data
        ulaw = audioop.lin2ulaw(Resampler(24_000, 8_000)(pcm24), 2)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(ulaw)
        return ulaw


PERSONAS = {
    "happy": Persona("happy", "questionnaire_completed"),
    "dob_retry": Persona("dob_retry", "questionnaire_completed"),
    "barge_in": Persona("barge_in", "questionnaire_completed", must_interrupt=True),
    "red_flag": Persona("red_flag", "emergency_ended", tier="emergency"),
    "negated_red_flag": Persona(
        "negated_red_flag", "questionnaire_completed", forbid_tier="emergency"
    ),
    "wrong_person": Persona("wrong_person", "unable_to_reach_wrong_person"),
    # A medication question must make the call same-day, with both questions recorded.
    "patient_questions": Persona(
        "patient_questions", "questionnaire_completed", tier="urgent_same_day", questions=2
    ),
    "voicemail": Persona(
        "happy", "voicemail_left", verdict="machine_end_beep", verdict_after_s=8.0
    ),
}
ES = "es-US"
PERSONAS_ES = {
    "happy_es": Persona("happy_es", "questionnaire_completed", language=ES),
    "dob_retry_es": Persona("dob_retry_es", "questionnaire_completed", language=ES),
    "red_flag_es": Persona("red_flag_es", "emergency_ended", tier="emergency", language=ES),
    "negated_red_flag_es": Persona(
        "negated_red_flag_es", "questionnaire_completed", forbid_tier="emergency", language=ES
    ),
    "patient_questions_es": Persona(
        "patient_questions_es",
        "questionnaire_completed",
        tier="urgent_same_day",
        questions=2,
        language=ES,
    ),
    "switch_to_english": Persona(
        "switch_to_english", "questionnaire_completed", language=ES, switches_to_english=True
    ),
    "voicemail_es": Persona(
        "happy_es", "voicemail_left", verdict="machine_end_beep", verdict_after_s=8.0, language=ES
    ),
}
ALL_PERSONAS = {**PERSONAS, **PERSONAS_ES}
ALL_SCENARIOS = {**SCENARIOS, **ES_SCENARIOS}


async def run_once(cfg, gw: Gateway, voice: PatientVoice, name: str, p: Persona) -> dict:
    store = MemoryStore()
    sink = StoreCallSink(
        store, recordings_bucket=None, project_id=cfg.gcp.project_id, call_id_pattern=CALL_ID_RE
    )
    call_id = "local_" + uuid.uuid4().hex[:16]
    staged = dict(PATIENT) if p.language is None else {**PATIENT, "language": p.language}
    await store.set("pending_calls", call_id, staged)
    await store.set("calls", call_id, {"call_sid": call_id, "amd_requested": True})
    media = FakeMediaSession(codec=Codec.MULAW_8K, call_id=call_id)
    patient = Patient(media=media, voice=voice, scenario=ALL_SCENARIOS[p.scenario])
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
    # The live transcript: a timed-out run never reaches the sink's finish().
    turns = list(session.transcript)
    agent_said = " ".join(
        str(x.get("text", "")) for x in turns if x.get("role") in ("agent", "assistant")
    ).lower()
    asked = (call.get("survey_responses") or {}).get("patient_questions") or []
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
        "questions": p.questions is None or len(asked) == p.questions,
        # Every completed call ends with the medication-list reminder.
        "med_reminder": p.outcome != "questionnaire_completed"
        or "list of your current medications" in agent_said
        or (p.language is not None and _es_med_reminder(agent_said)),
    }
    extra: dict = {}
    if p.language is not None:
        agent_turns = [str(x.get("text", "")) for x in turns if x.get("role") == "agent"]
        lang_event = next((e for e in tl if e["event"] == "language"), None)
        checks["spoken_language_set"] = bool(lang_event and lang_event.get("code") == p.language)
        bad = english_answers(call.get("survey_responses") or {})
        checks["answers_english"] = not bad
        if p.switches_to_english:
            k = patient.flags.get("switch_turn")
            before = agent_turns[: k or 0]
            after = agent_turns[(k or 0) :]
            share_before, share_after = spanish_share(before), spanish_share(after)
            checks["spanish_before_switch"] = share_before is not None and share_before >= 0.8
            checks["english_after_switch"] = share_after is not None and share_after <= 0.2
            extra.update(spanish_before=share_before, spanish_after=share_after)
        elif p.outcome != "voicemail_left":
            share = spanish_share(agent_turns)
            checks["agent_spanish"] = share is not None and share >= 0.9
            checks["med_reminder"] = p.outcome != "questionnaire_completed" or _es_med_reminder(
                agent_said
            )
            extra["spanish_share"] = share
        else:
            checks["spanish_clip"] = any(e["event"] == "vm.play" for e in tl)
        if bad:
            extra["non_english_answers"] = bad
        # Fictional patient: kept for every language run, for the review of the wording.
        extra["transcript_es"] = [f"{x.get('role')}: {x.get('text')}" for x in turns]
        extra["answers"] = {
            k: v for k, v in (call.get("survey_responses") or {}).items() if not k.startswith("_")
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
        **extra,
        # Kept for failed runs only (fictional patient): where did it stall?
        **(
            {}
            if all(checks.values()) and error is None
            else {"transcript": [f"{x.get('role')}: {x.get('text')}" for x in turns]}
        ),
    }


def _es_med_reminder(said: str) -> bool:
    t = _fold(said)
    return "lista" in t and "medicamentos" in t


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("personas", nargs="*")
    ap.add_argument("--set", choices=["en", "es", "all"], default="en")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--tag", default="", help="suffix for the report file name")
    a = ap.parse_args()
    if not a.personas:
        a.personas = list({"en": PERSONAS, "es": PERSONAS_ES, "all": ALL_PERSONAS}[a.set])
    if any(ALL_PERSONAS[n].language for n in a.personas):
        from voa_core import language_packs

        if not language_packs.voice_pack_for_call("post_op", ES):
            raise SystemExit(
                "The Spanish pack is not approved: run with VOA_ENV=dev VOA_ALLOW_DRAFT_PACKS=1 "
                "(evals only)."
            )
    cfg = load_config()
    gw = Gateway(cfg, store=MemoryStore())
    voices = {None: PatientVoice(cfg), ES: SpanishPatientVoice(cfg)}
    sem = asyncio.Semaphore(a.concurrency)
    jobs = [(n, ALL_PERSONAS[n]) for n in a.personas for _ in range(a.runs)]

    async def guarded(n, p):
        async with sem:
            r = await run_once(cfg, gw, voices[p.language], n, p)
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
    md = DOCS / f"voice-personas-{stamp[:8]}{'-' + a.tag if a.tag else ''}.md"
    md.write_text(
        "\n".join(
            [
                f"# Voice persona eval, {stamp[:8]}",
                "",
                f"Model `{cfg.models.live}`, AMD strategy `{cfg.amd.strategy}`, {a.runs} runs per persona, "
                f"concurrency {a.concurrency}. Overall **{total}/{len(results)}** passed."
                + (
                    " Spanish personas: the post-op es-US pack (status "
                    f"`{_pack_status()}`, used with VOA_ALLOW_DRAFT_PACKS=1 in "
                    f"VOA_ENV={os.environ.get('VOA_ENV')})."
                    if any(ALL_PERSONAS[n].language for n in a.personas)
                    else ""
                ),
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


def _pack_status() -> str:
    from voa_core import language_packs

    p = language_packs.load_pack("post_op", ES)
    return p.status if p else "missing"


if __name__ == "__main__":
    asyncio.run(main())
