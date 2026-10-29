"""TTS engine registry and worker client (host side).

Every engine lives in engines/<id>/ with:
  engine.json     manifest: name, license, languages, capabilities, settings schema
  adapter.py      load(settings, device) + synthesize(text, ...) -> (audio, sample_rate)
  pyproject.toml  dependencies for the engine's own environment (optional)

Engines run in their own Python environment because TTS stacks pin conflicting
torch/transformers versions. The host starts engines/worker.py with that
environment's interpreter and exchanges JSON lines with it.

Which interpreter is used, in order:
  1. engines/<id>/engine.local.json  {"python": "D:/somewhere/python.exe"}  (not committed)
  2. engines/<id>/.venv              created by "Install engine" (uv sync)
  3. the project's own interpreter    for runtime "main" engines (HTTP clients)
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ENGINES_DIR = ROOT / "engines"
WORKER = ENGINES_DIR / "worker.py"
SECRETS_FILE = ROOT / "config" / "secrets.local.json"
TTS_API_KEY_ENV = "DUB_TTS_API_KEY"


class EngineError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Registry


def engine_dir(engine_id):
    path = (ENGINES_DIR / str(engine_id)).resolve()
    if path.parent != ENGINES_DIR.resolve() or not (path / "engine.json").is_file():
        raise EngineError(f"Unknown TTS engine: {engine_id}")
    return path


def load_manifest(engine_id):
    directory = engine_dir(engine_id)
    manifest = json.loads((directory / "engine.json").read_text(encoding="utf-8"))
    manifest["id"] = directory.name
    manifest.setdefault("name", directory.name)
    manifest.setdefault("runtime", "venv")
    manifest.setdefault("kind", "tts")
    manifest.setdefault("supports", {})
    manifest.setdefault("settings", [])
    return manifest


def section_for(manifest):
    """Settings section of an engine: "tts" for voices, "separation" for vocal separators."""
    return "separation" if manifest.get("kind") == "separator" else "tts"


def list_engines(include_template=False, kind="tts"):
    """Engines of one kind ("tts" or "separator"); kind=None lists all."""
    engines = []
    for directory in sorted(ENGINES_DIR.iterdir()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        if directory.name.startswith("_") and not include_template:
            continue
        if not (directory / "engine.json").is_file():
            continue
        try:
            manifest = load_manifest(directory.name)
        except (OSError, json.JSONDecodeError, EngineError) as exc:
            engines.append({"id": directory.name, "name": directory.name, "error": str(exc)})
            continue
        if kind and manifest.get("kind", "tts") != kind:
            continue
        manifest["status"] = engine_status(manifest)
        engines.append(manifest)
    return engines


def local_overrides(engine_id):
    path = engine_dir(engine_id) / "engine.local.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def set_python_override(engine_id, python_path):
    path = engine_dir(engine_id) / "engine.local.json"
    data = local_overrides(engine_id)
    if python_path:
        if not Path(python_path).is_file():
            raise EngineError(f"Python interpreter not found: {python_path}")
        data["python"] = str(Path(python_path).resolve())
    else:
        data.pop("python", None)
    if data:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()


def venv_python(directory):
    directory = Path(directory)
    if os.name == "nt":
        return directory / ".venv" / "Scripts" / "python.exe"
    return directory / ".venv" / "bin" / "python"


def engine_python(manifest):
    """Return (python_path or None, source)."""
    override = local_overrides(manifest["id"]).get("python")
    if override:
        return (override if Path(override).is_file() else None), "override"
    if manifest.get("runtime") == "main":
        return sys.executable, "main"
    candidate = venv_python(engine_dir(manifest["id"]))
    return (str(candidate) if candidate.is_file() else None), "venv"


def engine_status(manifest):
    python, source = engine_python(manifest)
    if python:
        messages = {
            "override": "Готов: используется указанное окружение Python (engine.local.json).",
            "venv": "Готов: установлен в папку движка.",
            "main": "Готов: установка не нужна, работает в основном окружении.",
        }
        return {"installed": True, "python": python, "source": source, "message": messages[source]}
    if source == "override":
        return {"installed": False, "python": "", "source": source, "message": "Указанный в engine.local.json Python не найден."}
    installable = (engine_dir(manifest["id"]) / "pyproject.toml").is_file()
    return {
        "installed": False,
        "python": "",
        "source": source,
        "installable": installable,
        "message": "Не установлен. Нажмите «Установить движок» или укажите готовое окружение Python.",
    }


def install_command(engine_id):
    manifest = load_manifest(engine_id)
    directory = engine_dir(engine_id)
    if manifest.get("runtime") == "main":
        raise EngineError(f"{manifest['name']} needs no install.")
    if not (directory / "pyproject.toml").is_file():
        raise EngineError(f"{manifest['name']} has no pyproject.toml; install it manually and set its Python path.")
    uv = shutil.which("uv")
    if not uv:
        raise EngineError("uv was not found. Install it: https://docs.astral.sh/uv/ (one command), then retry.")
    return [uv, "sync", "--project", str(directory), "--no-install-project"]


# --------------------------------------------------------------------------
# Settings


def load_app_settings():
    from app_settings import load_settings

    return load_settings()


def default_settings(manifest):
    return {item["key"]: item.get("default") for item in manifest.get("settings") or [] if "key" in item}


def resolve_settings(manifest, *overrides):
    """Manifest defaults <- saved UI settings <- explicit overrides."""
    settings = default_settings(manifest)
    saved = ((load_app_settings().get(section_for(manifest)) or {}).get("engines") or {}).get(manifest["id"]) or {}
    for layer in (saved, *overrides):
        settings.update({key: value for key, value in (layer or {}).items() if value is not None})
    return settings


def resolve_tts_api_key(engine_id):
    if os.environ.get(TTS_API_KEY_ENV):
        return os.environ[TTS_API_KEY_ENV]
    try:
        secrets = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str((secrets.get("tts_api_keys") or {}).get(engine_id) or "")


# --------------------------------------------------------------------------
# Worker session


class EngineSession:
    """One running engine worker. Use as a context manager."""

    def __init__(self, engine_id, log=None, startup_timeout=600):
        self.manifest = load_manifest(engine_id)
        self.engine_id = self.manifest["id"]
        self.log = log or (lambda message: print(message, flush=True))
        self.startup_timeout = startup_timeout
        self.process = None
        self._lines = queue.Queue()
        self._next_id = 1
        self.sample_rate = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc_info):
        self.close()

    def start(self):
        python, source = engine_python(self.manifest)
        if not python:
            raise EngineError(
                f"TTS engine '{self.manifest['name']}' is not installed. "
                f"Install it in the Engines tab or set its Python in engines/{self.engine_id}/engine.local.json."
            )
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        api_key = resolve_tts_api_key(self.engine_id)
        if api_key:
            env[TTS_API_KEY_ENV] = api_key
        self.log(f"TTS engine: {self.manifest['name']} ({source}: {python})")
        self.process = subprocess.Popen(
            [python, "-u", str(WORKER), str(engine_dir(self.engine_id))],
            cwd=str(engine_dir(self.engine_id)),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        ready = self._read_reply(0, self.startup_timeout)
        if not ready.get("ok"):
            raise EngineError(self._format_error("Engine failed to start", ready))

    def _pump_stdout(self):
        for line in self.process.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _pump_stderr(self):
        last = ""
        for line in self.process.stderr:
            line = line.rstrip()
            if line.startswith("PROGRESS "):
                self.log(line)  # forwarded as-is so the UI shows a progress bar
                continue
            # Skip progress-bar redraws that repeat the same prefix.
            if not line or (last and line[:20] == last[:20] and "%|" in line):
                last = line or last
                continue
            last = line
            self.log(f"  [{self.engine_id}] {line}")

    def _read_reply(self, request_id, timeout):
        while True:
            try:
                line = self._lines.get(timeout=timeout)
            except queue.Empty:
                raise EngineError(f"TTS engine did not answer within {timeout}s.") from None
            if line is None:
                code = self.process.poll()
                raise EngineError(f"TTS engine process exited unexpectedly (code {code}). See log above.")
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self.log(f"  [{self.engine_id}] {line}")
                continue
            if message.get("id") == request_id:
                return message

    @staticmethod
    def _format_error(prefix, message):
        error = message.get("error") or "unknown error"
        trace = (message.get("traceback") or "").strip().splitlines()
        tail = "\n".join(trace[-6:]) if trace else ""
        return f"{prefix}: {error}" + (f"\n{tail}" if tail else "")

    def request(self, cmd, timeout=3600, **payload):
        if not self.process or self.process.poll() is not None:
            raise EngineError("TTS engine is not running.")
        request_id = self._next_id
        self._next_id += 1
        self.process.stdin.write(json.dumps({"id": request_id, "cmd": cmd, **payload}, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        reply = self._read_reply(request_id, timeout)
        if not reply.get("ok"):
            raise EngineError(self._format_error(f"{self.manifest['name']} {cmd} failed", reply))
        return reply.get("result") or {}

    def load(self, settings, device="auto"):
        result = self.request("load", timeout=self.startup_timeout, settings=settings, device=device)
        self.sample_rate = int(result.get("sample_rate") or 24000)
        return result

    def synthesize(self, text, out, language=None, voice=None, duration=None, speed=1.0, hints=None):
        voice = dict(voice or {})
        if voice.get("ref_audio"):
            # The worker runs in the engine folder, so relative paths would break.
            voice["ref_audio"] = str(Path(voice["ref_audio"]).resolve())
        return self.request(
            "synthesize",
            text=text,
            out=str(out),
            language=language,
            voice=voice or {},
            duration=duration,
            speed=speed,
            hints=hints or {},
        )

    def separate(self, input_path, out_dir, timeout=7200):
        """Separator engines: split a mix into vocals.wav + background.wav."""
        return self.request("separate", timeout=timeout, input=str(Path(input_path).resolve()), out_dir=str(Path(out_dir).resolve()))

    def voices(self, settings=None):
        return self.request("voices", timeout=120, settings=settings or {}).get("voices") or []

    def close(self):
        if not self.process:
            return
        if self.process.poll() is None:
            try:
                self.process.stdin.write(json.dumps({"id": -1, "cmd": "shutdown"}) + "\n")
                self.process.stdin.flush()
                self.process.wait(timeout=20)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(timeout=10)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except OSError:
                pass
        self.process = None


def main():
    """CLI: list engines, or synthesize one phrase to test an engine."""
    import argparse

    parser = argparse.ArgumentParser(description="List TTS engines or test one.")
    parser.add_argument("--engine", default="")
    parser.add_argument("--text", default="")
    parser.add_argument("--language", default=None)
    parser.add_argument("--ref-audio", default="")
    parser.add_argument("--ref-text", default="")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--out", default="outputs/engine_test.wav")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if not args.engine:
        for manifest in list_engines(kind=None):
            status = manifest.get("status") or {}
            mark = "+" if status.get("installed") else "-"
            print(f"{mark} {manifest['id']:<16} {manifest.get('name', '')}  [{status.get('message', manifest.get('error', ''))}]")
        return

    manifest = load_manifest(args.engine)
    with EngineSession(args.engine) as session:
        info = session.load(resolve_settings(manifest), args.device)
        print(f"loaded: {info}")
        voice = {"ref_audio": args.ref_audio, "ref_text": args.ref_text} if args.ref_audio else {}
        result = session.synthesize(args.text or "Hello! This is a test.", Path(args.out).resolve(), args.language, voice)
        print(f"result: {result}")


if __name__ == "__main__":
    main()
