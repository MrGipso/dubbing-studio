import argparse
import cgi
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import llm_client
import tts_engines


ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "ui"
OUTPUTS_DIR = ROOT / "outputs"
INPUTS_DIR = ROOT / "inputs"
YOUTUBE_DIR = INPUTS_DIR / "youtube"
CONFIG_DIR = ROOT / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.local.json"
POWERSHELL = os.environ.get("PIPELINE_UI_PWSH", "powershell.exe")

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
        "device": "auto",
        "engines": {},
    },
}
LANGUAGE_NAMES_BY_ID = {
    "en": "English",
    "ru": "Russian",
    "uk": "Ukrainian",
    "es": "Spanish",
    "pt": "Portuguese",
    "de": "German",
    "fr": "French",
    "it": "Italian",
    "pl": "Polish",
    "bg": "Bulgarian",
    "tr": "Turkish",
    "nl": "Dutch",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "el": "Greek",
    "he": "Hebrew",
    "ar": "Arabic",
    "hi": "Hindi",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "ms": "Malay",
    "sw": "Swahili",
}
LANGUAGE_IDS = {name.lower(): code for code, name in LANGUAGE_NAMES_BY_ID.items()}


class JobState:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.started_at = None
        self.finished_at = None
        self.exit_code = None
        self.command = []
        self.kind = ""
        self.lines = []
        self.result = {}

    def snapshot(self):
        with self.lock:
            running = self.process is not None and self.process.poll() is None
            return {
                "running": running,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "exit_code": self.exit_code,
                "command": self.command,
                "kind": self.kind,
                "lines": self.lines[-1000:],
                "result": self.result,
            }

    def append(self, line):
        with self.lock:
            self.lines.append(line.rstrip("\n"))
            if len(self.lines) > 3000:
                self.lines = self.lines[-3000:]


JOB = JobState()
SETTINGS_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# Engine settings (shared by every tab, saved in config/settings.local.json)


def load_settings():
    data = {}
    if SETTINGS_FILE.is_file():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    for section in ("llm", "tts"):
        settings[section].update(data.get(section) or {})
    return settings


