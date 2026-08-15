"""Mac control tools.

Each returns a short plain-text string, because whatever comes back gets read
aloud or fed straight back into the model. Errors are returned, never raised,
so a failing tool turns into "I couldn't do that" instead of a crash.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from .registry import boolean, enum, integer, osascript, run, string, tool

MEDIA_APPS = ["Music", "Spotify"]


# -- time -----------------------------------------------------------------


@tool(
    "Get the current local date and time. Use this whenever the user asks "
    "about the time, the date, or the day of the week."
)
def get_datetime() -> str:
    now = datetime.now().astimezone()
    return now.strftime("%A, %d %B %Y at %I:%M %p %Z").replace(" 0", " ")


# -- apps -----------------------------------------------------------------


@tool(
    "Open or switch to a Mac application by name, e.g. Safari, Notes, Xcode.",
    {"name": string("The application name.")},
    ["name"],
)
def open_app(name: str) -> str:
    result = run(["open", "-a", name])
    if result.startswith("Error"):
        return f"Couldn't open {name}. {result}"
    return f"Opened {name}."


@tool(
    "Quit a running Mac application by name.",
    {"name": string("The application name.")},
    ["name"],
)
def quit_app(name: str) -> str:
    result = osascript(f'tell application "{name}" to quit')
    if result.startswith("Error"):
        return f"Couldn't quit {name}. {result}"
    return f"Quit {name}."


@tool("List the applications that are currently running and visible.")
def list_running_apps() -> str:
    script = (
        'tell application "System Events" to get the name of every '
        "application process whose background only is false"
    )
    result = osascript(script)
    if result.startswith("Error"):
        return result
    apps = sorted({a.strip() for a in result.split(",") if a.strip()})
    return ", ".join(apps) if apps else "Nothing is running."


@tool("Get the name of the application currently in the foreground.")
def frontmost_app() -> str:
    script = (
        'tell application "System Events" to get the name of the first '
        "application process whose frontmost is true"
    )
    return osascript(script)


# -- audio ----------------------------------------------------------------


@tool(
    "Set the system output volume.",
    {"level": integer("Volume from 0 (silent) to 100 (loudest).")},
    ["level"],
)
def set_volume(level: int) -> str:
    level = max(0, min(100, int(level)))
    result = osascript(f"set volume output volume {level}")
    if result.startswith("Error"):
        return result
    return f"Volume set to {level} percent."


@tool("Get the current system output volume and mute state.")
def get_volume() -> str:
    result = osascript(
        "set s to get volume settings\n"
        'return (output volume of s as text) & "," & (output muted of s as text)'
    )
    if result.startswith("Error"):
        return result
    level, _, muted = result.partition(",")
    state = " and muted" if muted.strip() == "true" else ""
    return f"Volume is at {level.strip()} percent{state}."


@tool(
    "Mute or unmute system audio output.",
    {"muted": boolean("True to mute, false to unmute.")},
    ["muted"],
)
def set_mute(muted: bool) -> str:
    osascript(f"set volume output muted {'true' if muted else 'false'}")
    return "Muted." if muted else "Unmuted."


# -- media ----------------------------------------------------------------


def _active_player() -> str | None:
    for app in MEDIA_APPS:
        running = osascript(
            f'tell application "System Events" to (name of processes) contains "{app}"'
        )
        if running.strip() == "true":
            return app
    return None


@tool(
    "Control music playback in Music or Spotify.",
    {
        "action": enum(
            "What to do.", ["play", "pause", "playpause", "next", "previous"]
        )
    },
    ["action"],
)
def media_control(action: str) -> str:
    app = _active_player()
    if app is None:
        return "Neither Music nor Spotify is running."
    commands = {
        "play": "play",
        "pause": "pause",
        "playpause": "playpause",
        "next": "next track",
        "previous": "previous track",
    }
    command = commands.get(action)
    if command is None:
        return f"Unknown playback action: {action}"
    result = osascript(f'tell application "{app}" to {command}')
    if result.startswith("Error"):
        return result
    return f"{action.capitalize()} in {app}."


@tool("Get the track currently playing in Music or Spotify.")
def now_playing() -> str:
    app = _active_player()
    if app is None:
        return "Neither Music nor Spotify is running."
    result = osascript(
        f'tell application "{app}"\n'
        "  if player state is playing then\n"
        '    return name of current track & " by " & artist of current track\n'
        "  else\n"
        '    return "paused"\n'
        "  end if\n"
        "end tell"
    )
    if result.startswith("Error"):
        return result
    if result.strip() == "paused":
        return f"{app} is paused."
    return f"{result} — playing in {app}."


# -- files ----------------------------------------------------------------


@tool(
    "Search the Mac for files by name using Spotlight.",
    {
        "query": string("What to search for."),
        "limit": integer("Maximum results, default 10."),
    },
    ["query"],
)
def search_files(query: str, limit: int = 10) -> str:
    result = run(["mdfind", "-name", query], timeout=25)
    if result.startswith("Error"):
        return result
    paths = [p for p in result.splitlines() if p.strip()][: max(1, int(limit))]
    if not paths:
        return f"No files found matching {query!r}."
    return "\n".join(paths)


@tool(
    "Open a file, folder, or URL with its default application.",
    {"path": string("A file path, folder path, or http(s) URL.")},
    ["path"],
)
def open_path(path: str) -> str:
    target = path.strip()
    if not target.startswith(("http://", "https://")):
        resolved = Path(target).expanduser()
        if not resolved.exists():
            return f"Nothing exists at {resolved}."
        target = str(resolved)
    result = run(["open", target])
    if result.startswith("Error"):
        return result
    return f"Opened {target}."


# -- shortcuts ------------------------------------------------------------


@tool("List the Apple Shortcuts available on this Mac.")
def list_shortcuts() -> str:
    if shutil.which("shortcuts") is None:
        return "The shortcuts command isn't available."
    result = run(["shortcuts", "list"])
    if result.startswith("Error"):
        return result
    names = [n for n in result.splitlines() if n.strip()]
    return ", ".join(names) if names else "No shortcuts are set up."


@tool(
    "Run an Apple Shortcut by name. Check list_shortcuts first if unsure of "
    "the exact name.",
    {"name": string("Exact shortcut name.")},
    ["name"],
)
def run_shortcut(name: str) -> str:
    result = run(["shortcuts", "run", name], timeout=60)
    if result.startswith("Error"):
        return result
    return result or f"Ran shortcut {name}."


# -- system ---------------------------------------------------------------


@tool("Get battery level, free disk space, and how long the Mac has been up.")
def system_status() -> str:
    parts = []
    battery = run(["pmset", "-g", "batt"])
    for line in battery.splitlines():
        if "%" in line:
            segment = line.split("\t")[-1]
            parts.append(f"Battery {segment.split(';')[0].strip()}")
            break
    disk = run(["df", "-h", "/"])
    rows = disk.splitlines()
    if len(rows) > 1:
        cols = rows[1].split()
        if len(cols) >= 4:
            parts.append(f"{cols[3]} free of {cols[1]} on disk")
    up = run(["uptime"])
    if "up" in up:
        parts.append("Up " + up.split("up")[1].split(",")[0].strip())
    return ". ".join(parts) + "." if parts else "Couldn't read system status."


@tool(
    "Turn macOS dark mode on or off.",
    {"enabled": boolean("True for dark mode, false for light mode.")},
    ["enabled"],
)
def set_dark_mode(enabled: bool) -> str:
    value = "true" if enabled else "false"
    result = osascript(
        'tell application "System Events" to tell appearance preferences '
        f"to set dark mode to {value}"
    )
    if result.startswith("Error"):
        return result
    return f"Dark mode {'on' if enabled else 'off'}."


@tool("Put the display to sleep. The Mac itself stays awake.")
def sleep_display() -> str:
    run(["pmset", "displaysleepnow"])
    return "Display asleep."


@tool(
    "Run an arbitrary AppleScript for automation this assistant has no "
    "dedicated tool for. Return a short string from the script.",
    {"script": string("The AppleScript source to execute.")},
    ["script"],
    gated=True,
)
def run_applescript(script: str) -> str:
    return osascript(script, timeout=45) or "Done."
