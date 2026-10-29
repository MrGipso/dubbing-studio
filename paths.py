"""Where the app keeps the files it writes.

Run from a source checkout, everything lives next to the code, as always.
The desktop app sets two folders, because its code folder is replaced on
every update:

  DUB_DATA_DIR     settings, keys, Python environments of the engines, tools
                   such as ffmpeg (%LOCALAPPDATA%\\<app id> on Windows)
  DUB_LIBRARY_DIR  projects and the results of the tools (Documents\\Dubbing Studio)
"""

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DUB_DATA_DIR") or ROOT)
LIBRARY_DIR = Path(os.environ.get("DUB_LIBRARY_DIR") or ROOT)
SEPARATE_DATA = DATA_DIR.resolve() != ROOT

CONFIG_DIR = DATA_DIR / "config"          # settings.local.json, secrets.local.json
RUNTIME_DIR = DATA_DIR / ".runtime"       # resident engine workers
TOOLS_DIR = DATA_DIR / "tools"            # ffmpeg downloaded by the app
PROJECTS_DIR = LIBRARY_DIR / "projects"
OUTPUTS_DIR = LIBRARY_DIR / "outputs"
INPUTS_DIR = LIBRARY_DIR / "inputs"


def engine_data_dir(engine_id):
    """Writable folder of an engine: its environment, engine.local.json, downloaded models."""
    if SEPARATE_DATA:
        return DATA_DIR / "engines" / engine_id
    return ROOT / "engines" / engine_id


def ffmpeg_bin_dir():
    return TOOLS_DIR / "ffmpeg" / "bin"


def add_tools_to_path():
    """Let child processes find the ffmpeg the app downloaded (shutil.which looks at PATH)."""
    folder = ffmpeg_bin_dir()
    if folder.is_dir():
        current = os.environ.get("PATH", "")
        if str(folder) not in current.split(os.pathsep):
            os.environ["PATH"] = str(folder) + os.pathsep + current


def resolve_output_dir(value, default="outputs"):
    """Output folder from the UI: relative names live in the library, not in the code folder."""
    path = Path(str(value or "").strip() or default)
    return path if path.is_absolute() else LIBRARY_DIR / path
