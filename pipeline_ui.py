import argparse
import cgi
import hashlib
import json
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import i18n
import llm_client
import project as projects
import tts_engines
from app_settings import llm_script_args, load_settings, save_settings
from i18n import tr
from languages import LANGUAGE_NAMES_BY_ID


ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "ui"
OUTPUTS_DIR = ROOT / "outputs"
INPUTS_DIR = ROOT / "inputs"
CONFIG_DIR = ROOT / "config"



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
        self.progress = None
        self.stage = None
        self.meta = {}

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
                "progress": self.progress,
                "stage": self.stage,
                "meta": self.meta,
            }

    def append(self, line):
        line = line.rstrip("\n")
        parsed = parse_progress(line)
        with self.lock:
            if parsed:
                kind, value = parsed
                if kind == "stage":
                    self.stage = value
                    self.progress = None
                    self.lines.append(f"== {value['label']} ({value['done']}/{value['total']}) ==")
                else:
                    self.progress = value
                return
            self.lines.append(line)
            if len(self.lines) > 3000:
                self.lines = self.lines[-3000:]


def parse_progress(line):
    """'PROGRESS 3/10 label' or 'STAGE 2/4 label' -> (kind, {done, total, label})."""
    for prefix, kind in (("PROGRESS ", "progress"), ("STAGE ", "stage")):
        if line.startswith(prefix):
            head, _, label = line[len(prefix):].partition(" ")
            done, _, total = head.partition("/")
            try:
                return kind, {"done": int(done), "total": max(1, int(total)), "label": label.strip()}
            except ValueError:
                return None
    return None


JOB = JobState()


# --------------------------------------------------------------------------
# Engine settings (shared by every tab, saved in config/settings.local.json)






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






def job_environment():
    """Environment for child processes: UTF-8 logs and the LLM key (never on the command line)."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env[i18n.LANGUAGE_ENV] = i18n.language()
    llm = load_settings()["llm"]
    api_key = llm_client.stored_api_key(str(llm.get("provider") or ""))
    if api_key:
        env[llm_client.API_KEY_ENV] = api_key
    return env


def localized(item, keys):
    """Translate the user-facing texts of an engine manifest or provider preset."""
    for key in keys:
        if isinstance(item.get(key), str):
            item[key] = tr(item[key])
    return item


def engines_payload(kind="tts"):
    engines = tts_engines.list_engines(kind=kind)
    for manifest in engines:
        if "id" in manifest and "settings" in manifest:
            manifest["values"] = tts_engines.resolve_settings(manifest)
        localized(manifest, ("name", "description", "license", "install_note", "languages"))
        for item in manifest.get("settings") or []:
            localized(item, ("label", "help"))
    return engines


def ui_language_from(handler):
    """Saved interface language, or the browser's one when it was never chosen."""
    saved = i18n.normalize(load_settings()["ui"].get("language"))
    return saved or i18n.from_accept_language(handler.headers.get("Accept-Language"))


def locale_script(code):
    """JS that defines the interface language and its dictionary before the other scripts run."""
    payload = {
        "language": code,
        "languages": i18n.available_languages(),
        "messages": i18n.catalog(code),
    }
    return f"window.I18N = {json.dumps(payload, ensure_ascii=False)};\n".encode("utf-8")


def settings_payload():
    settings = load_settings()
    providers = [localized(dict(item), ("label", "hint")) for item in llm_client.load_providers().values()]
    keys = {item["id"]: llm_client.mask_key(llm_client.stored_api_key(item["id"])) for item in providers}
    return {
        "settings": settings,
        "providers": providers,
        "keys": keys,
        "engines": engines_payload("tts"),
        "separators": engines_payload("separator"),
        "resident": tts_engines.resident_status(),
    }


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
    # Re-voiced phrases and re-exported videos keep their file names.
    handler.send_header("Cache-Control", "no-cache")
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
        "cleanWav": "clean_wav",
    }
    prefix = prefixes.get(kind, "audio")
    candidate = INPUTS_DIR / f"{prefix}_{timestamp}_{safe_name}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = INPUTS_DIR / f"{prefix}_{timestamp}_{safe_name}_{counter}{suffix}"
        counter += 1
    return candidate


