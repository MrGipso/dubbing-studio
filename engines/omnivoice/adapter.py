"""OmniVoice adapter: https://github.com/k2-fsa/OmniVoice"""

import numpy as np
import torch
from omnivoice import OmniVoice


_model = None
_settings = {}

DTYPES = {
    "float16": torch.float16,
    "bfloat16": torch.bfloat16,
    "float32": torch.float32,
}


def resolve_device(device):
    if device in (None, "", "auto"):
        return "cuda:0" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        return "cuda:0"
    return device


def load(settings, device):
    global _model, _settings
    _settings = dict(settings)
    device = resolve_device(device)
    dtype = DTYPES.get(str(settings.get("dtype") or "float16"), torch.float16)
    if device == "cpu":
        dtype = torch.float32  # half precision is not usable on CPU
    _model = OmniVoice.from_pretrained(settings.get("model") or "k2-fsa/OmniVoice", device_map=device, dtype=dtype)
    return {"sample_rate": int(getattr(_model, "sampling_rate", 24000)), "device": device}


def synthesize(text, language=None, voice=None, duration=None, speed=1.0, hints=None):
    voice = voice or {}
    hints = hints or {}
    audios = _model.generate(
        text=text,
        language=language or None,
        ref_audio=voice.get("ref_audio") or None,
        ref_text=voice.get("ref_text") or None,
        instruct=hints.get("instruct") or None,
        duration=duration,
        speed=speed,
        num_step=int(_settings.get("num_step") or 16),
        guidance_scale=float(_settings.get("guidance_scale") or 2.0),
    )
    return np.asarray(audios[0], dtype=np.float32), int(getattr(_model, "sampling_rate", 24000))


def unload():
    global _model
    _model = None
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
