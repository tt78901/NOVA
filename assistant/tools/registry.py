"""Tool registry: decorate a function, get an Ollama-compatible schema."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., str]
    required: list[str] = field(default_factory=list)
    # Name of a feature that must be enabled in config for this tool to be
    # offered at all — "web", "applescript". Empty means always available.
    requires: str = ""

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": self.required,
                },
            },
        }


REGISTRY: dict[str, Tool] = {}


def tool(
    description: str,
    parameters: dict[str, Any] | None = None,
    required: list[str] | None = None,
    requires: str = "",
):
    def decorator(fn: Callable[..., str]) -> Callable[..., str]:
        REGISTRY[fn.__name__] = Tool(
            name=fn.__name__,
            description=description,
            parameters=parameters or {},
            fn=fn,
            required=required or [],
            requires=requires,
        )
        return fn

    return decorator


def string(desc: str) -> dict[str, Any]:
    return {"type": "string", "description": desc}


def integer(desc: str) -> dict[str, Any]:
    return {"type": "integer", "description": desc}


def boolean(desc: str) -> dict[str, Any]:
    return {"type": "boolean", "description": desc}


def enum(desc: str, values: list[str]) -> dict[str, Any]:
    return {"type": "string", "description": desc, "enum": values}


# -- shell helpers --------------------------------------------------------


def run(cmd: list[str], timeout: int = 20) -> str:
    """Run a command and return stdout, or a readable error string."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"Error: `{cmd[0]}` timed out after {timeout}s."
    except FileNotFoundError:
        return f"Error: `{cmd[0]}` is not installed."
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return f"Error: {err or out or f'exit code {proc.returncode}'}"
    return out


def osascript(script: str, timeout: int = 20) -> str:
    return run(["osascript", "-e", script], timeout=timeout)


def quote_applescript(value: str) -> str:
    """Wrap a Python string as an AppleScript string literal, safely."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def schemas(features: set[str] | None = None) -> list[dict[str, Any]]:
    features = features or set()
    return [
        t.schema()
        for t in REGISTRY.values()
        if not t.requires or t.requires in features
    ]


def dispatch(
    name: str, arguments: dict[str, Any], features: set[str] | None = None
) -> str:
    features = features or set()
    entry = REGISTRY.get(name)
    if entry is None:
        return f"Error: no such tool `{name}`."
    if entry.requires and entry.requires not in features:
        return f"Error: `{name}` is disabled in config.toml."
    try:
        result = entry.fn(**(arguments or {}))
    except TypeError as exc:
        return f"Error: bad arguments for `{name}`: {exc}"
    except Exception as exc:  # tools must never crash the assistant
        return f"Error running `{name}`: {exc}"
    text = str(result).strip()
    return text or "Done."