def receive_upload(handler, destination_for):
    """Stream a multipart "file" field to destination_for(filename, fields) and return its path."""
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

    fields = {key: form.getfirst(key, "") for key in form.keys() if key != "file"}
    destination = Path(destination_for(Path(file_item.filename).name, fields))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as f:
        while True:
            chunk = file_item.file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    return destination, fields


def upload_file(handler):
    destination, _ = receive_upload(handler, lambda name, fields: unique_input_path(name, str(fields.get("kind") or "audio")))
    return {
        "path": str(destination),
        "name": destination.name,
        "size": destination.stat().st_size,
    }


def safe_upload_name(name, fallback="file"):
    stem = safe_stem(name) or fallback
    suffix = re.sub(r"[^A-Za-z0-9.]", "", Path(name).suffix.lower())[:8]
    return stem + suffix


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
    try:
        os.killpg(process.pid, signal.SIGTERM)  # the job runs in its own session
    except (OSError, ProcessLookupError):
        process.terminate()




def build_reference_transcribe_command(payload):
    audio = str(payload.get("refAudio") or "").strip()
    if not audio:
        raise ValueError("Reference audio path is required.")
    if not Path(audio).is_file():
        raise ValueError(f"Reference audio was not found: {audio}")

    asr = load_settings()["asr"]
    output_dir = str(payload.get("outputDir") or "outputs")
    output_prefix = f"ref_{default_output_prefix(audio)}"
    command = [
        sys.executable or "python",
        str(ROOT / "transcribe.py"),
        audio,
        "--model", str(payload.get("whisperModel") or asr.get("model") or "medium"),
        "--language", "auto",
        "--device", str(payload.get("whisperDevice") or asr.get("device") or "cuda"),
        "--compute-type", str(payload.get("whisperComputeType") or asr.get("computeType") or "float16"),
        "--output-dir", output_dir,
        "--output-prefix", output_prefix,
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
    preset = str(payload.get("preset") or "custom")
    if preset not in {"custom", "clean", "youtube", "dense", "soft", "radio"}:
        preset = "custom"

    def payload_float(name, default):
        value = payload.get(name)
        if value is None or value == "":
            return default
        return float(value)

    command = [
        sys.executable or "python",
        str(ROOT / "clean_wav.py"),
        "--input", str(file_path),
        "--output", str(output_path),
        "--mode", mode,
        "--min-pause", str(float(payload.get("minPause") or 0.15)),
        "--fade-ms", str(payload_float("fadeMs", 40.0)),
        "--preset", preset,
        "--amount", str(payload_float("amount", 55.0)),
        "--deesser-freq", str(payload_float("deesserFreq", 5200.0)),
        "--deesser-strength", str(payload_float("deesserStrength", 55.0)),
        "--eq-mud-db", str(payload_float("eqMudDb", -1.8)),
        "--eq-clarity-db", str(payload_float("eqClarityDb", 2.2)),
        "--eq-air-db", str(payload_float("eqAirDb", 1.1)),
        "--comp-threshold", str(payload_float("compThreshold", -20.0)),
        "--comp-ratio", str(payload_float("compRatio", 3.0)),
        "--comp-makeup", str(payload_float("compMakeup", 2.2)),
        "--sat-drive", str(payload_float("satDrive", 45.0)),
        "--sat-mix", str(payload_float("satMix", 28.0)),
        "--limiter-ceiling", str(payload_float("limiterCeiling", 0.95)),
        "--loudness-target", str(payload_float("loudnessTarget", -16.0)),
        "--room-mix", str(payload_float("roomMix", 10.0)),
    ]
    if payload.get("normalize"):
        command.append("--normalize")
    for key in ("deesser", "eq", "compressor", "saturation", "limiter", "loudness", "room"):
        if payload.get(key):
            command.append(f"--{key}")
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
        sys.executable or "python",
        str(ROOT / "mix_music.py"),
        "--voice-audio", voice_audio,
        "--music-audio", music_audio,
        "--music-volume-db", str(volume_db),
    ]
    output = str(payload.get("musicOutput") or "").strip()
    if output:
        command.extend(["--output", output])
    return command


