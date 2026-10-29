"""Chatterbox Multilingual adapter: https://github.com/resemble-ai/chatterbox"""

import numpy as np
import torch
from chatterbox.mtl_tts import ChatterboxMultilingualTTS


_model = None
_settings = {}


def resolve_device(device):
    if device in (None, "", "auto"):
        return "cuda" if torch.cuda.is_available() else "cpu"
    return device


def load(settings, device):
    global _model, _settings
    _settings = dict(settings)
    device = resolve_device(device)
    _model = ChatterboxMultilingualTTS.from_pretrained(device=device)
    return {"sample_rate": int(_model.sr), "device": device}


def synthesize(text, language=None, voice=None, duration=None, speed=1.0, hints=None):
    voice = voice or {}
    language = (language or "en").split("-")[0].lower()
    wav = _model.generate(
        text,
        language_id=language,
        audio_prompt_path=voice.get("ref_audio") or None,
        exaggeration=float(_settings.get("exaggeration", 0.5)),
        cfg_weight=float(_settings.get("cfg_weight", 0.5)),
        temperature=float(_settings.get("temperature", 0.8)),
    )
    if hasattr(wav, "detach"):
        wav = wav.detach().float().cpu().numpy()
    return np.asarray(wav, dtype=np.float32).reshape(-1), int(_model.sr)


def unload():
    global _model
    _model = None
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
