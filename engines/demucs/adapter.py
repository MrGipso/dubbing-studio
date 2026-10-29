"""Demucs vocal separator: https://github.com/adefossez/demucs

Separator engines implement load() and separate(input_path, out_dir) instead
of synthesize(). The result is vocals.wav (speech) and background.wav
(music, effects, ambience) at the model's sample rate.
"""

from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from demucs.apply import apply_model
from demucs.pretrained import get_model
from julius import resample_frac


_model = None
_device = "cpu"
_settings = {}


def resolve_device(device):
    if device in (None, "", "auto"):
        return "cuda" if torch.cuda.is_available() else "cpu"
    return device


def load(settings, device):
    global _model, _device, _settings
    _settings = dict(settings)
    _device = resolve_device(device)
    _model = get_model(settings.get("model") or "htdemucs")
    _model.eval()
    return {"sample_rate": int(_model.samplerate), "device": _device, "sources": list(_model.sources)}


_total_samples = 1


def _progress(info):
    # Called after each ~8 s chunk; segment_offset is the chunk start in samples.
    if info.get("state") == "end":
        segment = int(float(getattr(_model, "segment", 8) or 8) * _model.samplerate)
        done = min(_total_samples, int(info.get("segment_offset") or 0) + segment)
        print(f"PROGRESS {int(done * 100 / _total_samples)}/100 Отделение голоса от фона", flush=True)


def separate(input_path, out_dir):
    audio, rate = sf.read(str(input_path), dtype="float32", always_2d=True)
    wav = torch.from_numpy(audio.T.copy())  # (channels, samples)
    if wav.shape[0] == 1:
        wav = wav.repeat(2, 1)
    elif wav.shape[0] > 2:
        wav = wav[:2]
    if rate != _model.samplerate:
        wav = resample_frac(wav, rate, _model.samplerate)
    global _total_samples
    _total_samples = max(1, wav.shape[-1])

    # Same normalization as the demucs CLI.
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    with torch.no_grad():
        sources = apply_model(
            _model,
            ((wav - mean) / std)[None],
            device=_device,
            shifts=int(_settings.get("shifts") or 0),
            split=True,
            overlap=0.25,
            callback=_progress,
        )[0]
    sources = sources * std + mean

    vocals = sources[_model.sources.index("vocals")]
    background = sources.sum(0) - vocals
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    vocals_path = out_dir / "vocals.wav"
    background_path = out_dir / "background.wav"
    sf.write(str(vocals_path), np.clip(vocals.T.cpu().numpy(), -1, 1), _model.samplerate, subtype="FLOAT")
    sf.write(str(background_path), np.clip(background.T.cpu().numpy(), -1, 1), _model.samplerate, subtype="FLOAT")
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return {"vocals": str(vocals_path), "background": str(background_path), "sample_rate": int(_model.samplerate)}


def unload():
    global _model
    _model = None
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
