"""Template for a new TTS engine. Copy this folder to engines/<your_id>/.

The dubbing core calls only these functions; everything else (timing slots,
speed-up, clipping, mixing, normalization) is handled outside the engine.
This file runs inside the engine's own environment (see pyproject.toml), so it
can import whatever the model needs.
"""

import numpy as np


_model = None
_settings = {}


def load(settings, device):
    """Load the model once.

    settings: values of the fields declared in engine.json "settings".
    device:   "auto", "cuda", "cuda:1" or "cpu".
    Return a dict with at least the output sample rate.
    """
    global _settings
    _settings = dict(settings)
    # global _model
    # _model = MyTTS.from_pretrained(...).to("cuda" if device in ("auto", "cuda") else device)
    return {"sample_rate": 24000}


def synthesize(text, language=None, voice=None, duration=None, speed=1.0, hints=None):
    """Turn one phrase into audio.

    text:     the phrase to speak.
    language: ISO code from the plan ("en", "ru", ...) or None.
    voice:    {"ref_audio": path, "ref_text": "..."} for cloning,
              or {"voice_id": "name"} for engines with a voice list.
    duration: target length in seconds, only when supports.duration is true.
    speed:    1.0 = normal, only when supports.speed is true.
    hints:    optional {"instruct": "...", "emotion": "calm"} from the script stage.

    Return (audio, sample_rate): mono float32 numpy array in [-1, 1].
    """
    sample_rate = 24000
    seconds = max(0.5, 0.06 * len(text))
    t = np.linspace(0.0, seconds, int(sample_rate * seconds), endpoint=False)
    audio = 0.1 * np.sin(2 * np.pi * 220.0 * t)  # replace with the real model call
    return audio.astype(np.float32), sample_rate


def list_voices(settings):
    """Optional: names for engines with supports.voice_list = true."""
    return []


def unload():
    """Optional: free GPU memory when the job ends."""
    global _model
    _model = None
