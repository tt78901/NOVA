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
| Speech | **Kokoro 82M** via **mlx-audio** | Neural, on the GPU, ~30× faster than real time |

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

### Hearing commands correctly

Whisper is a general model, so it guesses at proper nouns: "open Xcode" comes
back as "open exit code". Nova defends at two layers.

Before transcription, it primes Whisper with the vocabulary it expects on
*this* Mac — the wake word, the command grammar, and the names of your
installed apps — so those words are spelled right in the first place. Turn it
off with `bias = false` under `[stt]`.

After transcription, app names are matched against what's actually installed,
on a letters-only form, so `"exit code"` → `Xcode` and `"note s"` → `Notes`.
The fuzzy pass ignores app names shorter than five characters, because those
collide by accident — `"chrome"` scores 0.8 against `Home`, and opening the
wrong app is worse than saying it couldn't find one. If nothing plausible
matches, Nova says so instead of launching a guess.

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
| `--ui-only` | Serve the HUD alone, no models loaded |
| `--no-ui` | Run headless |

### The voice

Nova speaks through **Kokoro**, an 82M-parameter neural TTS running on the
Apple Silicon GPU via mlx-audio. Warm, it synthesises a sentence in about
0.2 s — roughly thirty times faster than real time — which is what makes
sentence-by-sentence streaming feel immediate rather than stuttery.

Voices are named `<accent><gender>_<name>`, `a` for American and `b` for
British. Set one in `[tts]`:

```toml
[tts]
backend = "kokoro"
voice = "bm_george"   # af_heart af_nova am_michael am_puck bf_emma bm_fable …
speed = 1.0
```

The macOS synthesiser is still there as `backend = "say"` — no download, but a
noticeably older generation of synthesis. Kokoro falls back to it automatically
if mlx-audio is missing.

## The HUD

Nova serves a local status display at `http://127.0.0.1:7788`, opened
automatically on launch. Concentric rings around a reactive core, a live mic
level, the current state, and a running transcript with tool calls as they
fire. The accent colour tracks state: cyan idle and listening, amber while
thinking, green while speaking, red on error.

It's plain HTML with no build step and no dependencies — a threaded
`http.server` serves one page and pushes state over server-sent events. SSE is
one-directional, which is all a display needs, and it avoids a websocket
stack. The bus never blocks: if a tab stops reading, its queue fills and events
drop for that client alone, because a slow HUD must never stall speech
recognition.

Preview the design on its own, with no models and no microphone:

```bash
.venv/bin/python -m assistant --ui-only
```

Disable it with `--no-ui`, or `enabled = false` under `[ui]`.

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

### Web search, and the one thing that leaves the machine

Ask Nova anything she can't know — current events, prices, facts — and she
searches DuckDuckGo and answers out loud. Say "show me" instead and she opens
Safari and **types the query in on screen, character by character**, then hits
Return.

That live typing drives the real keyboard through System Events, so it needs
Accessibility permission: System Settings → Privacy & Security → Accessibility,
and enable your terminal app. Without it she falls back to loading the results
page directly and tells you why.

This is the only part of Nova that touches the network at request time — a
search query has to reach the search engine. The wake word, transcription,
reasoning and speech all stay on the machine. Turn it off entirely:

```toml
[web]
enabled = false
```

Tools are gated by named feature, so a disabled capability isn't merely
refused at call time — it's never shown to the model at all, and never appears
in its capability list.

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