def build_transcription_command(payload):
    video = str(payload.get("transcribeVideo") or "").strip()
    audio = str(payload.get("transcribeAudio") or "").strip()
    media = video or audio
    if not media:
        raise ValueError("Choose a video or audio file to transcribe.")
    if not Path(media).is_file():
        raise ValueError(f"Media file was not found: {media}")

    # faster-whisper decodes video files directly (PyAV), no extraction step needed.
    asr = load_settings()["asr"]
    output_prefix = str(payload.get("transcribeOutputPrefix") or "").strip() or default_output_prefix(media)
    return [
        sys.executable or "python",
        str(ROOT / "transcribe.py"),
        media,
        "--model", str(payload.get("transcribeModel") or asr.get("model") or "medium"),
        "--language", str(payload.get("transcribeLanguage") or "auto"),
        "--device", str(payload.get("transcribeDevice") or asr.get("device") or "cuda"),
        "--compute-type", str(payload.get("transcribeComputeType") or asr.get("computeType") or "float16"),
        "--output-dir", str(payload.get("transcribeOutputDir") or "outputs"),
        "--output-prefix", output_prefix,
    ]


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


def start_job(command, kind, on_complete=None, meta=None):
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
            start_new_session=os.name != "nt",  # lets Stop kill the whole job on macOS/Linux
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
            JOB.progress = None
            JOB.stage = None
            JOB.meta = meta or {}

    threading.Thread(target=pump_output, args=(process, on_complete), daemon=True).start()


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


def start_clean_wav_job(payload):
    command, output_path = build_clean_wav_command(payload)

    def on_complete():
        if not output_path.is_file():
            raise RuntimeError(f"Cleaned WAV was not created: {output_path}")
        return {"wav": str(output_path)}

    start_job(command, "wav_clean", on_complete=on_complete)


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


PROJECT_ROUTE = re.compile(r"^/api/projects/([^/]+)(?:/([a-z-]+))?$")
MEDIA_UPLOAD_SUFFIXES = projects.VIDEO_SUFFIXES | projects.AUDIO_SUFFIXES


def project_file_url(project_id, relative):
    return f"/api/projects/{quote(project_id)}/file?path={quote(relative)}" if relative else ""


def project_detail(directory):
    data = projects.load_project(directory)
    project_id = data["id"]
    work = directory / "work"

    def file_entry(relative):
        if relative and (directory / relative).is_file():
            return {"path": relative, "url": project_file_url(project_id, relative), "name": Path(relative).name}
        return None

    files = {}
    shared = {
        "source": (data.get("source") or {}).get("file", ""),
        "vocals": "work/vocals.wav" if (work / "vocals.wav").is_file() else "",
        "background": "work/background.wav" if (work / "background.wav").is_file() else "",
        "sample": data["voice"].get("auto_file", ""),
        "uploaded_sample": data["voice"].get("file", ""),
        "music": data["mix"].get("music_file", ""),
    }
    for key, relative in shared.items():
        entry = file_entry(relative)
        if entry:
            files[key] = entry

    languages = []
    for target in data["targets"]:
        item = projects.track(data, target)
        language_files = {key: file_entry(item.get(key)) for key in projects.TRACK_FILES}
        languages.append({
            "name": target,
            "code": projects.code_for(target),
            "translated": item["translated"],
            "voiced": item["voiced"],
            "exported": item["exported"],
            "dirty": len(item.get("dirty") or []),
            "files": {key: value for key, value in language_files.items() if value},
        })
        if target == data["active"]:
            files.update({key: value for key, value in language_files.items() if value})

    active = data["active"]
    active_track = projects.track(data, active)
    segments = []
    report = projects.phrase_report(directory, data, active)
    phrase_dir = projects.segments_dir(directory, active)
    edited = set(active_track.get("edited") or [])
    dirty = set(active_track.get("dirty") or [])
    for unit in projects.load_units(directory, active):
        key = projects.unit_key(unit["id"])
        item_report = report.get(key) or {}
        phrase_wav = phrase_dir / f"{key}.wav"
        segments.append({
            "id": unit["id"],
            "start": unit.get("start"),
            "end": unit.get("end"),
            "duration": unit.get("duration"),
            "source": unit.get("source"),
            "translation": unit.get("translation"),
            "speedup": item_report.get("speedup"),
            "clipped": item_report.get("clipped_seconds"),
            "spoken": item_report.get("natural_seconds"),
            "edited": int(unit["id"]) in edited,
            "dirty": int(unit["id"]) in dirty,
            "voice_url": project_file_url(project_id, phrase_wav.relative_to(directory).as_posix()) if phrase_wav.is_file() else "",
        })

    settings = load_settings()
    separator = next((item for item in tts_engines.list_engines(kind="separator")
                      if item.get("id") == settings["separation"].get("engine")), None)
    snapshot = JOB.snapshot()
    return {
        **data,
        "files": files,
        "languages": languages,
        "active_track": {key: active_track[key] for key in ("translated", "voiced", "exported", "edited", "dirty")},
        "segments": segments,
        "separator": {
            "id": (separator or {}).get("id"),
            "name": (separator or {}).get("name"),
            "installed": bool((separator or {}).get("status", {}).get("installed")),
        },
        "running_job": snapshot["meta"].get("project") == project_id and snapshot["running"],
    }


