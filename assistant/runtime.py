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
import webbrowser

from .audio import vocab
from .audio import wake as wake_mod
from .audio.mic import Microphone
from .audio.stt import Transcriber
from .audio.tts import build_speaker
from .audio.vad import Endpointer, SpeechGate, rms
from .brain.llm import Brain, ensure_server
from .config import Config
from .tools import mac  # noqa: F401  (importing registers the tools)
from .ui.server import EventBus, UIServer

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
        features = set()
        if cfg.allow_applescript:
            features.add("applescript")
        if cfg.web.enabled:
            features.add("web")
        self.brain = Brain(cfg.llm, cfg.name, features)
        self.speaker = build_speaker(cfg.tts)
        prompt = vocab.bias_prompt(cfg.name) if cfg.stt.bias else None
        self.transcriber = Transcriber(cfg.stt, prompt=prompt)
        self.mic = Microphone(cfg.audio)
        self._wake = None

        self.bus = EventBus() if cfg.ui.enabled else None
        self.ui = UIServer(self.bus, cfg.ui.port) if self.bus else None
        if self.bus:
            self.brain.on_tool = lambda name, result: self.bus.publish(
                "tool", name=name, result=result
            )

    # -- ui ----------------------------------------------------------------

    def _publish(self, kind: str, **data) -> None:
        if self.bus is not None:
            self.bus.publish(kind, **data)

    def _state(self, value: str) -> None:
        self._publish("state", value=value)

    def _emit_level(self, frame) -> None:
        if self.bus is not None:
            # Scaled so ordinary speech lands near the top of the ring.
            self._publish("level", value=min(1.0, rms(frame) * 12))

    def _start_ui(self) -> None:
        if self.ui is None:
            return
        try:
            url = self.ui.start()
        except OSError as exc:
            status(f"HUD couldn't start on port {self.cfg.ui.port}: {exc}", YELLOW)
            self.ui = None
            return
        status(f"HUD at {url}", BOLD)
        self._publish(
            "info",
            name=self.cfg.name,
            model=self.cfg.llm.model,
            wake=self.cfg.wake.phrase,
        )
        if self.cfg.ui.open_browser:
            webbrowser.open(url)

    # -- setup -------------------------------------------------------------

    @property
    def wake(self):
        if self._wake is None:
            self._wake = wake_mod.build(self.cfg.wake, self.cfg.audio, self.cfg.stt)
        return self._wake

    def warm(self, voice: bool = True) -> None:
        """Load models up front so the first request isn't noticeably slower.

        The Ollama check comes first and is fatal: without it every reply
        would be "I couldn't reach the language model", which is a slow and
        confusing way to discover the server is down.
        """
        running, detail = ensure_server(self.cfg.llm.host)
        if not running:
            raise SystemExit(
                f"{YELLOW}Can't reach Ollama at {self.cfg.llm.host} — {detail}.\n"
                f"Start it yourself with:  ollama serve{RESET}"
            )
        if detail != "already running":
            status(f"Ollama {detail}.")

        self._start_ui()
        status("Loading speech recognition…")
        self.transcriber.warm()
        if self.cfg.tts.enabled:
            status("Loading voice…")
            self.speaker.warm()
        if voice:
            status("Loading wake word…")
            self.wake.warm()

        status("Waking the language model…")
        try:
            self.brain.client.generate(
                model=self.cfg.llm.model, prompt="hi", options={"num_predict": 1}
            )
        except Exception as exc:
            status(f"Couldn't preload {self.cfg.llm.model}: {exc}", YELLOW)
            status(f"Pull it with:  ollama pull {self.cfg.llm.model}", YELLOW)

    # -- one exchange ------------------------------------------------------

    def respond(self, text: str, echo_user: bool = True) -> str:
        if echo_user:
            print(f"{BOLD}{CYAN}you{RESET}  {text}")
        self._publish("user", text=text)
        self._state("thinking")
        print(f"{BOLD}{GREEN}{self.cfg.name.lower()}{RESET}  ", end="", flush=True)

        stream = self._stream(self.brain.ask(text))
        if self.cfg.tts.enabled:
            self.mic.mute()
            reply = self.speaker.speak_stream(stream)
            self.speaker.wait()
            self.mic.unmute()
        else:
            reply = "".join(stream)
        print()
        return reply

    def _stream(self, chunks):
        """Echo chunks to the terminal and HUD on their way to the speaker."""
        name = self.cfg.name.lower()
        first = True
        for chunk in chunks:
            if first:
                self._state("speaking")
                first = False
            sys.stdout.write(chunk)
            sys.stdout.flush()
            self._publish("delta", text=chunk, name=name)
            yield chunk

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
            self._emit_level(frame)
            utterance = endpointer.feed(frame)
            if utterance is not None:
                break
            if not endpointer.heard_speech and time.monotonic() > deadline:
                utterance = endpointer.timeout()
                break

        if utterance is None or utterance.reason == "no_speech":
            return ""
        status("  thinking…")
        self._state("thinking")
        return self.transcriber.transcribe(utterance.audio)

    # -- loops -------------------------------------------------------------

    def run_voice(self) -> None:
        self.warm(voice=True)
        phrase = self.cfg.wake.phrase
        status(f"\nListening for “{phrase}”. Ctrl-C to quit.\n", BOLD)

        with self.mic:
            while True:
                self._state("idle")
                event = self._await_wake()
                if event is None:
                    continue

                chime(CHIME_LISTEN)
                self._state("listening")
                command = event.command

                # Stay in conversation until the follow-up window lapses.
                while True:
                    if not command:
                        status("  listening…")
                        self._state("listening")
                        command = self._listen(wait_for_speech=6.0)
                    if not command:
                        break
                    self.respond(command)
                    command = ""
                    if self.cfg.wake.follow_up_seconds <= 0:
                        break
                    self._state("listening")
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
            self._emit_level(frame)
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
            self.respond(text, echo_user=False)
            self._state("idle")
            print()

    def shutdown(self) -> None:
        self.speaker.shutdown()
        self.mic.stop()
        if self.ui is not None:
            self.ui.stop()
