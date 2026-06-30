"""Kokoro TTS backend: lang mapping, WAV encoding, fallback dispatch (#84).

Builds a bare VoicePipeline via ``object.__new__`` so we never load Whisper or
the ONNX model — only the new branch logic is under test.
"""

import asyncio
import wave

import numpy as np

from voice.pipeline import VoicePipeline, _lang_for_voice, _pcm_to_wav


def test_lang_for_voice_prefix():
    assert _lang_for_voice("af_bella") == "en-us"
    assert _lang_for_voice("bm_george") == "en-gb"
    assert _lang_for_voice("jf_alpha") == "ja"
    assert _lang_for_voice("ff_siwis") == "fr-fr"
    assert _lang_for_voice("xx_unknown") == "en-us"  # default


def test_pcm_to_wav_roundtrip():
    samples = np.array([0.0, 1.0, -1.0, 2.0, -2.0], dtype=np.float32)  # last two clip
    data = _pcm_to_wav(samples, 24000)
    assert data[:4] == b"RIFF"
    import io

    with wave.open(io.BytesIO(data), "rb") as w:
        assert w.getframerate() == 24000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        frames = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    assert frames[1] == 32767 and frames[2] == -32767  # clipped to int16 range
    assert frames[3] == 32767 and frames[4] == -32767


def _bare_pipeline():
    p = object.__new__(VoicePipeline)
    p.tts_enabled = True
    p.tts_voice = "en-US-AvaNeural"
    p.kokoro_default_voice = "af_bella"
    p._kokoro = None
    return p


class _FakeKokoro:
    def __init__(self, raise_it=False):
        self.raise_it = raise_it
        self.calls = []

    def create(self, text, voice, speed, lang):
        self.calls.append((text, voice, lang))
        if self.raise_it:
            raise RuntimeError("onnx boom")
        return np.zeros(10, dtype=np.float32), 24000


def test_synthesize_uses_kokoro_when_loaded():
    p = _bare_pipeline()
    p._kokoro = _FakeKokoro()
    out = asyncio.run(p.synthesize("hello", voice="jf_alpha"))
    assert out[:4] == b"RIFF"
    assert p._kokoro.calls == [("hello", "jf_alpha", "ja")]


def test_synthesize_falls_back_to_edge_on_kokoro_error(monkeypatch):
    p = _bare_pipeline()
    p._kokoro = _FakeKokoro(raise_it=True)

    async def fake_edge(text, voice):
        return b"EDGE-AUDIO"

    monkeypatch.setattr(p, "_synthesize_edge", fake_edge)
    out = asyncio.run(p.synthesize("hello", voice="af_bella"))
    assert out == b"EDGE-AUDIO"  # kokoro raised → edge fallback


def test_synthesize_default_voice_when_none():
    p = _bare_pipeline()
    p._kokoro = _FakeKokoro()
    asyncio.run(p.synthesize("hi", voice=None))
    assert p._kokoro.calls[0][1] == "af_bella"  # fell back to kokoro_default_voice