def save_settings(update):
    with SETTINGS_LOCK:
        settings = load_settings()
        for section in ("llm", "tts"):
            incoming = update.get(section)
            if not isinstance(incoming, dict):
                continue
            if section == "tts" and isinstance(incoming.get("engines"), dict):
                engines = settings["tts"].setdefault("engines", {})
                for engine_id, values in incoming.pop("engines").items():
                    engines.setdefault(engine_id, {}).update(values or {})
            settings[section].update(incoming)
        CONFIG_DIR.mkdir(exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return settings


def llm_from_settings(llm, timeout=20):
    return llm_client.LLMClient(
        provider=llm.get("provider"),
        base_url=llm.get("url") or "",
        model=llm.get("model") or "",
        context_length=int(llm.get("contextLength") or 8192),
        auto_load=bool(llm.get("autoLoad", True)),
        unload_others=bool(llm.get("unloadOthers", True)),
        timeout=timeout,
    )


def llm_script_args(settings=None):
    """CLI arguments for translate_srt.py / prepare_script.py / ... (no API key here)."""
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


def llm_powershell_args(settings=None):
    llm = (settings or load_settings())["llm"]
    args = ["-LlmProvider", str(llm.get("provider") or "lmstudio")]
    if llm.get("url"):
        args += ["-LlmUrl", str(llm["url"])]
    if llm.get("model"):
        args += ["-LlmModel", str(llm["model"])]
    args += ["-LmContextLength", str(int(llm.get("contextLength") or 8192))]
    if not llm.get("autoLoad", True):
        args.append("-NoAutoLoadModel")
    if not llm.get("unloadOthers", True):
        args.append("-KeepOtherModels")
    if llm.get("unloadWhenDone", True):
        args.append("-UnloadModelWhenDone")
    return args


def job_environment():
    """Environment for child processes: UTF-8 logs and the LLM key (never on the command line)."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    llm = load_settings()["llm"]
    api_key = llm_client.stored_api_key(str(llm.get("provider") or ""))
    if api_key:
        env[llm_client.API_KEY_ENV] = api_key
    return env


def engines_payload():
    settings = load_settings()
    engines = tts_engines.list_engines()
    for manifest in engines:
        if "id" in manifest and "settings" in manifest:
            manifest["values"] = tts_engines.resolve_settings(manifest, (settings["tts"].get("engines") or {}).get(manifest["id"]))
    return engines


def settings_payload():
    settings = load_settings()
    providers = list(llm_client.load_providers().values())
    keys = {item["id"]: llm_client.mask_key(llm_client.stored_api_key(item["id"])) for item in providers}
    return {"settings": settings, "providers": providers, "keys": keys, "engines": engines_payload()}


def dub_command(jsonl, output, payload, prefix, natural=False):
    """Command for dub.py: the engine comes from saved settings, timing from the tab."""
    tts = load_settings()["tts"]
    engine_id = str(payload.get("ttsEngine") or tts.get("engine") or "omnivoice")
    manifest = tts_engines.load_manifest(engine_id)
    engine_settings = tts_engines.resolve_settings(manifest, (tts.get("engines") or {}).get(engine_id))

    def number(name, default):
        value = payload.get(f"{prefix}{name}")
        return float(default if value in (None, "") else value)

    command = [
        sys.executable or "python",
        str(ROOT / "dub.py"),
        str(jsonl),
        "--engine", engine_id,
        "--device", str(tts.get("device") or "auto"),
        "--engine-settings", json.dumps(engine_settings, ensure_ascii=False),
        "--speed", str(number("Speed", 1.0)),
        "--tail-room", str(number("TailRoom", 0.45)),
        "--max-auto-speed", str(number("MaxAutoSpeed", 1.18)),
    ]
    if output:
        command += ["--output", str(output)]
    if payload.get(f"{prefix}AutoFit"):
        command.append("--auto-fit")
    if natural:
        command.append("--natural-duration")
    if payload.get(f"{prefix}KeepSegments"):
        command.append("--keep-segments")
    append_voice_postprocess_args(command, payload, prefix)
    return command


def language_id_for(language):
    return LANGUAGE_IDS.get(str(language or "").strip().lower(), "")


def json_response(handler, payload, status=200):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def scoped_file_from_query(query, root):
    name = unquote(query.get("name", [""])[0])
    file_path = (root / name).resolve()
    scoped_root = root.resolve()
    if scoped_root not in file_path.parents or not file_path.is_file():
        return None
    return file_path


def output_file_from_query(query):
    return scoped_file_from_query(query, OUTPUTS_DIR)


def output_file_from_name(name):
    file_path = (OUTPUTS_DIR / str(name or "")).resolve()
    scoped_root = OUTPUTS_DIR.resolve()
    if scoped_root not in file_path.parents or not file_path.is_file():
        return None
    return file_path


def youtube_file_from_query(query):
    return scoped_file_from_query(query, YOUTUBE_DIR)


def audio_file_from_query(query):
    path = Path(unquote(query.get("path", [""])[0])).resolve()
    if not path.is_file() or path.suffix.lower() not in {".wav", ".mp3", ".m4a", ".flac", ".ogg"}:
        return None
    return path


def content_type_for_file(path):
    suffix = path.suffix.lower()
    audio_types = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
    }
    return audio_types.get(suffix) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def serve_output_file(handler, file_path):
    file_size = file_path.stat().st_size
    content_type = content_type_for_file(file_path)
    range_header = handler.headers.get("Range")

    start = 0
    end = file_size - 1
    status = 200
    if range_header:
        match = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
        if match:
            if match.group(1):
                start = int(match.group(1))
            if match.group(2):
                end = int(match.group(2))
            end = min(end, file_size - 1)
            if start > end or start >= file_size:
                handler.send_response(416)
                handler.send_header("Content-Range", f"bytes */{file_size}")
                handler.end_headers()
                return
            status = 206

    length = end - start + 1
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(length))
    if status == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
    handler.end_headers()

    with file_path.open("rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            chunk = f.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            handler.wfile.write(chunk)
            remaining -= len(chunk)


def read_json(handler):
    # Browsers send cross-site "simple" requests only as text/plain or forms;
    # requiring JSON forces a CORS preflight that this server never approves.
    if "application/json" not in handler.headers.get("Content-Type", ""):
        raise ValueError("Expected application/json request.")
    length = int(handler.headers.get("Content-Length") or 0)
    if not length:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    return json.loads(raw)


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def request_is_local(handler):
    """Reject DNS-rebinding (foreign Host) and cross-site requests (foreign Origin)."""
    host = (handler.headers.get("Host") or "").rsplit(":", 1)[0].lower()
    if host not in LOCAL_HOSTS:
        return False
    origin = handler.headers.get("Origin")
    if origin and origin != "null":
        origin_host = (urlparse(origin).hostname or "").lower()
        if origin_host not in {"127.0.0.1", "localhost", "::1"}:
            return False
    return True


def safe_stem(path):
    stem = Path(path).stem
    safe = re.sub(r"\s+", "_", stem)
    safe = re.sub(r"[^\w-]+", "_", safe).strip("_")
    return safe or "audio"


def default_output_prefix(path):
    full_path = str(Path(path).resolve()).lower()
    digest = hashlib.sha1(full_path.encode("utf-8")).hexdigest()[:8]
    return f"{digest}_{safe_stem(path)}"


def unique_input_path(filename, kind):
    INPUTS_DIR.mkdir(exist_ok=True)
    original = Path(filename or "audio.wav")
    safe_name = safe_stem(original.name)
    suffix = original.suffix.lower() or ".wav"
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    prefixes = {
        "reference": "reference",
        "music": "music",
        "mixVoice": "voice",
        "transcribeVideo": "video",
        "transcribeAudio": "transcribe",
        "translateReference": "translate_reference",
        "scriptReference": "script_reference",
        "contentSubtitles": "content_subtitles",
        "contentReference": "content_reference",
        "cleanWav": "clean_wav",
    }
    prefix = prefixes.get(kind, "audio")
    candidate = INPUTS_DIR / f"{prefix}_{timestamp}_{safe_name}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = INPUTS_DIR / f"{prefix}_{timestamp}_{safe_name}_{counter}{suffix}"
        counter += 1
    return candidate


def safe_relative_output_dir(value, default_dir):
    raw = str(value or "").strip()
    if not raw:
        return default_dir
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def upload_file(handler):
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        raise ValueError("Expected multipart/form-data upload.")

    form = cgi.FieldStorage(
        fp=handler.rfile,
        headers=handler.headers,
        environ={
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": handler.headers.get("Content-Length", "0"),
        },
    )

    if "file" not in form:
        raise ValueError("No file field was uploaded.")
    file_item = form["file"]
    if isinstance(file_item, list):
        file_item = file_item[0]
    if not getattr(file_item, "filename", ""):
        raise ValueError("Uploaded file has no filename.")

    kind = str(form.getfirst("kind", "audio"))
    destination = unique_input_path(file_item.filename, kind)
    with destination.open("wb") as f:
        while True:
            chunk = file_item.file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)

    return {
        "path": str(destination),
        "name": destination.name,
        "size": destination.stat().st_size,
    }


def terminate_process_tree(process):
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
        )
        return
    process.terminate()


def choose_file(kind):
    titles = {
        "reference": "Choose reference audio",
        "music": "Choose background music",
        "mixVoice": "Choose generated voice track",
        "transcribeVideo": "Choose video to transcribe",
        "transcribeAudio": "Choose audio to transcribe",
        "translateReference": "Choose translation reference audio",
        "scriptReference": "Choose script voice reference audio",
        "contentSubtitles": "Choose subtitles for content generation",
        "contentReference": "Choose content voice reference audio",
        "cleanWav": "Choose WAV to clean",
    }
    title = titles.get(kind, "Choose audio to translate")
    media_filter = "Video files (*.mp4;*.mov;*.mkv;*.avi;*.webm;*.m4v;*.wmv;*.flv)|*.mp4;*.mov;*.mkv;*.avi;*.webm;*.m4v;*.wmv;*.flv|Audio files (*.wav;*.mp3;*.m4a;*.flac;*.ogg)|*.wav;*.mp3;*.m4a;*.flac;*.ogg|All files (*.*)|*.*"
    audio_filter = "Audio files (*.wav;*.mp3;*.m4a;*.flac;*.ogg)|*.wav;*.mp3;*.m4a;*.flac;*.ogg|All files (*.*)|*.*"
    subtitle_filter = "Subtitle files (*.srt;*.vtt;*.ttml;*.txt)|*.srt;*.vtt;*.ttml;*.txt|All files (*.*)|*.*"
    if kind == "contentSubtitles":
        file_filter = subtitle_filter
    else:
        file_filter = media_filter if kind == "transcribeVideo" else audio_filter
    script = rf"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = '{title}'
$dialog.Filter = '{file_filter}'
$dialog.InitialDirectory = '{str(INPUTS_DIR if INPUTS_DIR.is_dir() else ROOT)}'
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  Write-Output $dialog.FileName
}}
"""
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-STA", "-Command", script],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "File picker failed.").strip())
    return result.stdout.strip()


def build_reference_transcribe_command(payload):
    audio = str(payload.get("refAudio") or "").strip()
    if not audio:
        raise ValueError("Reference audio path is required.")
    if not Path(audio).is_file():
        raise ValueError(f"Reference audio was not found: {audio}")

    output_dir = str(payload.get("outputDir") or "outputs")
    whisper_model = str(payload.get("whisperModel") or "medium")
    whisper_device = str(payload.get("whisperDevice") or "cuda")
    whisper_compute_type = str(payload.get("whisperComputeType") or "float16")
    output_prefix = f"ref_{default_output_prefix(audio)}"
    script = ROOT / "run_whisper.ps1"
    command = [
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Audio",
        audio,
        "-Model",
        whisper_model,
        "-Language",
        "auto",
        "-OutputDir",
        output_dir,
        "-Device",
        whisper_device,
        "-ComputeType",
        whisper_compute_type,
        "-OutputPrefix",
        output_prefix,
    ]
    txt_path = ROOT / output_dir / (output_prefix + ".txt")
    return command, txt_path


def list_outputs():
    OUTPUTS_DIR.mkdir(exist_ok=True)
    files = []
    for path in sorted(OUTPUTS_DIR.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        if not path.is_file():
            continue
        stat = path.stat()
        files.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "kind": path.suffix.lower().lstrip(".") or "file",
            }
        )
    return files