def create_project_upload(handler):
    holder = {}

    def destination(name, fields):
        if Path(name).suffix.lower() not in MEDIA_UPLOAD_SUFFIXES:
            raise ValueError(tr("Нужен видео- или аудиофайл (mp4, mov, mkv, webm, wav, mp3, ...)."))
        directory = projects.new_project(str(fields.get("name") or ""), name)
        holder["dir"] = directory
        return directory / "source" / safe_upload_name(name, "source")

    try:
        source_path, fields = receive_upload(handler, destination)
        data = projects.attach_source(holder["dir"], source_path)
        changes = {}
        targets = [value.strip() for value in str(fields.get("targets") or fields.get("target") or "").split(",") if value.strip()]
        if targets:
            changes.update(targets=targets, active=targets[0])
        if fields.get("source_language"):
            changes["source_language"] = fields["source_language"]
        if changes:
            data = projects.update_project(holder["dir"], changes)
        return data
    except Exception:
        if holder.get("dir"):
            shutil.rmtree(holder["dir"], ignore_errors=True)
        raise


def project_upload(handler, directory, kind):
    if kind not in ("voice", "music"):
        raise ValueError("Unknown upload kind.")

    def destination(name, fields):
        if Path(name).suffix.lower() not in projects.AUDIO_SUFFIXES | projects.VIDEO_SUFFIXES:
            raise ValueError(tr("Нужен аудиофайл."))
        folder = "voice" if kind == "voice" else "source"
        return directory / folder / f"{kind}_{int(time.time())}{Path(safe_upload_name(name)).suffix}"

    saved, _ = receive_upload(handler, destination)
    relative = saved.relative_to(directory).as_posix()
    if kind == "voice":
        return projects.update_project(directory, {"voice": {"mode": "file", "file": relative, "ref_text": ""}})
    return projects.update_project(directory, {"mix": {"music_file": relative}})


def start_project_job(directory, stage_name, ids=None, targets=None):
    if stage_name not in projects.STAGES:
        raise ValueError(f"Unknown stage: {stage_name}")
    project_id = directory.name
    ids = [int(value) for value in ids or []]
    targets = [str(value) for value in targets or []]
    command = [sys.executable or "python", str(ROOT / "project.py"), "run", str(directory), stage_name]
    if ids:
        command += ["--ids", ",".join(str(value) for value in ids)]
    if targets:
        command += ["--targets", ",".join(targets)]

    def on_complete():
        return {"project": project_id, "stage": stage_name}

    start_job(command, "project", on_complete=on_complete,
              meta={"project": project_id, "stage": stage_name, "ids": ids, "targets": targets})


def open_folder(path):
    if os.name == "nt":
        os.startfile(str(path))  # noqa: S606 - local desktop action requested from the local UI
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def handle_project_get(handler, parsed):
    match = PROJECT_ROUTE.match(parsed.path)
    if not match:
        return False
    directory = projects.project_dir(unquote(match.group(1)))
    action = match.group(2) or ""
    if action == "":
        json_response(handler, {"ok": True, "project": project_detail(directory)})
        return True
    if action == "file":
        relative = unquote(parse_qs(parsed.query).get("path", [""])[0])
        file_path = (directory / relative).resolve()
        if directory.resolve() not in file_path.parents or not file_path.is_file():
            handler.send_error(404)
            return True
        serve_output_file(handler, file_path)
        return True
    return False


