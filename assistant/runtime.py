"""The main loop.

    idle ──wake word──> listening ──silence──> thinking ──> speaking ──┐
     ▲                                                                 │
     └──────────────── follow-up window expires ───────────────────────┘

After Nova answers she keeps listening for a few seconds, so you can say
"and open Safari" without repeating the wake word.
"""

from __future__ import annotations

import subprocess
import sys
import time

from .audio import wake as wake_mod
from .audio.mic import Microphone
from .audio.stt import Transcriber
from .audio.tts import Speaker
from .audio.vad import Endpointer, SpeechGate
from .brain.llm import Brain
from .config import Config
from .tools import mac  # noqa: F401  (importing registers the tools)

CHIME_LISTEN = "/System/Library/Sounds/Pop.aiff"
CHIME_DONE = "/System/Library/Sounds/Tink.aiff"

DIM, BOLD, CYAN, GREEN, YELLOW, RESET = (
    "\033[2m",
    "\033[1m",
    "\033[36m",
    "\033[32m",
    "\033[33m",
    "\033[0m",
)


def chime(path: str) -> None:
    try:
        subprocess.Popen(
            ["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except OSError:
        pass


def status(text: str, colour: str = DIM) -> None:
    print(f"{colour}{text}{RESET}", flush=True)


class Assistant:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.brain = Brain(cfg.llm, cfg.name, cfg.allow_applescript)
        self.speaker = Speaker(cfg.tts)
        self.transcriber = Transcriber(cfg.stt)
        self.mic = Microphone(cfg.audio)
        self._wake = None

    # -- setup -------------------------------------------------------------

    @property
    def wake(self):
        if self._wake is None:
            self._wake = wake_mod.build(self.cfg.wake, self.cfg.audio, self.cfg.stt)
        return self._wake

    def warm(self, voice: bool = True) -> None:
        """Load models up front so the first request isn't noticeably slower."""
        status("Loading speech recognition…")
        self.transcriber.warm()
        if voice:
            status("Loading wake word…")
            self.wake.warm()
        status("Waking the language model…")
        try:
            self.brain.client.generate(
                model=self.cfg.llm.model, prompt="hi", options={"num_predict": 1}
            )
        except Exception as exc:
            status(f"Ollama isn't reachable: {exc}", YELLOW)

    # -- one exchange ------------------------------------------------------

    def respond(self, text: str) -> str:
        print(f"{BOLD}{CYAN}you{RESET}  {text}")
        print(f"{BOLD}{GREEN}{self.cfg.name.lower()}{RESET}  ", end="", flush=True)

        chunks = self.brain.ask(text)
        if self.cfg.tts.enabled:
            self.mic.mute()
            reply = self.speaker.speak_stream(_echo(chunks))
            self.speaker.wait()
            self.mic.unmute()
        else:
            reply = "".join(_echo(chunks))
        print()
        return reply

    # -- listening ---------------------------------------------------------

    def _listen(self, wait_for_speech: float) -> str:
        """Record one utterance and transcribe it. Empty string if silent."""
        endpointer = Endpointer(
            samplerate=self.cfg.audio.samplerate,
            frame_ms=self.cfg.audio.frame_ms,
            silence_ms=self.cfg.audio.silence_ms,
            max_ms=self.cfg.audio.max_utterance_ms,
            min_ms=self.cfg.audio.min_utterance_ms,
            gate=SpeechGate(),
        )
        deadline = time.monotonic() + wait_for_speech
        utterance = None

        while True:
            frame = self.mic.read(timeout=0.5)
            if frame is None:
                if time.monotonic() > deadline:
                    utterance = endpointer.timeout()
                    break
                continue
            utterance = endpointer.feed(frame)
            if utterance is not None:
                break
            if not endpointer.heard_speech and time.monotonic() > deadline:
                utterance = endpointer.timeout()
                break

        if utterance is None or utterance.reason == "no_speech":
            return ""
        status("  thinking…")
        return self.transcriber.transcribe(utterance.audio)

    # -- loops -------------------------------------------------------------

    def run_voice(self) -> None:
        self.warm(voice=True)
        phrase = self.cfg.wake.phrase
        status(f"\nListening for “{phrase}”. Ctrl-C to quit.\n", BOLD)

        with self.mic:
            while True:
                event = self._await_wake()
                if event is None:
                    continue

                chime(CHIME_LISTEN)
                command = event.command

                # Stay in conversation until the follow-up window lapses.
                while True:
                    if not command:
                        status("  listening…")
                        command = self._listen(wait_for_speech=6.0)
                    if not command:
                        break
                    self.respond(command)
                    command = ""
                    if self.cfg.wake.follow_up_seconds <= 0:
                        break
                    command = self._listen(
                        wait_for_speech=self.cfg.wake.follow_up_seconds
                    )
                    if not command:
                        break

                chime(CHIME_DONE)
                self.wake.reset()
                status(f"\nListening for “{phrase}”…\n")

    def _await_wake(self):
        while True:
            frame = self.mic.read(timeout=0.5)
            if frame is None:
                continue
            result = self.wake.feed(frame)
            if result is not None:
                return result

    def run_text(self) -> None:
        self.warm(voice=False)
        status(f"\nType to {self.cfg.name}. Ctrl-C or 'exit' to quit.\n", BOLD)
        while True:
            try:
                text = input(f"{BOLD}{CYAN}you{RESET}  ").strip()
            except EOFError:
                return
            if text.lower() in {"exit", "quit"}:
                return
            if not text:
                continue
            print(f"{BOLD}{GREEN}{self.cfg.name.lower()}{RESET}  ", end="", flush=True)
            chunks = self.brain.ask(text)
            if self.cfg.tts.enabled:
                self.speaker.speak_stream(_echo(chunks))
                self.speaker.wait()
            else:
                "".join(_echo(chunks))
            print("\n")

    def shutdown(self) -> None:
        self.speaker.shutdown()
        self.mic.stop()


def _echo(chunks):
    """Pass chunks through to the terminal on their way to the speaker."""
    for chunk in chunks:
        sys.stdout.write(chunk)
        sys.stdout.flush()
        yield chunk
