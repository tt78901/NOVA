"""The reasoning loop: Ollama, streamed, with tool calling."""

from __future__ import annotations

import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterator

from ollama import Client

from ..config import LlmConfig
from ..tools import registry
from .prompts import system_prompt

# Where the server binary tends to live. The .app bundle is checked too,
# because installing Ollama by download rather than package manager leaves
# nothing on PATH.
OLLAMA_BINARIES = [
    Path.home() / ".local/bin/ollama",
    Path("/Applications/Ollama.app/Contents/Resources/ollama"),
    Path("/usr/local/bin/ollama"),
    Path("/opt/homebrew/bin/ollama"),
]


def server_running(host: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"{host}/api/version", timeout=timeout):
            return True
    except (urllib.error.URLError, OSError):
        return False


def find_ollama() -> Path | None:
    found = shutil.which("ollama")
    if found:
        return Path(found)
    return next((p for p in OLLAMA_BINARIES if p.exists()), None)


def ensure_server(host: str, wait: float = 20.0) -> tuple[bool, str]:
    """Start `ollama serve` if nothing is listening yet.

    Returns (running, message). The server is detached from this process so it
    outlives the assistant, which is what you want for a background daemon.
    """
    if server_running(host):
        return True, "already running"

    binary = find_ollama()
    if binary is None:
        return False, (
            "Ollama isn't installed. Get it from https://ollama.com/download"
        )

    try:
        subprocess.Popen(
            [str(binary), "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # survives Nova exiting
        )
    except OSError as exc:
        return False, f"couldn't launch {binary}: {exc}"

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if server_running(host, timeout=1.0):
            return True, f"started {binary}"
        time.sleep(0.5)
    return False, f"{binary} did not come up within {wait:.0f}s"


class Brain:
    def __init__(self, cfg: LlmConfig, name: str, allow_applescript: bool = False):
        self.cfg = cfg
        self.name = name
        self.allow_gated = allow_applescript
        self.client = Client(host=cfg.host)
        self.messages: list[dict[str, Any]] = []
        self._supports_think = cfg.think
        # Optional observer, so the UI can show tool activity as it happens.
        self.on_tool: Callable[[str, str], None] | None = None

    # -- session state -----------------------------------------------------

    def reset(self) -> None:
        self.messages.clear()

    def _trim(self) -> None:
        """Keep the last N user turns, dropping whole turns at a time so a
        tool result never outlives the assistant message that requested it."""
        starts = [i for i, m in enumerate(self.messages) if m.get("role") == "user"]
        if len(starts) > self.cfg.max_history_turns:
            cut = starts[-self.cfg.max_history_turns]
            self.messages = self.messages[cut:]

    # -- inference ---------------------------------------------------------

    def _chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]):
        kwargs: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "tools": tools,
            "stream": True,
            "options": {
                "temperature": self.cfg.temperature,
                "num_ctx": self.cfg.context_tokens,
            },
        }
        if self._supports_think is not None:
            kwargs["think"] = self._supports_think
        try:
            return self.client.chat(**kwargs)
        except Exception as exc:
            # Models that have no reasoning mode reject `think` outright.
            if "think" in str(exc).lower() and "think" in kwargs:
                self._supports_think = None
                kwargs.pop("think")
                return self.client.chat(**kwargs)
            raise

    def ask(self, text: str) -> Iterator[str]:
        """Yield the spoken reply in chunks, running any tools along the way."""
        self.messages.append({"role": "user", "content": text})
        self._trim()

        tools = registry.schemas(allow_gated=self.allow_gated)
        system = {
            "role": "system",
            "content": system_prompt(self.name, self.allow_gated),
        }

        for _ in range(self.cfg.max_tool_iterations):
            content: list[str] = []
            calls: list[Any] = []

            try:
                for chunk in self._chat([system] + self.messages, tools):
                    message = chunk.message
                    if message.tool_calls:
                        calls.extend(message.tool_calls)
                    if message.content:
                        content.append(message.content)
                        yield message.content
            except Exception as exc:
                yield f"I couldn't reach the language model. {exc}"
                return

            assistant: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(content),
            }
            if calls:
                assistant["tool_calls"] = [
                    {
                        "function": {
                            "name": c.function.name,
                            "arguments": dict(c.function.arguments or {}),
                        }
                    }
                    for c in calls
                ]
            self.messages.append(assistant)

            if not calls:
                return

            for call in calls:
                name = call.function.name
                args = dict(call.function.arguments or {})
                result = registry.dispatch(name, args, allow_gated=self.allow_gated)
                if self.on_tool is not None:
                    self.on_tool(name, result)
                self.messages.append(
                    {"role": "tool", "tool_name": name, "content": result}
                )

        yield "I got stuck working through that one."

    def ask_text(self, text: str) -> str:
        return "".join(self.ask(text)).strip()
