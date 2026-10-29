"""Generic adapter for any server with an OpenAI-compatible /v1/audio/speech API.

Runs in the main environment (runtime "main"): it only needs the standard
library, numpy and soundfile. The API key, if the server needs one, comes from
the DUB_TTS_API_KEY environment variable that the host sets from
config/secrets.local.json.
"""

import io
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
import soundfile as sf


OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"]

_settings = {}


def _headers():
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("DUB_TTS_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _url(path):
    return str(_settings.get("base_url") or "http://127.0.0.1:8880/v1").rstrip("/") + path


def _request(method, path, payload=None, timeout=None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(_url(path), data=data, method=method, headers=_headers())
    try:
        with urlopen(request, timeout=timeout or int(_settings.get("timeout") or 300)) as response:
            return response.read()
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"HTTP {exc.code} from {_url(path)}: {body or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot reach TTS server {_url(path)}: {exc.reason}") from exc


def load(settings, device):
    global _settings
    _settings = dict(settings)
    return {"sample_rate": 24000, "server": _url("")}


def synthesize(text, language=None, voice=None, duration=None, speed=1.0, hints=None):
    voice = voice or {}
    payload = {
        "model": _settings.get("model") or "tts-1",
        "input": text,
        "voice": voice.get("voice_id") or _settings.get("voice") or "alloy",
        "response_format": "wav",
        "speed": max(0.25, min(4.0, float(speed or 1.0))),
    }
    if _settings.get("instructions"):
        payload["instructions"] = _settings["instructions"]
    raw = _request("POST", "/audio/speech", payload)
    try:
        audio, sample_rate = sf.read(io.BytesIO(raw), dtype="float32", always_2d=False)
    except RuntimeError as exc:
        raise RuntimeError(f"Server did not return readable audio ({len(raw)} bytes): {exc}") from exc
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return np.asarray(audio, dtype=np.float32), int(sample_rate)


def list_voices(settings):
    global _settings
    _settings = dict(settings or _settings)
    for path in ("/audio/voices", "/voices"):
        try:
            data = json.loads(_request("GET", path, timeout=15).decode("utf-8"))
        except (RuntimeError, ValueError):
            continue
        voices = data.get("voices") if isinstance(data, dict) else data
        if isinstance(voices, list) and voices:
            return [v if isinstance(v, str) else (v.get("id") or v.get("name") or v.get("voice_id")) for v in voices]
    return OPENAI_VOICES
