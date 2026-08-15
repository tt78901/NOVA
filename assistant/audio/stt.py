"""Speech to text via mlx-whisper, which runs on the Apple Silicon GPU."""

from __future__ import annotations

import numpy as np

from ..config import SttConfig

# Whisper hallucinates these on silence or breath noise. Drop them.
_JUNK = {
    "",
    ".",
    "you",
    "thank you.",
    "thanks for watching!",
    "thank you for watching!",
    "bye.",
    "so",
    "[blank_audio]",
    "(upbeat music)",
    "silence",
}


def to_float32(audio: np.ndarray) -> np.ndarray:
    if audio.dtype == np.float32:
        return audio
    return audio.astype(np.float32) / 32768.0


class Transcriber:
    def __init__(self, cfg: SttConfig, model: str | None = None):
        self.cfg = cfg
        self.model = model or cfg.model
        self._mlx = None

    def _backend(self):
        if self._mlx is None:
            import mlx_whisper  # imported lazily; loading it is slow

            self._mlx = mlx_whisper
        return self._mlx

    def warm(self) -> None:
        """Pull weights and JIT the graph so the first real turn isn't slow."""
        self.transcribe(np.zeros(16000, dtype=np.int16))

    def transcribe(self, audio: np.ndarray) -> str:
        if audio.size == 0:
            return ""
        result = self._backend().transcribe(
            to_float32(audio),
            path_or_hf_repo=self.model,
            language=self.cfg.language or None,
            fp16=True,
            condition_on_previous_text=False,
        )
        text = (result.get("text") or "").strip()
        return "" if text.lower() in _JUNK else text
