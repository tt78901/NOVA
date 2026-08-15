#!/usr/bin/env bash
# Sets up Nova from a clean machine. No Homebrew and no sudo required.
set -euo pipefail

cd "$(dirname "$0")"

info() { printf '\033[1m==> %s\033[0m\n' "$1"; }

# 1. uv — installs Python without touching the system one.
if ! command -v uv >/dev/null 2>&1; then
  info "Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

# 2. Python 3.12 + dependencies.
info "Creating the virtualenv"
uv venv --python 3.12
uv pip install -r <(uv pip compile pyproject.toml 2>/dev/null || echo "") 2>/dev/null \
  || uv pip install sounddevice numpy openwakeword onnxruntime mlx-whisper ollama

# 3. Ollama, as a plain app download rather than a package manager.
if [ ! -x "$HOME/.local/bin/ollama" ] && ! command -v ollama >/dev/null 2>&1; then
  info "Installing Ollama"
  tmp="$(mktemp -d)"
  curl -# -L -o "$tmp/Ollama.zip" https://ollama.com/download/Ollama-darwin.zip
  unzip -q "$tmp/Ollama.zip" -d "$tmp"
  rm -rf /Applications/Ollama.app
  cp -R "$tmp/Ollama.app" /Applications/
  xattr -dr com.apple.quarantine /Applications/Ollama.app 2>/dev/null || true
  mkdir -p "$HOME/.local/bin"
  ln -sf /Applications/Ollama.app/Contents/Resources/ollama "$HOME/.local/bin/ollama"
  rm -rf "$tmp"
fi

# 4. The model.
if ! curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  info "Starting the Ollama server"
  nohup ollama serve >/tmp/ollama-serve.log 2>&1 &
  sleep 4
fi
info "Pulling qwen3:8b (about 5 GB, one time)"
ollama pull qwen3:8b

info "Done. Start Nova with:  ./.venv/bin/python -m assistant"
echo "The first run downloads the Whisper weights (~1.5 GB) and macOS will ask"
echo "for microphone access — grant it to your terminal app."
