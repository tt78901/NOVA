# Nova

A voice assistant for macOS that runs entirely on your own machine. Say
"hey nova", ask a question or give an order, and it answers out loud and
controls your Mac. No account, no API key, and nothing leaves the laptop.

```
you    hey nova, what's playing?
nova   Weightless by Marconi Union — playing in Music.
you    turn it down a bit
nova   Volume set to 30 percent.
```

## How it works

Four local stages, each swappable in `config.toml`:

| Stage | What runs | Notes |
| --- | --- | --- |
| Wake word | Whisper `tiny.en`, behind an energy gate | Any phrase you like, including a custom name |
| Speech to text | `whisper-large-v3-turbo` via **mlx-whisper** | Runs on the Apple Silicon GPU |
| Reasoning | **qwen3:8b** through **Ollama** | Streamed, with tool calling |
| Speech | macOS `say` | Speaks sentence by sentence as tokens arrive |

The pipeline is built around latency. Audio comes off one shared microphone
stream, silence never reaches a model, and Nova starts speaking the first
sentence while the model is still generating the second.

### The wake word, and why the name matters

Most always-on wake-word engines only recognise phrases they ship pretrained
models for. openWakeWord — the low-CPU option here — knows exactly four:
`alexa`, `hey_mycroft`, `hey_jarvis`, `hey_rhasspy`. A custom name like "Nova"
isn't among them, so the default backend is different:

- **`whisper`** (default) — an energy gate watches the mic for free, and only
  actual speech gets transcribed by a tiny Whisper model and matched against
  your phrase. Any name works. It also catches whatever you said *after* the
  wake word, so "hey nova, what time is it" resolves in one breath.
- **`openwakeword`** — near-zero CPU, marginally faster to trigger, but you
  must rename Nova to one of its four phrases.

Switch in `config.toml`:

```toml
[wake]
backend = "openwakeword"
model = "hey_jarvis"
```

## Setup

```bash
./setup.sh
```

That installs `uv`, a private Python 3.12, the dependencies, and Ollama, then
pulls the model. It needs no Homebrew and no `sudo`.

First run downloads the Whisper weights (~1.5 GB) and macOS will ask for
microphone access — grant it to your terminal app. Tool calls that drive other
apps will also trigger a one-time Automation prompt.

## Running

```bash
./.venv/bin/python -m assistant
```

| Flag | Effect |
| --- | --- |
| *(none)* | Voice mode: wait for the wake word |
| `--text` | Type instead of talking, still speaks replies |
| `--once "…"` | Answer a single question and exit |
| `--quiet` | Print the reply instead of speaking it |
| `--devices` | List microphones |
| `--voices` | List speech voices and show the auto-selected one |

A much better voice is one download away: System Settings → Accessibility →
Spoken Content → System Voice → Manage Voices. Grab a Premium voice, then set
`voice = "Ava (Premium)"` in `config.toml`.

## What it can do

Time and date, opening and quitting apps, listing what's running, volume and
mute, playback in Music or Spotify, what's currently playing, Spotlight file
search, opening files and URLs, running Apple Shortcuts, battery and disk
status, dark mode, and sleeping the display.

Adding a tool is one decorated function in `assistant/tools/mac.py`:

```python
@tool("Set the desktop wallpaper.", {"path": string("Image path.")}, ["path"])
def set_wallpaper(path: str) -> str:
    return osascript(f'tell application "Finder" to set desktop picture to POSIX file "{path}"')
```

The docstring-style description is what the model sees, so write it for a
reader who has never seen the code.

### AppleScript

There's a `run_applescript` tool that hands the model arbitrary automation.
It's genuinely useful and genuinely a loaded gun — the model can run any code
it writes. It ships **disabled**; enable it in `config.toml` if you want it.

## Layout

```
assistant/
  runtime.py       state machine: idle → listening → thinking → speaking
  config.py        dataclass defaults, overlaid by config.toml
  audio/
    mic.py         one shared input stream
    vad.py         adaptive-noise-floor speech gate and endpointing
    wake.py        the two wake-word backends
    stt.py         mlx-whisper
    tts.py         sentence-streamed `say`
  brain/
    llm.py         Ollama streaming + the tool loop
    prompts.py     a system prompt written for being heard, not read
  tools/
    registry.py    @tool decorator → JSON schema
    mac.py         the Mac control surface
```

## Licences

Nova is MIT. Everything it depends on is permissively licensed too — no
copyleft anywhere in the tree:

| Component | Licence |
| --- | --- |
| mlx, mlx-whisper | MIT |
| ollama (python client) | MIT |
| sounddevice, onnxruntime, tiktoken | MIT |
| openwakeword | Apache-2.0 |
| numpy, scipy, scikit-learn | BSD-3-Clause |
| Whisper model weights | MIT |
| Qwen3 model weights | Apache-2.0 |

Names aren't covered by copyright at all, and "Nova" is a common dictionary
word rather than anyone's distinctive mark, so a personal project using it
locally raises no trademark question either. That is *not* true of "Jarvis",
which is a Marvel character — fine as a private wake word, worth avoiding if
you ever publish this under that name.
