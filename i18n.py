"""Interface language.

Source strings are written in Russian right in the code; translations live in
locales/<code>.json as {"Russian text": "translated text"}. Missing entries
fall back to the Russian text, so a partial translation never breaks anything.

The UI server picks the language (settings or the browser) and passes it to
pipeline jobs through the DUB_LANG environment variable.
"""

import json
import os
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LOCALES_DIR = ROOT / "locales"
SOURCE_LANGUAGE = "ru"
LANGUAGE_ENV = "DUB_LANG"

_current = None


def available_languages():
    return [SOURCE_LANGUAGE] + sorted(path.stem for path in LOCALES_DIR.glob("*.json") if path.stem != SOURCE_LANGUAGE)


def normalize(code):
    """'en-US' -> 'en'; unknown languages -> '' (caller decides the fallback)."""
    code = str(code or "").strip().lower().replace("_", "-").split("-")[0]
    return code if code in available_languages() else ""


def from_accept_language(header):
    """Best supported language from an Accept-Language header (ru for Russian-speaking browsers, else en)."""
    for part in str(header or "").split(","):
        code = part.split(";")[0].strip().lower().split("-")[0]
        if code in ("ru", "uk", "be", "kk"):
            return SOURCE_LANGUAGE
        if normalize(code):
            return normalize(code)
    return "en" if "en" in available_languages() else SOURCE_LANGUAGE


def set_language(code):
    global _current
    _current = normalize(code) or SOURCE_LANGUAGE


def language():
    return _current or normalize(os.environ.get(LANGUAGE_ENV)) or SOURCE_LANGUAGE


@lru_cache(maxsize=None)
def catalog(code):
    if code == SOURCE_LANGUAGE:
        return {}
    path = LOCALES_DIR / f"{code}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, str) and not key.startswith("@")}


def tr(text, **params):
    """Translate a source string; {name} placeholders are filled from params."""
    text = catalog(language()).get(text, text)
    return text.format(**params) if params else text
