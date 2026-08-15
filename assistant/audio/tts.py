"""Text to speech.

Two backends behind one interface:

kokoro
    An 82M-parameter neural model on the Apple Silicon GPU, via mlx-audio.
    Synthesises about thirty times faster than real time once warm, so a
    sentence is ready in well under a tenth of a second.

say
    macOS's built-in synthesiser. No download and no dependencies, but it is
    an older generation of synthesis and sounds it.

Either way speech is queued sentence by sentence rather than waiting for the
whole reply, so Nova starts talking as soon as the first sentence exists.
"""

from __future__ import annotations

import queue
import re
import subprocess
import threading
from typing import Iterable, Iterator

from ..config import TtsConfig

# Best first. Premium/Enhanced voices only exist if downloaded in
# System Settings > Accessibility > Spoken Content > System Voice > Manage.
VOICE_PREFERENCE = [
    "Ava (Premium)",
    "Zoe (Premium)",
    "Evan (Premium)",
    "Allison (Premium)",
    "Samantha (Enhanced)",
    "Ava (Enhanced)",
    "Samantha",
    "Alex",
    "Karen",
    "Daniel",
]

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n{2,}")
# Strip things that sound wrong read aloud: code fences, markdown emphasis.
_CODE_FENCE = re.compile(r"```.*?```", re.S)
_MARKDOWN = re.compile(r"[*_`#>]+")
# `say` treats [[...]] as inline synthesiser commands. Model output must never
# be able to smuggle those in, so they're stripped from anything we speak.
_SPEECH_COMMAND = re.compile(r"\[\[|\]\]")


def available_voices() -> list[str]:
    try:
        out = subprocess.run(
            ["say", "-v", "?"], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    voices = []
    for line in out.splitlines():
        # "Samantha            en_US    # Hello! My name is Samantha."
        parts = re.split(r"\s{2,}", line.strip())
        if parts and parts[0]:
            voices.append(parts[0])
    return voices


def pick_voice(preferred: str = "") -> str:
    voices = available_voices()
    if preferred and preferred in voices:
        return preferred
    for candidate in VOICE_PREFERENCE:
        if candidate in voices:
            return candidate
    return voices[0] if voices else "Samantha"


def clean_for_speech(text: str) -> str:
    text = _CODE_FENCE.sub(" (code omitted) ", text)
    text = _MARKDOWN.sub("", text)
    text = _SPEECH_COMMAND.sub("", text)
    return text.strip()


def split_sentences(text: str) -> Iterator[str]:
    for part in _SENTENCE_END.split(text):
        if part.strip():
            yield part


class QueuedSpeaker:
    """Queue plus worker thread. Subclasses only implement `_render`."""

    def __init__(self, cfg: TtsConfig):
        self.cfg = cfg
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._stopped = False
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    # -- subclass hooks ----------------------------------------------------

    def _render(self, text: str) -> None:
        """Speak `text`, blocking until done."""
        raise NotImplementedError

    def _interrupt(self) -> None:
        """Cut off whatever is sounding right now."""

    def warm(self) -> None:
        """Optional: preload models so the first reply isn't slow."""

    # -- worker ------------------------------------------------------------

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            if self._stopped or not item.strip():
                self._mark_idle()
                continue
            try:
                self._render(item)
            except Exception:
                pass  # a broken voice must never take down the assistant
            finally:
                self._mark_idle()

    def _mark_idle(self) -> None:
        if self._queue.empty():
            self._idle.set()

    # -- api ---------------------------------------------------------------

    def enqueue(self, text: str) -> None:
        text = clean_for_speech(text)
        if not text or not self.cfg.enabled:
            return
        self._stopped = False
        self._idle.clear()
        self._queue.put(text)

    def say(self, text: str) -> None:
        for sentence in split_sentences(text):
            self.enqueue(sentence)
        self.wait()

    def speak_stream(self, chunks: Iterable[str]) -> str:
        """Consume a token stream, speaking each sentence as it completes."""
        buffer, spoken = "", []
        for chunk in chunks:
            buffer += chunk
            *ready, buffer = _SENTENCE_END.split(buffer)
            for sentence in ready:
                if sentence.strip():
                    self.enqueue(sentence)
                    spoken.append(sentence)
        if buffer.strip():
            self.enqueue(buffer)
            spoken.append(buffer)
        return " ".join(spoken)

    def wait(self, timeout: float | None = None) -> None:
        self._idle.wait(timeout)

    @property
    def speaking(self) -> bool:
        return not self._idle.is_set()

    def stop(self) -> None:
        self._stopped = True
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._interrupt()
        self._idle.set()

    def shutdown(self) -> None:
        self.stop()
        self._queue.put(None)


class SaySpeaker(QueuedSpeaker):
    """macOS `say`."""

    def __init__(self, cfg: TtsConfig):
        self.voice = pick_voice(cfg.voice)
        self.prefix = _speech_prefix(cfg)
        self._proc: subprocess.Popen | None = None
        super().__init__(cfg)

    def _render(self, text: str) -> None:
        with self._lock:
            if self._stopped:
                return
            self._proc = subprocess.Popen(
                ["say", "-v", self.voice, "-r", str(self.cfg.rate), self.prefix + text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        try:
            self._proc.wait()
        finally:
            with self._lock:
                self._proc = None

    def _interrupt(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()


def _speech_prefix(cfg: TtsConfig) -> str:
    """Build the [[pbas]]/[[pmod]] preamble that shapes a classic voice."""
    parts = []
    if cfg.pitch:
        parts.append(f"[[pbas {max(0, min(127, cfg.pitch))}]]")
    if cfg.modulation >= 0:
        parts.append(f"[[pmod {max(0, min(127, cfg.modulation))}]]")
    return "".join(parts)


def build_speaker(cfg: TtsConfig) -> QueuedSpeaker:
    if cfg.backend == "kokoro":
        from .kokoro import KokoroSpeaker  # heavy import, only when chosen

        try:
            return KokoroSpeaker(cfg)
        except Exception as exc:
            print(f"[tts] Kokoro unavailable ({exc}); falling back to `say`.")
    return SaySpeaker(cfg)


# Kept so older imports keep working.
Speaker = SaySpeaker