def handle_project_post(handler, parsed):
    if parsed.path == "/api/projects":
        data = create_project_upload(handler)
        json_response(handler, {"ok": True, "project": project_detail(projects.project_dir(data["id"]))})
        return True
    match = PROJECT_ROUTE.match(parsed.path)
    if not match:
        return False
    directory = projects.project_dir(unquote(match.group(1)))
    action = match.group(2) or ""
    if action == "update":
        projects.update_project(directory, read_json(handler))
    elif action == "upload":
        project_upload(handler, directory, parse_qs(parsed.query).get("kind", [""])[0])
    elif action == "run":
        payload = read_json(handler)
        start_project_job(directory, str(payload.get("stage") or "all"), payload.get("ids"), payload.get("targets"))
    elif action == "phrases":
        payload = read_json(handler)
        data = projects.load_project(directory)
        projects.set_translations(directory, data, {int(key): value for key, value in (payload.get("translations") or {}).items()},
                                  payload.get("target") or None)
    elif action == "sample":
        projects.choose_sample(directory, int(read_json(handler).get("index") or 0))
    elif action == "delete":
        projects.delete_project(directory)
        json_response(handler, {"ok": True, "projects": projects.list_projects()})
        return True
    elif action == "open":
        open_folder(directory / "output")
    else:
        return False
    json_response(handler, {"ok": True, "project": project_detail(directory), "job": JOB.snapshot()})
    return True


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
                },
            )
        if path == "/api/settings":
            return json_response(self, {"ok": True, **settings_payload()})
        if path == "/locale.js":
            code = ui_language_from(self)
            i18n.set_language(code)  # server messages and new jobs follow the page language
            content = locale_script(code)
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)
            return
        if path == "/api/projects":
            return json_response(self, {"ok": True, "projects": projects.list_projects()})
        if path.startswith("/api/projects/"):
            try:
                if handle_project_get(self, parsed):
                    return
            except projects.ProjectError as exc:
                return json_response(self, {"ok": False, "error": str(exc)}, 404)
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
        if path == "/api/local-audio":
            query = parse_qs(parsed.query)
            file_path = audio_file_from_query(query)
            if not file_path:
                self.send_error(404)
                return
            serve_output_file(self, file_path)
            return
        if path == "/api/latest-audio":
            return json_response(self, {"ok": True, "path": latest_output_audio()})
        if path == "/api/latest-translations":
            return json_response(self, {"ok": True, "path": latest_translations_json()})
        if path == "/api/latest-script-jsonl":
            return json_response(self, {"ok": True, "path": latest_script_jsonl()})

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
            if parsed.path.startswith("/api/projects") and handle_project_post(self, parsed):
                return
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
            if parsed.path == "/api/run-clean-wav":
                payload = read_json(self)
                start_clean_wav_job(payload)
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/stop":
                with JOB.lock:
                    process = JOB.process
                if process is not None and process.poll() is None:
                    terminate_process_tree(process)
                    JOB.append("Stop requested.")
                return json_response(self, {"ok": True, "job": JOB.snapshot()})
            if parsed.path == "/api/settings":
                payload = read_json(self)
                saved = save_settings(payload)
                if isinstance(payload.get("ui"), dict) and "language" in payload["ui"]:
                    i18n.set_language(i18n.normalize(saved["ui"]["language"])
                                      or i18n.from_accept_language(self.headers.get("Accept-Language")))
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
                    [{"role": "user", "content": "Translate to English and answer with the translation only: Привет, мир!"}],  # i18n-ignore: test prompt
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
            if parsed.path == "/api/engines/release":
                stopped = tts_engines.stop_resident(read_json(self).get("engine") or None)
                return json_response(self, {"ok": True, "stopped": stopped, **settings_payload()})
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
    parser.add_argument("--open", action="store_true", help="Open the UI in the default browser.")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"Dubbing Studio: {url}", flush=True)
    if args.open:
        import webbrowser

        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
