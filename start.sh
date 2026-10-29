#!/usr/bin/env sh
# Start Dubbing Studio on macOS / Linux and open it in the browser.
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8

if [ ! -x ".venv/bin/python" ]; then
  echo "The environment is not set up yet. Run ./setup.sh first."
  exit 1
fi

exec .venv/bin/python pipeline_ui.py --open "$@"
