"""Shared app settings, stored in config/settings.local.json (not committed).

Used by the UI server and by the project pipeline, so a job started from the
UI and the same stage run from the command line pick the same engines.
"""

import json
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.local.json"

DEFAULT_SETTINGS = {
    "llm": {
        "provider": "lmstudio",
        "url": "",
        "model": "",
        "contextLength": 8192,
        "autoLoad": True,
        "unloadOthers": True,
        # Free VRAM for the TTS engine after each LLM stage.
        "unloadWhenDone": True,
    },
    "tts": {
        "engine": "omnivoice",
        "keepWarmMinutes": 10,
        "device": "auto",
        "engines": {},
    },
    "separation": {
        "engine": "demucs",
        "device": "auto",
        "engines": {},
    },
    "asr": {
        "model": "medium",
        "device": "cuda",
        "computeType": "float16",
    },
    "ui": {
        # "" = follow the browser language; otherwise a code from locales/ (or "ru").
        "language": "",
    },
}

SECTIONS = tuple(DEFAULT_SETTINGS)
_LOCK = threading.Lock()


def load_settings():
    data = {}
    if SETTINGS_FILE.is_file():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    for section in SECTIONS:
        settings[section].update(data.get(section) or {})
    return settings


def save_settings(update):
    """Merge a partial update ({"llm": {...}, "tts": {"engines": {"id": {...}}}})."""
    with _LOCK:
        settings = load_settings()
        for section in SECTIONS:
            incoming = update.get(section)
            if not isinstance(incoming, dict):
                continue
            incoming = dict(incoming)
            if isinstance(incoming.get("engines"), dict):
                engines = settings[section].setdefault("engines", {})
                for engine_id, values in incoming.pop("engines").items():
                    engines.setdefault(engine_id, {}).update(values or {})
            settings[section].update(incoming)
        CONFIG_DIR.mkdir(exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return settings


def llm_script_args(settings=None):
    """CLI arguments for translate_srt.py / prepare_script.py / ... (never the API key)."""
    llm = (settings or load_settings())["llm"]
    args = ["--llm-provider", str(llm.get("provider") or "lmstudio")]
    if llm.get("url"):
        args += ["--llm-url", str(llm["url"])]
    if llm.get("model"):
        args += ["--model", str(llm["model"])]
    args += ["--lm-context-length", str(int(llm.get("contextLength") or 8192))]
    if not llm.get("autoLoad", True):
        args.append("--no-auto-load-model")
    if not llm.get("unloadOthers", True):
        args.append("--keep-other-models")
    if llm.get("unloadWhenDone", True):
        args.append("--unload-model-when-done")
    return args


def progress(done, total, label=""):
    """Machine-readable progress line; the UI server turns it into a progress bar."""
    print(f"PROGRESS {int(done)}/{max(1, int(total))} {label}".rstrip(), flush=True)


def stage(index, total, label):
    print(f"STAGE {int(index)}/{int(total)} {label}", flush=True)