def list_youtube_downloads():
    YOUTUBE_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for path in sorted(YOUTUBE_DIR.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        if not path.is_file():
            continue
        stat = path.stat()
        files.append(
            {
                "name": path.name,
                "path": str(path),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "kind": path.suffix.lower().lstrip(".") or "file",
            }
        )
    return files


def latest_youtube_download(media_kind=""):
    files = list_youtube_downloads()
    if media_kind == "audio":
        files = [item for item in files if item["kind"] in {"wav", "mp3", "m4a", "flac", "ogg"}]
    elif media_kind == "video":
        files = [item for item in files if item["kind"] in {"mp4", "mov", "mkv", "avi", "webm", "m4v", "wmv", "flv"}]
    elif media_kind in {"subtitle", "subtitles"}:
        files = [item for item in files if item["kind"] in {"srt", "vtt", "ttml", "srv3", "txt"}]
    return files[0]["path"] if files else ""


def latest_dub_jsonl():
    OUTPUTS_DIR.mkdir(exist_ok=True)
    candidates = [
        path for path in OUTPUTS_DIR.glob("*.dub.jsonl")
        if ".partial." not in path.name and path.is_file()
    ]
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return str(candidates[0])


def latest_output_audio():
    OUTPUTS_DIR.mkdir(exist_ok=True)
    audio_suffixes = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}
    candidates = [
        path for path in OUTPUTS_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in audio_suffixes
    ]
    if not candidates:
        return ""

    preferred = [
        path for path in candidates
        if path.name.lower().endswith((".dub.wav", ".music.wav"))
    ]
    selected = preferred or candidates
    selected.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return str(selected[0])


def latest_translations_json():
    OUTPUTS_DIR.mkdir(exist_ok=True)
    candidates = [
        path for path in OUTPUTS_DIR.glob("*.translations.json")
        if path.is_file()
    ]
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return str(candidates[0])


def latest_script_jsonl():
    OUTPUTS_DIR.mkdir(exist_ok=True)
    candidates = [
        path for path in OUTPUTS_DIR.glob("*.script.dub.jsonl")
        if ".partial." not in path.name and path.is_file()
    ]
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return str(candidates[0])


def latest_content_script_text():
    OUTPUTS_DIR.mkdir(exist_ok=True)
    candidates = [
        path for path in OUTPUTS_DIR.glob("*.content.script.txt")
        if path.is_file()
    ]
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return str(candidates[0])


def latest_content_script_jsonl():
    OUTPUTS_DIR.mkdir(exist_ok=True)
    candidates = [
        path for path in OUTPUTS_DIR.glob("*.script.dub.jsonl")
        if ".partial." not in path.name and path.is_file() and path.name.startswith("content_")
    ]
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return str(candidates[0])


def build_command(payload):
    script = ROOT / "run_translate_audio.ps1"
    audio = str(payload.get("audio") or "").strip()
    if not audio:
        raise ValueError("Audio path is required.")
    if not payload.get("skipWhisper") and not Path(audio).is_file():
        raise ValueError(f"Audio file was not found: {audio}")

    command = [
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Audio",
        audio,
        "-TargetLanguage",
        str(payload.get("targetLanguage") or "English"),
        "-SourceLanguage",
        str(payload.get("sourceLanguage") or "Russian"),
        "-WhisperModel",
        str(payload.get("whisperModel") or "medium"),
        "-WhisperLanguage",
        str(payload.get("whisperLanguage") or language_id_for(payload.get("sourceLanguage")) or "auto"),
        "-WhisperDevice",
        str(payload.get("whisperDevice") or "cuda"),
        "-WhisperComputeType",
        str(payload.get("whisperComputeType") or "float16"),
        "-OutputDir",
        str(payload.get("outputDir") or "outputs"),
        *llm_powershell_args(),
        "-BatchSize",
        str(int(payload.get("batchSize") or 8)),
        "-Style",
        str(payload.get("style") or "Natural spoken dubbing, concise timing-preserving translation."),
        "-PhraseMaxGap",
        str(float(payload.get("phraseMaxGap") or 0.8)),
        "-PhraseMaxDuration",
        str(float(payload.get("phraseMaxDuration") or 12.0)),
    ]

    output_prefix = str(payload.get("outputPrefix") or "").strip()
    if output_prefix:
        command.extend(["-OutputPrefix", output_prefix])

    target_language_id = str(payload.get("targetLanguageId") or "").strip()
    if not target_language_id:
        target_language_id = language_id_for(payload.get("targetLanguage"))
    if target_language_id:
        command.extend(["-TargetLanguageId", target_language_id])

    optional_strings = {
        "refAudio": "-RefAudio",
        "refText": "-RefText",
    }
    for key, flag in optional_strings.items():
        value = str(payload.get(key) or "").strip()
        if value:
            command.extend([flag, value])

    switches = {
        "skipWhisper": "-SkipWhisper",
        "resume": "-Resume",
        "dryRun": "-DryRun",
        "noMergePhrases": "-NoMergePhrases",
    }
    for key, flag in switches.items():
        if payload.get(key):
            command.append(flag)

    return command


def build_dubbing_command(payload):
    jsonl = str(payload.get("jsonl") or "").strip()
    if not jsonl:
        jsonl = latest_dub_jsonl()
    if not jsonl:
        raise ValueError("Voice plan (.dub.jsonl) is required: run the translation first.")
    if not Path(jsonl).is_file():
        raise ValueError(f"Voice plan was not found: {jsonl}")

    output = str(payload.get("dubOutput") or "").strip()
    return dub_command(jsonl, output, payload, "dub")


def append_voice_postprocess_args(command, payload, prefix):
    enabled = payload.get(f"{prefix}PostCleanEnabled")
    clean_mode = str(payload.get(f"{prefix}PostCleanMode") or "soft").strip().lower()
    if enabled:
        command.extend(["--post-clean", clean_mode if clean_mode in {"soft", "normal", "hard"} else "soft"])
    else:
        command.extend(["--post-clean", "off"])
    command.extend(["--post-fade-ms", str(float(payload.get(f"{prefix}PostFadeMs") or 40.0))])
    command.extend(["--post-min-pause", str(float(payload.get(f"{prefix}PostMinPause") or 0.15))])
    if payload.get(f"{prefix}PostNormalize"):
        command.append("--post-normalize")


