# ruff: noqa: E501
"""Build the sample-mode call recording and its transcript (macOS only).

Every word is fictional. The patient (Vivian Ashcombe, P-2054) is invented and
matches her answers in ui/src/lib/nurse/demo.ts. Voices come from macOS ``say``;
the layout matches real recordings: patient on the left channel, agent on the
right.

Writes:
  ui/public/samples/post-op-call-sample.m4a   the recording (AAC, stereo)
  ui/src/lib/nurse/sample-call.json           the turns with their start times

Run: python3 scripts/demo/make_sample_recording.py
"""

from __future__ import annotations

import array
import json
import subprocess
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIO_OUT = ROOT / "ui/public/samples/post-op-call-sample.m4a"
JSON_OUT = ROOT / "ui/src/lib/nurse/sample-call.json"

RATE = 16000
GAP_S = 0.6
LEAD_S = 1.0
VOICES = {"agent": "Samantha", "patient": "Moira"}

TURNS: list[tuple[str, str]] = [
    (
        "agent",
        "Hi, this is Sara calling from the clinic about your recent knee surgery. Am I speaking with Vivian?",
    ),
    ("patient", "Yes, this is Vivian."),
    ("agent", "Thanks, Vivian. To confirm your identity, can you tell me your date of birth?"),
    ("patient", "March third, nineteen fifty-eight."),
    ("agent", "Thank you. Are you having any pain today?"),
    ("patient", "Yes, some."),
    ("agent", "On a scale from zero to ten, how bad is it?"),
    ("patient", "About a five. It's a tight, aching feeling in my calf, below the new knee."),
    ("agent", "Is it better, worse, or about the same as yesterday?"),
    ("patient", "About the same."),
    (
        "agent",
        "Have you had a fever, a temperature over one hundred point four, since you went home?",
    ),
    ("patient", "No."),
    ("agent", "How does your incision look? Any redness, drainage, or opening?"),
    ("patient", "It looks fine."),
    ("agent", "Is either of your legs swollen?"),
    ("patient", "Just the left leg. The calf is puffier than yesterday, and it's tender."),
    (
        "agent",
        "Thank you for telling me. Swelling in one calf is something a nurse should look at today, so I'm flagging it now. Are you sleeping well?",
    ),
    ("patient", "Pretty well, yes."),
    ("agent", "And overall, how are you feeling?"),
    ("patient", "Okay."),
    ("agent", "Do you have any questions for your care team?"),
    ("patient", "No, I don't think so."),
    (
        "agent",
        "A nurse will call you back today about the swelling. If you get chest pain or trouble breathing, call nine one one right away. Take care, Vivian.",
    ),
    ("patient", "Okay. Thank you. Bye."),
]


def synth(text: str, voice: str, path: Path) -> array.array:
    subprocess.run(
        [
            "say",
            "-v",
            voice,
            "-r",
            "180",
            "-o",
            str(path),
            "--file-format=WAVE",
            f"--data-format=LEI16@{RATE}",
            text,
        ],
        check=True,
    )
    with wave.open(str(path)) as w:
        assert w.getnchannels() == 1 and w.getframerate() == RATE and w.getsampwidth() == 2
        return array.array("h", w.readframes(w.getnframes()))


def main() -> None:
    left = array.array("h")  # patient
    right = array.array("h")  # agent
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        cursor = int(LEAD_S * RATE)
        for i, (role, text) in enumerate(TURNS):
            pcm = synth(text, VOICES[role], Path(tmp) / f"{i}.wav")
            end = cursor + len(pcm)
            for ch in (left, right):
                if len(ch) < end:
                    ch.extend([0] * (end - len(ch)))
            target = left if role == "patient" else right
            target[cursor:end] = pcm
            rows.append({"role": role, "s": round(cursor / RATE, 1), "text": text})
            cursor = end + int(GAP_S * RATE)
        n = cursor
        for ch in (left, right):
            ch.extend([0] * (n - len(ch)))
        stereo = array.array("h", [0] * (2 * n))
        stereo[0::2] = left
        stereo[1::2] = right
        wav = Path(tmp) / "call.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(stereo.tobytes())
        AUDIO_OUT.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["afconvert", str(wav), str(AUDIO_OUT), "-f", "m4af", "-d", "aac", "-b", "40000"],
            check=True,
        )

    JSON_OUT.write_text(
        json.dumps({"duration_s": round(n / RATE, 1), "turns": rows}, indent=2) + "\n"
    )
    print(
        f"{AUDIO_OUT.relative_to(ROOT)}: {AUDIO_OUT.stat().st_size // 1024} KB, {n / RATE:.1f} s, {len(rows)} turns"
    )


if __name__ == "__main__":
    main()
