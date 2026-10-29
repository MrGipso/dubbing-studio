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
    if not callable(getattr(module, "synthesize", None)) and not callable(getattr(module, "separate", None)):
        raise RuntimeError(f"{adapter_path} must define 'synthesize' (TTS) or 'separate' (separator).")
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
    if cmd == "voices":
        list_voices = getattr(adapter, "list_voices", None)
        return {"voices": list_voices(message.get("settings") or {}) if callable(list_voices) else []}
    if cmd == "ping":
        return {"pong": True}
    raise ValueError(f"Unknown command: {cmd}")


def main():
    if len(sys.argv) < 2:
        print("usage: worker.py <engine_dir>", file=sys.stderr)
        return 2
    for stream in (sys.stdin, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    channel = open_protocol_channel()
    engine_dir = Path(sys.argv[1]).resolve()

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
            reply({
                "id": message.get("id"),
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })
    return 0


if __name__ == "__main__":
    sys.exit(main())