def build_clean_wav_command(payload):
    file_path = output_file_from_name(payload.get("fileName"))
    if not file_path:
        raw_path = str(payload.get("cleanWavInput") or "").strip()
        file_path = Path(raw_path).resolve() if raw_path else None
    if not file_path or not file_path.is_file():
        raise ValueError("WAV file was not found.")
    if file_path.suffix.lower() != ".wav":
        raise ValueError("Only WAV files can be cleaned.")

    output = str(payload.get("cleanWavOutput") or "").strip()
    output_path = Path(output).resolve() if output else file_path.with_name(file_path.stem + ".clean.wav")
    mode = str(payload.get("mode") or "soft").strip().lower()
    if mode not in {"soft", "normal", "hard"}:
        mode = "soft"

    def payload_float(name, default):
        value = payload.get(name)
        if value is None or value == "":
            return default
        return float(value)

    command = [
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "run_clean_wav.ps1"),
        "-InputPath",
        str(file_path),
        "-Output",
        str(output_path),
        "-Mode",
        mode,
        "-MinPause",
        str(float(payload.get("minPause") or 0.15)),
        "-FadeMs",
        str(payload_float("fadeMs", 40.0)),
        "-Preset",
        str(payload.get("preset") or "custom"),
        "-Amount",
        str(payload_float("amount", 55.0)),
        "-DeesserFreq",
        str(payload_float("deesserFreq", 5200.0)),
        "-DeesserStrength",
        str(payload_float("deesserStrength", 55.0)),
        "-EqMudDb",
        str(payload_float("eqMudDb", -1.8)),
        "-EqClarityDb",
        str(payload_float("eqClarityDb", 2.2)),
        "-EqAirDb",
        str(payload_float("eqAirDb", 1.1)),
        "-CompThreshold",
        str(payload_float("compThreshold", -20.0)),
        "-CompRatio",
        str(payload_float("compRatio", 3.0)),
        "-CompMakeup",
        str(payload_float("compMakeup", 2.2)),
        "-SatDrive",
        str(payload_float("satDrive", 45.0)),
        "-SatMix",
        str(payload_float("satMix", 28.0)),
        "-LimiterCeiling",
        str(payload_float("limiterCeiling", 0.95)),
        "-LoudnessTarget",
        str(payload_float("loudnessTarget", -16.0)),
        "-RoomMix",
        str(payload_float("roomMix", 10.0)),
    ]
    if payload.get("normalize"):
        command.append("-Normalize")
    effect_switches = {
        "deesser": "-Deesser",
        "eq": "-Eq",
        "compressor": "-Compressor",
        "saturation": "-Saturation",
        "limiter": "-Limiter",
        "loudness": "-Loudness",
        "room": "-Room",
    }
    for key, flag in effect_switches.items():
        if payload.get(key):
            command.append(flag)
    return command, output_path


def build_music_mix_command(payload):
    voice_audio = str(payload.get("musicVoiceAudio") or "").strip()
    music_audio = str(payload.get("musicAudio") or "").strip()
    if not voice_audio:
        voice_audio = latest_output_audio()
    if not voice_audio:
        raise ValueError("Generated voice track is required.")
    if not music_audio:
        raise ValueError("Background music track is required.")
    if not Path(voice_audio).is_file():
        raise ValueError(f"Generated voice track was not found: {voice_audio}")
    if not Path(music_audio).is_file():
        raise ValueError(f"Background music track was not found: {music_audio}")

    volume_db = max(-30.0, min(0.0, float(payload.get("musicVolumeDb") or -18.0)))
    command = [
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "run_music_mix.ps1"),
        "-VoiceAudio",
        voice_audio,
        "-MusicAudio",
        music_audio,
        "-MusicVolumeDb",
        str(volume_db),
    ]
    output = str(payload.get("musicOutput") or "").strip()
    if output:
        command.extend(["-Output", output])
    return command


def build_transcription_command(payload):
    video = str(payload.get("transcribeVideo") or "").strip()
    audio = str(payload.get("transcribeAudio") or "").strip()
    media = video or audio
    if not media:
        raise ValueError("Choose a video or audio file to transcribe.")
    if not Path(media).is_file():
        raise ValueError(f"Media file was not found: {media}")

    command = [
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "run_transcription.ps1"),
        "-Media",
        media,
        "-Model",
        str(payload.get("transcribeModel") or "medium"),
        "-Language",
        str(payload.get("transcribeLanguage") or "ru"),
        "-OutputDir",
        str(payload.get("transcribeOutputDir") or "outputs"),
        "-Device",
        str(payload.get("transcribeDevice") or "cuda"),
        "-ComputeType",
        str(payload.get("transcribeComputeType") or "float16"),
    ]

    output_prefix = str(payload.get("transcribeOutputPrefix") or "").strip()
    if output_prefix:
        command.extend(["-OutputPrefix", output_prefix])
    if payload.get("keepExtractedAudio"):
        command.append("-KeepExtractedAudio")
    return command


def safe_output_prefix(value, fallback):
    raw = str(value or "").strip()
    if not raw:
        raw = fallback
    safe = re.sub(r"\s+", "_", raw)
    safe = re.sub(r"[^\w-]+", "_", safe).strip("_")
    return safe or fallback


def build_text_translation_command(payload):
    text = str(payload.get("translateText") or "").strip()
    if not text:
        raise ValueError("Russian text is required.")

    ref_audio = str(payload.get("translateRefAudio") or "").strip()
    if ref_audio and not Path(ref_audio).is_file():
        raise ValueError(f"Reference audio was not found: {ref_audio}")
    ref_text = str(payload.get("translateRefText") or "").strip()

    target_language = str(payload.get("translateTargetLanguage") or "").strip()
    language_flags = [
        ("translateEnglish", "English"),
        ("translateSpanish", "Spanish"),
        ("translatePortuguese", "Portuguese"),
        ("translateBulgarian", "Bulgarian"),
    ]
    targets = [target_language] if target_language else [
        language for key, language in language_flags if payload.get(key)
    ]
    if not targets:
        raise ValueError("Choose at least one target language.")

    OUTPUTS_DIR.mkdir(exist_ok=True)
    fallback_prefix = f"text_{time.strftime('%Y%m%d_%H%M%S')}_{hashlib.sha1(text.encode('utf-8')).hexdigest()[:8]}"
    output_prefix = safe_output_prefix(payload.get("translateOutputPrefix"), fallback_prefix)
    text_file = OUTPUTS_DIR / f"{output_prefix}.source.txt"
    text_file.write_text(text + "\n", encoding="utf-8")

    command = [
        sys.executable or "python",
        str(ROOT / "translate_text.py"),
        "--text-file",
        str(text_file),
        "--source-language",
        str(payload.get("translateSourceLanguage") or "Russian"),
        *llm_script_args(),
        "--output-dir",
        str(payload.get("translateOutputDir") or "outputs"),
        "--output-prefix",
        output_prefix,
        "--temperature",
        str(float(payload.get("translateTemperature") or 0.2)),
        "--style",
        str(payload.get("translateStyle") or "Natural spoken voiceover translation."),
    ]
    for target in targets:
        command.extend(["--target-language", target])
    if ref_audio:
        command.extend(["--ref-audio", ref_audio])
    if ref_text:
        command.extend(["--ref-text", ref_text])
    return command


