"""Configuration loading. Reads config.toml from the project root, falling back
to the defaults below for any key the user hasn't set."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"


@dataclass
class WakeConfig:
    backend: str = "whisper"  # "whisper" | "openwakeword"
    model: str = "hey_jarvis"  # openwakeword only: alexa | hey_mycroft | hey_jarvis | hey_rhasspy
    phrase: str = "hey nova"  # whisper backend: any phrase you like
    threshold: float = 0.5
    # After answering, listen this long for a follow-up without the wake word.
    follow_up_seconds: float = 8.0


@dataclass
class AudioConfig:
    input_device: str = ""  # empty string = system default
    samplerate: int = 16000
    frame_ms: int = 80  # openWakeWord wants 80 ms frames at 16 kHz
    silence_ms: int = 900  # trailing silence that ends an utterance
    max_utterance_ms: int = 15000
    min_utterance_ms: int = 400  # shorter than this is treated as noise


@dataclass
class SttConfig:
    model: str = "mlx-community/whisper-large-v3-turbo"
    language: str = "en"


@dataclass
class LlmConfig:
    host: str = "http://127.0.0.1:11434"
    model: str = "qwen3:8b"
    temperature: float = 0.6
    context_tokens: int = 8192
    max_history_turns: int = 8
    think: bool = False  # qwen3-style reasoning; off keeps replies snappy
    max_tool_iterations: int = 5


@dataclass
class TtsConfig:
    voice: str = ""  # empty = auto-pick the best installed voice
    rate: int = 190  # words per minute
    enabled: bool = True


@dataclass
class Config:
    name: str = "Nova"
    # AppleScript is a full scripting escape hatch. Powerful, but it lets the
    # model run arbitrary code on your Mac, so it's opt-in.
    allow_applescript: bool = False
    wake: WakeConfig = field(default_factory=WakeConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    tts: TtsConfig = field(default_factory=TtsConfig)


def _merge(obj: Any, data: dict[str, Any], path: str = "") -> None:
    """Overlay a parsed-TOML dict onto a dataclass instance, in place."""
    known = {f.name: f for f in fields(obj)}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"unknown config key: {path}{key}")
        current = getattr(obj, key)
        if is_dataclass(current) and isinstance(value, dict):
            _merge(current, value, f"{path}{key}.")
        else:
            setattr(obj, key, value)


def load(path: Path | None = None) -> Config:
    cfg = Config()
    path = path or CONFIG_PATH
    if path.exists():
        with path.open("rb") as fh:
            _merge(cfg, tomllib.load(fh))
    return cfg
