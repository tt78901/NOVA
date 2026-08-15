"""Energy-based speech detection and utterance endpointing.

Deliberately dependency-free. The noise floor is tracked as a slow EMA of quiet
frames, so the threshold adapts to a humming fridge or a noisy cafe instead of
relying on one fixed number.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def rms(frame: np.ndarray) -> float:
    """Root-mean-square level of an int16 frame, normalised to 0.0-1.0."""
    if frame.size == 0:
        return 0.0
    x = frame.astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(x * x)))


class SpeechGate:
    """Tracks a noise floor and reports whether a frame contains speech."""

    # A frame counts as speech when it is this many times above the floor.
    MARGIN = 3.5
    # ...but never below this, so near-silence can't trip the gate.
    ABSOLUTE_FLOOR = 0.006

    def __init__(self) -> None:
        self.noise = 0.01
        self._warmup = 0

    def threshold(self) -> float:
        return max(self.noise * self.MARGIN, self.ABSOLUTE_FLOOR)

    def update(self, frame: np.ndarray) -> bool:
        level = rms(frame)
        is_speech = level > self.threshold()
        if self._warmup < 8:
            # First ~0.6 s: trust everything as ambience to seed the floor.
            self.noise = 0.7 * self.noise + 0.3 * level
            self._warmup += 1
            return False
        if not is_speech:
            self.noise = 0.95 * self.noise + 0.05 * level
        return is_speech


@dataclass
class Utterance:
    audio: np.ndarray  # int16, mono
    duration_ms: int
    reason: str  # "silence" | "max_length" | "no_speech"


class Endpointer:
    """Accumulates frames and decides when the speaker has finished."""

    def __init__(
        self,
        samplerate: int,
        frame_ms: int,
        silence_ms: int,
        max_ms: int,
        min_ms: int,
        gate: SpeechGate | None = None,
        lead_in_ms: int = 300,
    ):
        self.samplerate = samplerate
        self.frame_ms = frame_ms
        self.silence_frames = max(1, silence_ms // frame_ms)
        self.max_frames = max(1, max_ms // frame_ms)
        self.min_ms = min_ms
        self.lead_in_frames = max(0, lead_in_ms // frame_ms)
        self.gate = gate or SpeechGate()
        self.reset()

    def reset(self) -> None:
        self._frames: list[np.ndarray] = []
        self._lead_in: list[np.ndarray] = []
        self._trailing_silence = 0
        self._heard_speech = False

    @property
    def heard_speech(self) -> bool:
        """True once the speaker has actually started talking."""
        return self._heard_speech

    def feed(self, frame: np.ndarray) -> Utterance | None:
        """Add a frame. Returns an Utterance once the turn is complete."""
        speech = self.gate.update(frame)

        if not self._heard_speech:
            if not speech:
                # Keep a rolling pre-roll so we don't clip the first syllable.
                self._lead_in.append(frame)
                if len(self._lead_in) > self.lead_in_frames:
                    self._lead_in.pop(0)
                return None
            self._heard_speech = True
            self._frames.extend(self._lead_in)
            self._lead_in.clear()

        self._frames.append(frame)
        self._trailing_silence = 0 if speech else self._trailing_silence + 1

        if self._trailing_silence >= self.silence_frames:
            return self._finish("silence")
        if len(self._frames) >= self.max_frames:
            return self._finish("max_length")
        return None

    def timeout(self) -> Utterance:
        """Called when the caller gives up waiting for speech to start."""
        return self._finish("no_speech" if not self._heard_speech else "silence")

    def _finish(self, reason: str) -> Utterance:
        audio = (
            np.concatenate(self._frames)
            if self._frames
            else np.zeros(0, dtype=np.int16)
        )
        duration_ms = int(audio.size / self.samplerate * 1000)
        self.reset()
        if reason != "no_speech" and duration_ms < self.min_ms:
            reason = "no_speech"
        return Utterance(audio=audio, duration_ms=duration_ms, reason=reason)
