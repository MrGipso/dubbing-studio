#!/usr/bin/env sh
# One-time setup of the main environment (Whisper + UI) on macOS / Linux.
# TTS and separation engines are installed later from Settings.
set -e
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

echo "Creating the main environment (.venv) ..."
uv sync

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo
  echo "ffmpeg not found in PATH. Install it (macOS: brew install ffmpeg, Ubuntu: sudo apt install ffmpeg)."
fi

echo
echo "Done. Start the app with ./start.sh, then open Settings."