def split_voice_text(text, max_chars=260):
    text = re.sub(r"\r\n?", "\n", str(text or "").strip())
    pieces = []
    for paragraph in re.split(r"\n\s*\n+", text):
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        if not paragraph:
            continue
        pieces.extend(
            part.strip()
            for part in re.split(r"(?<=[.!?\u2026])\s+", paragraph)
            if part.strip()
        )

    chunks = []
    current = ""
    for piece in pieces:
        if len(piece) > max_chars:
            words = piece.split()
            buf = ""
            for word in words:
                candidate = f"{buf} {word}".strip()
                if buf and len(candidate) > max_chars:
                    chunks.append(buf)
                    buf = word
                else:
                    buf = candidate
            if buf:
                chunks.append(buf)
            current = ""
            continue

        candidate = f"{current} {piece}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = piece
        else:
            current = candidate

    if current:
        chunks.append(current)
    return chunks or [text]


def estimate_voice_duration(text):
    words = len(re.findall(r"\S+", text))
    chars = len(text)
    return round(max(1.2, words / 2.45 + 0.55, chars / 18.0), 2)


def translation_language_match(item, requested):
    requested = str(requested or "").strip().lower()
    return requested in {
        str(item.get("language_id") or "").strip().lower(),
        str(item.get("language") or "").strip().lower(),
    }


def create_text_voice_jsonl(payload):
    OUTPUTS_DIR.mkdir(exist_ok=True)
    voice_source = str(payload.get("translateVoiceSource") or "translation").strip().lower()
    language_id = str(payload.get("translateVoiceLanguage") or "en").strip().lower()

    data = {}
    base = ""
    if voice_source == "direct":
        text = str(payload.get("translateVoiceText") or "").strip()
        if not text:
            raise ValueError("Ready text is required for WAV generation.")
        fallback_prefix = f"voice_{time.strftime('%Y%m%d_%H%M%S')}_{hashlib.sha1(text.encode('utf-8')).hexdigest()[:8]}"
        base = safe_output_prefix(payload.get("translateVoiceOutputPrefix"), fallback_prefix)
    else:
        translations_json = str(payload.get("translateVoiceJson") or "").strip()
        if not translations_json:
            translations_json = latest_translations_json()
        if not translations_json:
            raise ValueError("Translations JSON path is required.")

        translations_path = Path(translations_json)
        if not translations_path.is_file():
            raise ValueError(f"Translations JSON was not found: {translations_json}")

        data = json.loads(translations_path.read_text(encoding="utf-8", errors="replace"))
        translations = data.get("translations") or []
        selected = next((item for item in translations if translation_language_match(item, language_id)), None)
        if not selected:
            available = [f"{item.get('language')} ({item.get('language_id')})" for item in translations]
            raise ValueError(f"Translation for language '{language_id}' was not found. Available: {available}")
        text = str(selected.get("translation") or "").strip()
        if not text:
            raise ValueError(f"Selected translation is empty: {language_id}")

        base = translations_path.name
        if base.endswith(".translations.json"):
            base = base[: -len(".translations.json")]
        else:
            base = translations_path.stem
        base = safe_output_prefix(payload.get("translateVoiceOutputPrefix"), base)

    # A voice sample is optional: engines without cloning use their own voices.
    ref_audio = str(payload.get("translateRefAudio") or data.get("ref_audio") or "").strip()
    if ref_audio and not Path(ref_audio).is_file():
        raise ValueError(f"Reference audio was not found: {ref_audio}")
    ref_text = str(payload.get("translateRefText") or data.get("ref_text") or "").strip()

    jsonl_path = OUTPUTS_DIR / f"{base}.{language_id}.voice.jsonl"
    chunks = split_voice_text(text)
    cursor = 0.0
    with jsonl_path.open("w", encoding="utf-8") as f:
        for index, chunk in enumerate(chunks, start=1):
            duration = estimate_voice_duration(chunk)
            item = {
                "id": f"voice_{index:04d}",
                "text": chunk,
                "duration": duration,
                "source_start": round(cursor, 3),
                "source_end": round(cursor + duration, 3),
                "language_id": language_id,
                "ref_audio": ref_audio,
            }
            if ref_text:
                item["ref_text"] = ref_text
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            cursor += duration + 0.15
    return jsonl_path, base, language_id


def build_text_voice_command(payload):
    jsonl_path, base, language_id = create_text_voice_jsonl(payload)
    output = str(payload.get("translateVoiceOutput") or "").strip()
    if not output:
        output = str(OUTPUTS_DIR / f"{base}.{language_id}.voice.wav")
    return dub_command(jsonl_path, output, payload, "translateVoice")


def build_script_prepare_command(payload):
    text = str(payload.get("scriptText") or "").strip()
    if not text:
        raise ValueError("Script text is required.")

    ref_audio = str(payload.get("scriptRefAudio") or "").strip()
    if ref_audio and not Path(ref_audio).is_file():
        raise ValueError(f"Script reference audio was not found: {ref_audio}")
    ref_text = str(payload.get("scriptRefText") or "").strip()

    OUTPUTS_DIR.mkdir(exist_ok=True)
    fallback_prefix = f"script_{time.strftime('%Y%m%d_%H%M%S')}_{hashlib.sha1(text.encode('utf-8')).hexdigest()[:8]}"
    output_prefix = safe_output_prefix(payload.get("scriptOutputPrefix"), fallback_prefix)
    text_file = OUTPUTS_DIR / f"{output_prefix}.source.txt"
    text_file.write_text(text + "\n", encoding="utf-8")

    language_id = str(payload.get("scriptLanguageId") or "ru").strip() or "ru"
    language_name = str(payload.get("scriptLanguage") or "").strip() or LANGUAGE_NAMES_BY_ID.get(language_id, language_id)
    command = [
        sys.executable or "python",
        str(ROOT / "prepare_script.py"),
        "--text-file",
        str(text_file),
        "--language",
        language_name,
        "--language-id",
        language_id,
        *llm_script_args(),
        "--output-dir",
        str(payload.get("scriptOutputDir") or "outputs"),
        "--output-prefix",
        output_prefix,
        "--temperature",
        str(float(payload.get("scriptTemperature") or 0.4)),
        "--style",
        str(payload.get("scriptStyle") or "Natural YouTube narration, clear pacing, keep author's wording."),
    ]
    if ref_audio:
        command.extend(["--ref-audio", ref_audio])
    if ref_text:
        command.extend(["--ref-text", ref_text])
    if payload.get("scriptSimpleSplit"):
        command.append("--simple")
    return command


