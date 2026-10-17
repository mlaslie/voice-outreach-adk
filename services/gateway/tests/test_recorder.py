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
