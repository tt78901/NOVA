"""Wake-word detection, with two interchangeable backends.

whisper
    Works with *any* wake phrase, including a custom name like "hey nova".
    Silence costs nothing because a speech gate runs first; only actual speech
    is sent to a tiny Whisper model, which is then matched against the phrase.

openwakeword
    Tiny always-on neural net, ~0% CPU. Only recognises the phrases it ships
    pretrained models for: alexa, hey_mycroft, hey_jarvis, hey_rhasspy.

The whisper backend has a bonus: because it transcribes the whole segment, it
can hand back whatever you said *after* the wake phrase. That makes
"hey nova, what time is it" work in a single breath.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from difflib import SequenceMatcher

import numpy as np

from ..config import AudioConfig, SttConfig, WakeConfig
from .stt import Transcriber
from .vad import Endpointer, SpeechGate

WAKE_STT_MODEL = "mlx-community/whisper-tiny.en-mlx"


@dataclass
class WakeResult:
    """Fired when the wake phrase is heard."""

    command: str = ""  # anything said after the phrase, if we caught it


def normalise(text: str) -> list[str]:
    text = text.lower().translate(str.maketrans("", "", string.punctuation))
    return [t for t in re.split(r"\s+", text) if t]


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


class WhisperWake:
    def __init__(self, wake: WakeConfig, audio: AudioConfig, stt: SttConfig):
        self.cfg = wake
        self.phrase_tokens = normalise(wake.phrase)
        self.name_token = self.phrase_tokens[-1] if self.phrase_tokens else ""
        self.transcriber = Transcriber(stt, model=WAKE_STT_MODEL)
        # Short segments: we only need enough to catch "hey nova ...".
        self.endpointer = Endpointer(
            samplerate=audio.samplerate,
            frame_ms=audio.frame_ms,
            silence_ms=500,
            max_ms=6000,
            min_ms=250,
            gate=SpeechGate(),
            lead_in_ms=240,
        )

    def warm(self) -> None:
        self.transcriber.warm()

    def reset(self) -> None:
        self.endpointer.reset()

    def feed(self, frame: np.ndarray) -> WakeResult | None:
        segment = self.endpointer.feed(frame)
        if segment is None or segment.reason == "no_speech":
            return None
        text = self.transcriber.transcribe(segment.audio)
        if not text:
            return None
        return self.match(text)

    def match(self, text: str) -> WakeResult | None:
        """Look for the wake phrase and return whatever follows it."""
        tokens = normalise(text)
        if not tokens or not self.phrase_tokens:
            return None

        span = len(self.phrase_tokens)
        phrase = " ".join(self.phrase_tokens)
        best_score, best_end = 0.0, -1

        # Slide a phrase-sized window; also try the bare name on its own, since
        # people drop the "hey" once they're used to it.
        for i in range(len(tokens)):
            window = " ".join(tokens[i : i + span])
            score = _similar(window, phrase)
            if score > best_score:
                best_score, best_end = score, i + span
            if self.name_token:
                score = _similar(tokens[i], self.name_token)
                if score >= 0.85 and score > best_score:
                    best_score, best_end = score, i + 1

        if best_score < 0.78:
            return None
        return WakeResult(command=" ".join(tokens[best_end:]).strip())


class OpenWakeWord:
    def __init__(self, wake: WakeConfig, audio: AudioConfig):
        import openwakeword
        from openwakeword.model import Model

        openwakeword.utils.download_models([wake.model])
        self.cfg = wake
        self.model = Model(wakeword_models=[wake.model], inference_framework="onnx")

    def warm(self) -> None:
        pass

    def reset(self) -> None:
        self.model.reset()

    def feed(self, frame: np.ndarray) -> WakeResult | None:
        scores = self.model.predict(frame)
        if max(scores.values(), default=0.0) >= self.cfg.threshold:
            self.model.reset()
            return WakeResult()
        return None


def build(wake: WakeConfig, audio: AudioConfig, stt: SttConfig):
    if wake.backend == "openwakeword":
        return OpenWakeWord(wake, audio)
    if wake.backend == "whisper":
        return WhisperWake(wake, audio, stt)
    raise ValueError(f"unknown wake backend: {wake.backend}")
