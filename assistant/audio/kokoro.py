"""Neural speech via Kokoro, running on the Apple Silicon GPU through mlx-audio.

Roughly thirty times faster than real time once warm, so a sentence is
synthesised in under a tenth of a second and sentence-by-sentence streaming
still feels immediate.

Voices are named `<accent><gender>_<name>`: `a` American, `b` British.
American female  af_heart af_nova af_bella af_sarah af_sky af_aoede af_kore
American male    am_michael am_puck am_adam am_echo am_eric am_liam am_onyx
British female   bf_emma bf_alice bf_isabella bf_lily
British male     bm_george bm_fable bm_daniel bm_lewis
"""

from __future__ import annotations

import numpy as np
import sounddevice as sd

from ..config import TtsConfig

DEFAULT_MODEL = "mlx-community/Kokoro-82M-bf16"
DEFAULT_VOICE = "af_heart"
from .tts import QueuedSpeaker  # noqa: E402  (circular-safe: tts imports us lazily)


class KokoroSpeaker(QueuedSpeaker):
    def __init__(self, cfg: TtsConfig):
        self.voice = cfg.voice or DEFAULT_VOICE
        self.model_id = cfg.model or DEFAULT_MODEL
        # Accent is encoded in the first letter of the voice name.
        self.lang_code = "b" if self.voice.startswith("b") else "a"
        self._model = None
        super().__init__(cfg)

    def _load(self):
        if self._model is None:
            from mlx_audio.tts.utils import load_model

            self._model = load_model(self.model_id)
        return self._model

    def warm(self) -> None:
        """Load weights and build the phoneme pipeline ahead of first use.

        Worth doing explicitly: the very first call also downloads the voice
        and constructs the g2p pipeline, which together take a few seconds.
        """
        self._synthesise("Ready.")

    def _synthesise(self, text: str) -> tuple[np.ndarray, int]:
        model = self._load()
        chunks, rate = [], 24000
        for result in model.generate(
            text=text,
            voice=self.voice,
            speed=self.cfg.speed,
            lang_code=self.lang_code,
        ):
            chunks.append(np.asarray(result.audio, dtype=np.float32).reshape(-1))
            rate = result.sample_rate
        if not chunks:
            return np.zeros(0, dtype=np.float32), rate
        return np.concatenate(chunks), rate

    def _render(self, text: str) -> None:
        audio, rate = self._synthesise(text)
        if audio.size == 0 or self._stopped:
            return
        sd.play(audio, samplerate=rate)
        sd.wait()

    def _interrupt(self) -> None:
        sd.stop()
