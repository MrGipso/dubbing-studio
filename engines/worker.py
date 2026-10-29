"""TTS engine worker: runs inside the engine's own Python environment.

The dubbing core starts it as a subprocess:

    <engine python> engines/worker.py engines/<engine_id>

and talks to it with one JSON object per line:

    -> {"id": 1, "cmd": "load", "settings": {...}, "device": "cuda"}
    <- {"id": 1, "ok": true, "result": {"sample_rate": 24000}}
    -> {"id": 2, "cmd": "synthesize", "text": "...", "language": "en",
        "voice": {"ref_audio": "...", "ref_text": "..."}, "duration": 3.2,
        "speed": 1.0, "hints": {"instruct": "...", "emotion": "calm"},
        "out": "C:/.../seg_0001.wav"}
    <- {"id": 2, "ok": true, "result": {"path": "...", "sample_rate": 24000, "seconds": 3.1}}
    -> {"id": 3, "cmd": "voices"}            (optional, engines with a voice list)
    -> {"id": 3, "cmd": "separate", "input": "mix.wav", "out_dir": "..."}
    <- {"id": 3, "ok": true, "result": {"vocals": ".../vocals.wav", "background": ".../background.wav"}}
    -> {"id": 3, "cmd": "diarize", "input": "speech_16k.wav", "num_speakers": null}
    <- {"id": 3, "ok": true, "result": {"turns": [{"start": 0.0, "end": 2.5, "speaker": 0}, ...]}}
    -> {"id": 4, "cmd": "shutdown"}

Only this file and the engine's adapter.py run in the engine environment, so
it depends on nothing but the standard library and numpy (every TTS stack has
numpy). Library output printed to stdout is redirected to stderr, which keeps
the protocol channel clean.
"""

import importlib.util
import json
import os
import sys
import traceback
import wave
from pathlib import Path


def open_protocol_channel():
    # Keep the real stdout for protocol messages, then point fd 1 (and
    # sys.stdout) at stderr so prints from torch/transformers/tqdm cannot
    # corrupt the JSON stream.
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return os.fdopen(protocol_fd, "w", encoding="utf-8", buffering=1)


def load_adapter(engine_dir):
    adapter_path = engine_dir / "adapter.py"
    spec = importlib.util.spec_from_file_location(f"engine_adapter_{engine_dir.name}", adapter_path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(engine_dir))
    spec.loader.exec_module(module)
    if not callable(getattr(module, "load", None)):
        raise RuntimeError(f"{adapter_path} must define a function 'load'.")
    if not any(callable(getattr(module, name, None)) for name in ("synthesize", "separate", "diarize")):
        raise RuntimeError(f"{adapter_path} must define 'synthesize' (TTS), 'separate' (separator) or 'diarize' (diarizer).")
    return module


def write_wav(path, audio, sample_rate):
    import numpy as np

    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import soundfile as sf

        sf.write(str(path), audio, int(sample_rate), subtype="FLOAT")
    except ImportError:
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(int(sample_rate))
            wav.writeframes(pcm.tobytes())
    return len(audio) / float(sample_rate)


def to_mono_float(audio):
    import numpy as np

    if hasattr(audio, "detach"):  # torch tensor
        audio = audio.detach().float().cpu().numpy()
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        # (channels, samples) or (samples, channels) -> mono
        axis = 0 if audio.shape[0] < audio.shape[-1] else -1
        audio = audio.mean(axis=axis)
    return audio.reshape(-1)


def handle(adapter, state, message):
    cmd = message.get("cmd")
    if cmd == "load":
        info = adapter.load(message.get("settings") or {}, message.get("device") or "cuda") or {}
        state["sample_rate"] = int(info.get("sample_rate") or state.get("sample_rate") or 24000)
        return {"sample_rate": state["sample_rate"], **info}
    if cmd == "synthesize":
        result = adapter.synthesize(
            message.get("text") or "",
            language=message.get("language"),
            voice=message.get("voice") or {},
            duration=message.get("duration"),
            speed=float(message.get("speed") or 1.0),
            hints=message.get("hints") or {},
        )
        if isinstance(result, tuple):
            audio, sample_rate = result
        else:
            audio, sample_rate = result, state.get("sample_rate", 24000)
        seconds = write_wav(message["out"], to_mono_float(audio), sample_rate)
        return {"path": message["out"], "sample_rate": int(sample_rate), "seconds": round(seconds, 3)}
    if cmd == "separate":
        separate = getattr(adapter, "separate", None)
        if not callable(separate):
            raise ValueError("This engine cannot separate audio.")
        return separate(message["input"], message["out_dir"]) or {}
    if cmd == "diarize":
        diarize = getattr(adapter, "diarize", None)
        if not callable(diarize):
            raise ValueError("This engine cannot tell speakers apart.")
        return diarize(message["input"], message.get("num_speakers")) or {}
    if cmd == "voices":
        list_voices = getattr(adapter, "list_voices", None)
        return {"voices": list_voices(message.get("settings") or {}) if callable(list_voices) else []}
    if cmd == "ping":
        return {"pong": True}
    raise ValueError(f"Unknown command: {cmd}")


