import io
import wave

from voa_gateway.recorder import StereoRecorder


def test_agent_audio_is_placed_on_the_caller_clock_and_cut_on_clear():
    r = StereoRecorder(caller_rate=16_000, agent_rate=24_000)
    r.caller(b"\x00\x00" * 16_000)  # 1 s of caller audio
    r.agent(b"\x10\x00" * 48_000)  # 2 s of agent audio, arrives at t=1s
    r.clear()  # barge-in at t=1s: none of it played
    r.caller(b"\x00\x00" * 16_000)  # t=2s
    r.agent(b"\x10\x00" * 24_000)  # 1 s, starts at t=2s
    with wave.open(io.BytesIO(r.wav())) as w:
        assert (w.getnchannels(), w.getframerate()) == (2, 8000)
        assert abs(w.getnframes() - 3 * 8000) <= 4
    assert abs(r.seconds - 2.0) < 0.01


def test_clean_transcript_strips_markup_and_stray_scripts():
    from voa_gateway.session import clean_transcript

    stray = "date of birth?িতার"  # Bengali letters + vowel signs from a real call
    assert clean_transcript(stray, latin_only=True) == "date of birth?"
    assert clean_transcript("Ça va, José — 3/15/1950 <no speech>{pause}", latin_only=True) == (
        "Ça va, José — 3/15/1950"
    )
    assert clean_transcript("Café", latin_only=True) == "Café"  # decomposed accent kept
    assert clean_transcript(stray) == stray  # non-English campaigns keep every script
