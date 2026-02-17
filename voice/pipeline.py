"""Voice pipeline — Whisper STT + edge-tts TTS."""

from __future__ import annotations

import io
import logging
from functools import partial
from typing import TYPE_CHECKING

import edge_tts
from faster_whisper import WhisperModel

if TYPE_CHECKING:
    import asyncio

log = logging.getLogger(__name__)


class VoicePipeline:
    """Speech-to-text via faster-whisper, text-to-speech via edge-tts."""

    def __init__(
        self,
        stt_model: str = "base",
        tts_voice: str = "en-US-AvaNeural",
        tts_enabled: bool = True,
    ):
        self.tts_voice = tts_voice
        self.tts_enabled = tts_enabled

        log.info("Loading Whisper model '%s' …", stt_model)
        self._whisper = WhisperModel(stt_model, compute_type="int8")
        log.info("Whisper model loaded.")

    # -- STT ----------------------------------------------------------------

    async def transcribe(
        self, audio_bytes: bytes, *, loop: asyncio.AbstractEventLoop | None = None
    ) -> str:
        """Transcribe audio bytes (OGG/WAV/MP3) to text.

        faster-whisper is synchronous and CPU-bound, so we run it in the
        default executor to avoid blocking the event loop.
        """
        import asyncio as _asyncio

        _loop = loop or _asyncio.get_running_loop()
        return await _loop.run_in_executor(None, partial(self._transcribe_sync, audio_bytes))

    def _transcribe_sync(self, audio_bytes: bytes) -> str:
        segments, info = self._whisper.transcribe(io.BytesIO(audio_bytes))
        text = " ".join(seg.text.strip() for seg in segments)
        log.info(
            "Transcribed %s audio → %d chars (lang=%s)", info.language, len(text), info.language
        )
        return text

    # -- TTS ----------------------------------------------------------------

    async def synthesize(self, text: str) -> bytes:
        """Convert text to speech using edge-tts.  Returns raw MP3 bytes."""
        if not self.tts_enabled:
            raise RuntimeError("TTS is disabled in config")

        communicate = edge_tts.Communicate(text, self.tts_voice)
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])

        audio = buf.getvalue()
        log.info("Synthesized %d chars → %d bytes audio", len(text), len(audio))
        return audio
