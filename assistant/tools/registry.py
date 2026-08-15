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
    # Gated tools only load when explicitly enabled in config.
    gated: bool = False

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
    gated: bool = False,
):
    def decorator(fn: Callable[..., str]) -> Callable[..., str]:
        REGISTRY[fn.__name__] = Tool(
            name=fn.__name__,
            description=description,
            parameters=parameters or {},
            fn=fn,
            required=required or [],
            gated=gated,
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


def schemas(allow_gated: bool = False) -> list[dict[str, Any]]:
    return [t.schema() for t in REGISTRY.values() if allow_gated or not t.gated]


def dispatch(name: str, arguments: dict[str, Any], allow_gated: bool = False) -> str:
    entry = REGISTRY.get(name)
    if entry is None:
        return f"Error: no such tool `{name}`."
    if entry.gated and not allow_gated:
        return f"Error: `{name}` is disabled in config.toml."
    try:
        result = entry.fn(**(arguments or {}))
    except TypeError as exc:
        return f"Error: bad arguments for `{name}`: {exc}"
    except Exception as exc:  # tools must never crash the assistant
        return f"Error running `{name}`: {exc}"
    text = str(result).strip()
    return text or "Done."