def error_reply(message, exc):
    return {
        "id": message.get("id"),
        "ok": False,
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc(),
    }


def serve(adapter, engine_dir, runtime_path, idle_seconds):
    """Resident mode: keep the model loaded and accept one client at a time.

    The host connects to 127.0.0.1:<port> from the runtime file and must send
    the token it passed in DUB_ENGINE_TOKEN. The worker exits after
    idle_seconds without requests, or on "shutdown".
    """
    import socket
    import time

    token = os.environ.get("DUB_ENGINE_TOKEN", "")
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(1.0)
    info = {
        "port": server.getsockname()[1],
        "pid": os.getpid(),
        "token": token,
        "engine": engine_dir.name,
        "python": sys.executable,
        "started": time.time(),
        "last_activity": time.time(),
        "idle_seconds": idle_seconds,
        "loaded": False,
    }

    def write_info():
        tmp = runtime_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(info), encoding="utf-8")
        tmp.replace(runtime_path)

    write_info()
    state = {}
    loaded_key = None
    running = True
    try:
        while running:
            if idle_seconds and time.time() - info["last_activity"] > idle_seconds:
                print("Idle timeout: unloading the model.", flush=True)
                break
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            connection.settimeout(None)
            with connection:
                reader = connection.makefile("r", encoding="utf-8", errors="replace")
                writer = connection.makefile("w", encoding="utf-8")

                def reply(payload):
                    writer.write(json.dumps(payload, ensure_ascii=False) + "\n")
                    writer.flush()

                try:
                    hello = json.loads(reader.readline() or "{}")
                except json.JSONDecodeError:
                    continue
                if not token or hello.get("token") != token:
                    continue  # not our host: drop the connection silently
                reply({"id": hello.get("id", 0), "ok": True, "result": {"ready": True, "resident": True, "loaded": info["loaded"]}})
                try:
                    for line in reader:
                        line = line.strip()
                        if not line:
                            continue
                        message = json.loads(line)
                        info["last_activity"] = time.time()
                        cmd = message.get("cmd")
                        if cmd == "release":
                            break
                        if cmd == "shutdown":
                            reply({"id": message.get("id"), "ok": True, "result": {}})
                            running = False
                            break
                        try:
                            if cmd == "load":
                                key = json.dumps([message.get("settings"), message.get("device")], sort_keys=True)
                                if key == loaded_key and state.get("load_result"):
                                    reply({"id": message.get("id"), "ok": True, "result": {**state["load_result"], "reused": True}})
                                    continue
                                result = handle(adapter, state, message)
                                loaded_key = key
                                state["load_result"] = result
                                info["loaded"] = True
                                write_info()
                            else:
                                result = handle(adapter, state, message)
                            reply({"id": message.get("id"), "ok": True, "result": result})
                        except Exception as exc:
                            reply(error_reply(message, exc))
                except (OSError, ValueError):
                    pass  # client went away; keep serving
                info["last_activity"] = time.time()
                write_info()
    finally:
        server.close()
        runtime_path.unlink(missing_ok=True)
        unload = getattr(adapter, "unload", None)
        if callable(unload):
            unload()
    return 0


def main():
    args = sys.argv[1:]
    if not args:
        print("usage: worker.py <engine_dir> [--serve --runtime-file FILE --idle SECONDS]", file=sys.stderr)
        return 2
    for stream in (sys.stdin, sys.stderr, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    engine_dir = Path(args[0]).resolve()

    if "--serve" in args:
        runtime_path = Path(args[args.index("--runtime-file") + 1])
        idle_seconds = int(float(args[args.index("--idle") + 1])) if "--idle" in args else 600
        adapter = load_adapter(engine_dir)  # import errors end up in the worker log
        return serve(adapter, engine_dir, runtime_path, idle_seconds)

    channel = open_protocol_channel()

    def reply(payload):
        channel.write(json.dumps(payload, ensure_ascii=False) + "\n")
        channel.flush()

    try:
        adapter = load_adapter(engine_dir)
    except Exception as exc:  # report import errors (missing packages) to the host
        reply({"id": 0, "ok": False, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()})
        return 1
    reply({"id": 0, "ok": True, "result": {"ready": True, "python": sys.executable}})

    state = {}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        if message.get("cmd") == "shutdown":
            unload = getattr(adapter, "unload", None)
            if callable(unload):
                unload()
            reply({"id": message.get("id"), "ok": True, "result": {}})
            break
        try:
            reply({"id": message.get("id"), "ok": True, "result": handle(adapter, state, message)})
        except Exception as exc:
            reply(error_reply(message, exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