def build_script_voice_command(payload):
    jsonl = str(payload.get("scriptJsonl") or "").strip()
    if not jsonl:
        jsonl = latest_script_jsonl()
    if not jsonl:
        raise ValueError("Script voice plan (.script.dub.jsonl) is required.")
    if not Path(jsonl).is_file():
        raise ValueError(f"Script voice plan was not found: {jsonl}")
    output = str(payload.get("scriptVoiceOutput") or "").strip()
    return dub_command(jsonl, output, payload, "scriptVoice", natural=bool(payload.get("scriptVoiceNaturalDuration")))


def build_youtube_download_command(payload):
    url = str(payload.get("youtubeUrl") or "").strip()
    if not url:
        raise ValueError("YouTube URL is required.")
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("YouTube URL should start with http:// or https://.")

    output_dir = safe_relative_output_dir(payload.get("youtubeOutputDir"), YOUTUBE_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    mode = str(payload.get("youtubeMode") or "video").strip().lower()
    quality = str(payload.get("youtubeQuality") or "best").strip().lower()
    subtitle_languages = str(payload.get("youtubeSubtitleLanguages") or "ru,en,bg,es,pt").strip()
    cookies_browser = str(payload.get("youtubeCookiesBrowser") or "none").strip().lower()
    output_prefix = str(payload.get("youtubeOutputPrefix") or "").strip()

    command = [
        sys.executable or "python",
        str(ROOT / "download_youtube.py"),
        "--url",
        url,
        "--mode",
        mode,
        "--quality",
        quality,
        "--output-dir",
        str(output_dir),
    ]
    if subtitle_languages:
        command.extend(["--subtitle-languages", subtitle_languages])
    if cookies_browser and cookies_browser != "none":
        command.extend(["--cookies-from-browser", cookies_browser])
    if output_prefix:
        command.extend(["--output-prefix", output_prefix])
    return command, output_dir


def build_content_generate_command(payload):
    subtitles = str(payload.get("contentSubtitles") or "").strip()
    if not subtitles:
        subtitles = latest_youtube_download("subtitle")
    if not subtitles:
        raise ValueError("Subtitles file is required.")
    if not Path(subtitles).is_file():
        raise ValueError(f"Subtitles file was not found: {subtitles}")

    text_for_hash = subtitles + str(payload.get("contentMode") or "") + str(payload.get("contentRewriteDepth") or "")
    fallback_prefix = f"content_{time.strftime('%Y%m%d_%H%M%S')}_{hashlib.sha1(text_for_hash.encode('utf-8')).hexdigest()[:8]}"
    output_prefix = safe_output_prefix(payload.get("contentOutputPrefix"), fallback_prefix)

    command = [
        sys.executable or "python",
        str(ROOT / "generate_content_from_subtitles.py"),
        "--subtitles",
        subtitles,
        "--source-language",
        str(payload.get("contentSourceLanguage") or "auto"),
        "--target-language",
        str(payload.get("contentTargetLanguage") or "Russian"),
        "--mode",
        str(payload.get("contentMode") or "YouTube 8-15 minutes"),
        "--rewrite-depth",
        str(payload.get("contentRewriteDepth") or "strong"),
        "--target-duration-minutes",
        str(float(payload.get("contentTargetDuration") or 10.0)),
        "--style",
        str(payload.get("contentStyle") or "Documentary YouTube narration, clear and engaging."),
        "--image-style",
        str(payload.get("contentImageStyle") or "Cinematic realistic documentary frame, high detail."),
        "--aspect-ratio",
        str(payload.get("contentAspectRatio") or "16:9"),
        "--visual-count",
        str(int(payload.get("contentVisualCount") or 16)),
        *llm_script_args(),
        "--output-dir",
        str(payload.get("contentOutputDir") or "outputs"),
        "--output-prefix",
        output_prefix,
        "--temperature",
        str(float(payload.get("contentTemperature") or 0.45)),
    ]
    if not payload.get("contentGenerateVisuals"):
        command.append("--voice-only")
    return command


def content_script_base(script_path):
    name = Path(script_path).name
    if name.endswith(".content.script.txt"):
        name = name[: -len(".content.script.txt")]
    else:
        name = Path(name).stem
    return safe_output_prefix("", name or "content_voice")


def build_content_voice_prepare_command(payload):
    script_file = str(payload.get("contentScriptFile") or "").strip()
    if not script_file:
        script_file = latest_content_script_text()
    if not script_file:
        raise ValueError("Content script file is required.")
    script_path = Path(script_file)
    if not script_path.is_file():
        raise ValueError(f"Content script file was not found: {script_file}")

    ref_audio = str(payload.get("contentRefAudio") or "").strip()
    if ref_audio and not Path(ref_audio).is_file():
        raise ValueError(f"Content reference audio was not found: {ref_audio}")
    ref_text = str(payload.get("contentRefText") or "").strip()

    target_language = str(payload.get("contentTargetLanguage") or "Russian").strip() or "Russian"
    language_id = language_id_for(target_language) or "ru"
    language_name = LANGUAGE_NAMES_BY_ID.get(language_id, target_language)
    output_prefix = safe_output_prefix(payload.get("contentVoiceOutputPrefix"), content_script_base(script_path))

    command = [
        sys.executable or "python",
        str(ROOT / "prepare_script.py"),
        "--text-file",
        str(script_path),
        "--language",
        language_name,
        "--language-id",
        language_id,
        *llm_script_args(),
        "--output-dir",
        str(payload.get("contentOutputDir") or "outputs"),
        "--output-prefix",
        output_prefix,
        "--temperature",
        str(float(payload.get("contentVoiceTemperature") or payload.get("contentTemperature") or 0.35)),
        "--style",
        str(payload.get("contentVoiceStyle") or "Expressive YouTube narration with natural pauses, clear emotion labels, and clean spoken text."),
    ]
    if ref_audio:
        command.extend(["--ref-audio", ref_audio])
    if ref_text:
        command.extend(["--ref-text", ref_text])
    return command


def build_content_voice_command(payload):
    jsonl = str(payload.get("contentVoiceJsonl") or "").strip()
    if not jsonl:
        jsonl = latest_content_script_jsonl()
    if not jsonl:
        raise ValueError("Content voice plan (.script.dub.jsonl) is required.")
    if not Path(jsonl).is_file():
        raise ValueError(f"Content voice plan was not found: {jsonl}")
    output = str(payload.get("contentVoiceOutput") or "").strip()
    return dub_command(jsonl, output, payload, "contentVoice")


def build_ytdlp_check_command():
    return [sys.executable or "python", str(ROOT / "download_youtube.py"), "--check"]


def build_ytdlp_update_command():
    return [sys.executable or "python", str(ROOT / "download_youtube.py"), "--update"]


def build_youtube_subtitles_command(payload):
    url = str(payload.get("youtubeUrl") or "").strip()
    if not url:
        raise ValueError("YouTube URL is required.")
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("YouTube URL should start with http:// or https://.")

    cookies_browser = str(payload.get("youtubeCookiesBrowser") or "none").strip().lower()
    command = [
        sys.executable or "python",
        str(ROOT / "download_youtube.py"),
        "--list-subs",
        "--url",
        url,
    ]
    if cookies_browser and cookies_browser != "none":
        command.extend(["--cookies-from-browser", cookies_browser])
    return command


def detect_youtube_subtitles(payload):
    result = subprocess.run(
        build_youtube_subtitles_command(payload),
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=190,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Subtitle detection failed.").strip())
    return json.loads(result.stdout)


def pump_output(process, on_complete=None):
    try:
        for line in process.stdout:
            JOB.append(line)
    finally:
        code = process.wait()
        result = {}
        if code == 0 and on_complete:
            try:
                result = on_complete()
            except Exception as exc:
                code = 1
                JOB.append(str(exc))
        with JOB.lock:
            JOB.exit_code = code
            JOB.finished_at = time.time()
            JOB.result = result


JOB_START_LOCK = threading.Lock()


def start_job(command, kind, on_complete=None):
    # One lock around check + spawn, so two quick clicks cannot start two jobs.
    with JOB_START_LOCK:
        with JOB.lock:
            if JOB.process is not None and JOB.process.poll() is None:
                raise RuntimeError("A job is already running.")

        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=job_environment(),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        with JOB.lock:
            JOB.process = process
            JOB.started_at = time.time()
            JOB.finished_at = None
            JOB.exit_code = None
            JOB.command = command
            JOB.kind = kind
            JOB.lines = ["> " + " ".join(command)]
            JOB.result = {}

    threading.Thread(target=pump_output, args=(process, on_complete), daemon=True).start()


def start_translation_job(payload):
    start_job(build_command(payload), "translate")


def start_dubbing_job(payload):
    start_job(build_dubbing_command(payload), "dubbing")


def start_music_mix_job(payload):
    start_job(build_music_mix_command(payload), "music_mix")


def start_transcription_job(payload):
    start_job(build_transcription_command(payload), "transcription")


def start_text_translation_job(payload):
    start_job(build_text_translation_command(payload), "text_translation")


def start_text_voice_job(payload):
    start_job(build_text_voice_command(payload), "text_voice")


def start_script_prepare_job(payload):
    start_job(build_script_prepare_command(payload), "script_prepare")


def start_script_voice_job(payload):
    start_job(build_script_voice_command(payload), "script_voice")


def start_content_generate_job(payload):
    start_job(build_content_generate_command(payload), "content_generate")


def start_content_voice_prepare_job(payload):
    start_job(build_content_voice_prepare_command(payload), "content_voice_prepare")


def start_content_voice_job(payload):
    start_job(build_content_voice_command(payload), "content_voice")


def start_clean_wav_job(payload):
    command, output_path = build_clean_wav_command(payload)

    def on_complete():
        if not output_path.is_file():
            raise RuntimeError(f"Cleaned WAV was not created: {output_path}")
        return {"wav": str(output_path)}

    start_job(command, "wav_clean", on_complete=on_complete)


def start_youtube_download_job(payload):
    command, output_dir = build_youtube_download_command(payload)
    before = {str(path.resolve()) for path in output_dir.glob("*") if path.is_file()}

    def on_complete():
        downloads = [
            str(path.resolve())
            for path in sorted(output_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True)
            if path.is_file() and str(path.resolve()) not in before
        ]
        return {"downloads": downloads}

    start_job(command, "youtube_download", on_complete=on_complete)


def start_ytdlp_update_job():
    start_job(build_ytdlp_update_command(), "yt_dlp_update")


def start_reference_transcribe_job(payload):
    command, txt_path = build_reference_transcribe_command(payload)

    def on_complete():
        if not txt_path.is_file():
            raise RuntimeError(f"Whisper did not create expected text output: {txt_path}")
        text = txt_path.read_text(encoding="utf-8", errors="replace").strip()
        return {"text": text, "txt": str(txt_path)}

    start_job(command, "reference_transcribe", on_complete=on_complete)


def start_engine_install_job(payload):
    engine_id = str(payload.get("engine") or "").strip()
    command = tts_engines.install_command(engine_id)

    def on_complete():
        manifest = tts_engines.load_manifest(engine_id)
        status = tts_engines.engine_status(manifest)
        if not status.get("installed"):
            raise RuntimeError(f"Install finished but {manifest['name']} is still not available.")
        return {"engine": engine_id, "status": status}

    start_job(command, "engine_install", on_complete=on_complete)


def start_engine_test_job(payload):
    engine_id = str(payload.get("engine") or load_settings()["tts"].get("engine") or "omnivoice")
    tts_engines.load_manifest(engine_id)  # validates the id
    OUTPUTS_DIR.mkdir(exist_ok=True)
    output = OUTPUTS_DIR / f"engine_test_{engine_id}_{time.strftime('%H%M%S')}.wav"
    command = [
        sys.executable or "python",
        str(ROOT / "tts_engines.py"),
        "--engine", engine_id,
        "--device", str(load_settings()["tts"].get("device") or "auto"),
        "--text", str(payload.get("text") or "Hello! This is a voice test."),
        "--out", str(output),
    ]
    if payload.get("language"):
        command += ["--language", str(payload["language"])]
    ref_audio = str(payload.get("refAudio") or "").strip()
    if ref_audio:
        if not Path(ref_audio).is_file():
            raise ValueError(f"Reference audio was not found: {ref_audio}")
        command += ["--ref-audio", ref_audio]
        if payload.get("refText"):
            command += ["--ref-text", str(payload["refText"])]

    def on_complete():
        if not output.is_file():
            raise RuntimeError("The engine finished without writing audio.")
        return {"wav": str(output), "name": output.name}

    start_job(command, "engine_test", on_complete=on_complete)


def llm_from_payload(payload):
    """Client for the settings form (may differ from saved settings while editing)."""
    llm = dict(load_settings()["llm"])
    for key in ("provider", "url", "model", "contextLength"):
        if key in payload:
            llm[key] = payload[key]
    return llm_from_settings(llm)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        if not request_is_local(self):
            self.send_error(403, "Only local requests are allowed.")
            return
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/state":
            return json_response(
                self,
                {
                    "job": JOB.snapshot(),
                    "outputs": list_outputs(),
                    "youtube": list_youtube_downloads(),
                },
            )
        if path == "/api/settings":
            return json_response(self, {"ok": True, **settings_payload()})
        if path == "/api/llm/models":
            query = parse_qs(parsed.query)
            payload = {key: values[0] for key, values in query.items() if values}
            try:
                client = llm_from_payload(payload)
                return json_response(self, {"ok": True, "models": client.list_models(), "label": client.label})
            except llm_client.LLMError as exc:
                return json_response(self, {"ok": False, "error": str(exc), "models": []}, 200)
        if path == "/api/file":
            query = parse_qs(parsed.query)
            file_path = output_file_from_query(query)
            if not file_path:
                return json_response(self, {"error": "File not found."}, 404)
            return json_response(
                self,
                {
                    "name": file_path.name,
                    "text": file_path.read_text(encoding="utf-8", errors="replace"),
                },
            )
        if path == "/api/output":
            query = parse_qs(parsed.query)
            file_path = output_file_from_query(query)
            if not file_path:
                self.send_error(404)
                return
            serve_output_file(self, file_path)
            return
        if path == "/api/youtube-file":
            query = parse_qs(parsed.query)
            file_path = youtube_file_from_query(query)
            if not file_path:
                self.send_error(404)
                return
            serve_output_file(self, file_path)
            return
        if path == "/api/local-audio":
            query = parse_qs(parsed.query)
            file_path = audio_file_from_query(query)
            if not file_path:
                self.send_error(404)
                return
            serve_output_file(self, file_path)
            return
        if path == "/api/youtube-text":
            query = parse_qs(parsed.query)
            file_path = youtube_file_from_query(query)
            if not file_path:
                return json_response(self, {"error": "File not found."}, 404)
            return json_response(
                self,
                {
                    "name": file_path.name,
                    "text": file_path.read_text(encoding="utf-8", errors="replace"),
                },
            )
        if path == "/api/latest-dub-jsonl":
            return json_response(self, {"ok": True, "path": latest_dub_jsonl()})
        if path == "/api/latest-audio":
            return json_response(self, {"ok": True, "path": latest_output_audio()})
        if path == "/api/latest-youtube":
            query = parse_qs(parsed.query)
            media_kind = query.get("kind", [""])[0]
            return json_response(self, {"ok": True, "path": latest_youtube_download(media_kind)})
        if path == "/api/latest-translations":
            return json_response(self, {"ok": True, "path": latest_translations_json()})
        if path == "/api/latest-script-jsonl":
            return json_response(self, {"ok": True, "path": latest_script_jsonl()})
        if path == "/api/latest-content-script":
            return json_response(self, {"ok": True, "path": latest_content_script_text()})
        if path == "/api/latest-content-jsonl":
            return json_response(self, {"ok": True, "path": latest_content_script_jsonl()})
        if path == "/api/yt-dlp-status":
            result = subprocess.run(
                build_ytdlp_check_command(),
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return json_response(
                self,
                {
                    "ok": True,
                    "available": result.returncode == 0,
                    "message": (result.stdout or result.stderr).strip(),
                },
                200,
            )

        if path == "/":
            path = "/index.html"
        file_path = (UI_DIR / path.lstrip("/")).resolve()
        if UI_DIR.resolve() not in file_path.parents and file_path != (UI_DIR / "index.html").resolve():
            self.send_error(403)
            return
        if not file_path.is_file():
            self.send_error(404)
            return

        content = file_path.read_bytes()
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        if not request_is_local(self):
            self.send_error(403, "Only local requests are allowed.")
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/run":
                payload = read_json(self)
                start_translation_job(payload)
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/run-dubbing":
                payload = read_json(self)
                start_dubbing_job(payload)
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/run-music-mix":
                payload = read_json(self)
                start_music_mix_job(payload)
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/run-transcription":
                payload = read_json(self)
                start_transcription_job(payload)
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/run-text-translation":
                payload = read_json(self)
                start_text_translation_job(payload)
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/run-text-voice":
                payload = read_json(self)
                start_text_voice_job(payload)
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/run-script-prepare":
                payload = read_json(self)
                start_script_prepare_job(payload)
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/run-script-voice":
                payload = read_json(self)
                start_script_voice_job(payload)
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/run-content-generate":
                payload = read_json(self)
                start_content_generate_job(payload)
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/run-content-voice-prepare":
                payload = read_json(self)
                start_content_voice_prepare_job(payload)
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/run-content-voice":
                payload = read_json(self)
                start_content_voice_job(payload)
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/run-clean-wav":
                payload = read_json(self)
                start_clean_wav_job(payload)
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/run-youtube-download":
                payload = read_json(self)
                start_youtube_download_job(payload)
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/run-ytdlp-update":
                start_ytdlp_update_job()
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/youtube-subtitles":
                payload = read_json(self)
                return json_response(self, {"ok": True, **detect_youtube_subtitles(payload)})
            if parsed.path == "/api/stop":
                with JOB.lock:
                    process = JOB.process
                if process is not None and process.poll() is None:
                    terminate_process_tree(process)
                    JOB.append("Stop requested.")
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/settings":
                payload = read_json(self)
                save_settings(payload)
                return json_response(self, {"ok": True, **settings_payload()})
            if parsed.path == "/api/llm/key":
                payload = read_json(self)
                provider = str(payload.get("provider") or "").strip()
                if not provider:
                    raise ValueError("provider is required.")
                llm_client.save_api_key(provider, str(payload.get("apiKey") or "").strip())
                return json_response(self, {"ok": True, **settings_payload()})
            if parsed.path == "/api/llm/test":
                payload = read_json(self)
                client = llm_from_payload(payload)
                started = time.time()
                client.prepare()
                answer = client.complete(
                    [{"role": "user", "content": "Translate to English and answer with the translation only: Привет, мир!"}],
                    temperature=0.0,
                    timeout=180,
                    max_tokens=60,
                )
                return json_response(self, {
                    "ok": True,
                    "model": client.active_model,
                    "answer": answer[:300],
                    "seconds": round(time.time() - started, 1),
                })
            if parsed.path == "/api/llm/unload":
                payload = read_json(self)
                llm_from_payload(payload).unload_all()
                return json_response(self, {"ok": True})
            if parsed.path == "/api/engines/install":
                payload = read_json(self)
                start_engine_install_job(payload)
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/engines/test":
                payload = read_json(self)
                start_engine_test_job(payload)
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/engines/python":
                payload = read_json(self)
                tts_engines.set_python_override(str(payload.get("engine") or ""), str(payload.get("python") or "").strip())
                return json_response(self, {"ok": True, **settings_payload()})
            if parsed.path == "/api/engines/voices":
                payload = read_json(self)
                engine_id = str(payload.get("engine") or "")
                manifest = tts_engines.load_manifest(engine_id)
                settings = tts_engines.resolve_settings(manifest, payload.get("settings") or {})
                with tts_engines.EngineSession(engine_id, log=lambda message: None, startup_timeout=120) as session:
                    voices = session.voices(settings)
                return json_response(self, {"ok": True, "voices": voices})
            if parsed.path == "/api/select-file":
                payload = read_json(self)
                path = choose_file(str(payload.get("kind") or "audio"))
                return json_response(self, {"ok": True, "path": path})
            if parsed.path == "/api/upload-file":
                result = upload_file(self)
                return json_response(self, {"ok": True, **result})
            if parsed.path == "/api/transcribe-reference":
                payload = read_json(self)
                start_reference_transcribe_job(payload)
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
        except Exception as exc:
            return json_response(self, {"ok": False, "error": str(exc)}, 400)

        self.send_error(404)


def main():
    parser = argparse.ArgumentParser(description="Local UI for the timed translation pipeline.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Pipeline UI: http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
