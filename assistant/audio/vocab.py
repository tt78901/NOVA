"""Vocabulary biasing and fuzzy name recovery.

Whisper is a general model, so it guesses at proper nouns it has no reason to
expect: "open Xcode" comes back as "open exit code". Two defences here, applied
at different layers:

1. `bias_prompt` primes Whisper with the words it is *likely* to hear on this
   particular Mac — the wake word and the names of installed apps — which
   makes it spell them correctly in the first place.
2. `resolve_app` cleans up afterwards. Even a mangled transcript usually
   survives as something close, so matching on letters-only forms recovers
   "exit code" back to "Xcode" before the tool runs.
"""

from __future__ import annotations

import functools
import re
from difflib import SequenceMatcher
from pathlib import Path

APP_DIRS = [
    Path("/Applications"),
    Path("/Applications/Utilities"),
    Path("/System/Applications"),
    Path("/System/Applications/Utilities"),
    Path.home() / "Applications",
]

# Phrasings the assistant hears constantly. Listing them steers Whisper's
# language model toward command grammar rather than prose.
COMMAND_PHRASES = [
    "Open Safari.",
    "Quit Terminal.",
    "Set the volume to thirty percent.",
    "Mute the audio.",
    "Play some music.",
    "Skip this track.",
    "What is playing?",
    "What time is it?",
    "How is my battery?",
    "Turn on dark mode.",
    "Search my files for the invoice.",
    "Run the shortcut.",
    "Put the display to sleep.",
]


# People say the vendor out loud; macOS drops it. "Apple Music" is filed as
# "Music", "Google Chrome" is usually just "Chrome" in speech.
VENDOR_PREFIXES = ("apple", "google", "microsoft", "adobe")

# Names that aren't a prefix or spelling problem — the app is simply called
# something else from what people say.
ALIASES = {
    "applemusic": "Music",
    "itunes": "Music",
    "musicapp": "Music",
    "systempreferences": "System Settings",
    "preferences": "System Settings",
    "settings": "System Settings",
    "vscode": "Visual Studio Code",
    "visualstudio": "Visual Studio Code",
    "code": "Visual Studio Code",
    "imessage": "Messages",
    "imessages": "Messages",
    "textmessages": "Messages",
    "quicktime": "QuickTime Player",
    "appstore": "App Store",
    "activitymonitor": "Activity Monitor",
    "browser": "Safari",
    "webbrowser": "Safari",
}


def collapse(text: str) -> str:
    """Letters and digits only, lowercased: 'exit code' -> 'exitcode'."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


@functools.lru_cache(maxsize=1)
def installed_apps() -> tuple[str, ...]:
    names: set[str] = set()
    for directory in APP_DIRS:
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.suffix == ".app":
                names.add(entry.stem)
    return tuple(sorted(names))


@functools.lru_cache(maxsize=8)
def bias_prompt(name: str = "Nova", max_apps: int = 60) -> str:
    """A short passage of the vocabulary we expect, fed to Whisper as context.

    Kept deliberately brief: Whisper only reads the last ~224 tokens of the
    prompt, and an over-long one starts leaking into transcripts.
    """
    apps = ", ".join(installed_apps()[:max_apps])
    return (
        f"{name} is a voice assistant on a Mac. "
        f"{' '.join(COMMAND_PHRASES)} "
        f"Installed applications: {apps}."
    )


def resolve_app(spoken: str) -> tuple[str | None, bool]:
    """Map a possibly-misheard app name onto a real installed app.

    Returns (resolved name, whether it needed correcting). A None result means
    nothing plausible matched, so the caller should report failure rather than
    launch something the user never asked for.
    """
    spoken = spoken.strip()
    if not spoken:
        return None, False

    apps = installed_apps()
    if not apps:  # nothing to match against; trust the caller
        return spoken, False

    target = collapse(spoken)
    by_collapsed = {collapse(app): app for app in apps}

    # Aliases win outright, but only if that app is really here.
    alias = ALIASES.get(target)
    if alias and collapse(alias) in by_collapsed:
        return alias, collapse(alias) != target

    match = _match(target, by_collapsed)
    if match:
        return match, collapse(match) != target

    # "apple music" -> "music". Retry once with the vendor name removed.
    for vendor in VENDOR_PREFIXES:
        if target.startswith(vendor) and len(target) > len(vendor):
            stripped = target[len(vendor) :]
            alias = ALIASES.get(stripped)
            if alias and collapse(alias) in by_collapsed:
                return alias, True
            match = _match(stripped, by_collapsed)
            if match:
                return match, True
            break

    return None, False


def _match(target: str, by_collapsed: dict[str, str]) -> str | None:
    if target in by_collapsed:
        return by_collapsed[target]

    # Prefix and containment beat edit distance for things like "photo" -> "Photos".
    contained = [
        app
        for key, app in by_collapsed.items()
        if key.startswith(target) or target.startswith(key)
    ]
    if len(contained) == 1:
        return contained[0]
    if contained:
        return min(contained, key=lambda a: abs(len(collapse(a)) - len(target)))

    # Last resort: edit distance. Restricted to names of five characters or
    # more, because short ones collide by accident — "chrome" scores 0.8
    # against "Home", and opening the wrong app is worse than admitting
    # defeat. Short names are already covered by the exact and prefix passes.
    scored = [
        (SequenceMatcher(None, target, key).ratio(), app)
        for key, app in by_collapsed.items()
        if len(key) >= 5
    ]
    if not scored:
        return None
    score, app = max(scored, key=lambda pair: pair[0])
    return app if score >= 0.75 else None
