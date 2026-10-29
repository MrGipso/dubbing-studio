"""Speaker diarization with sherpa-onnx: https://github.com/k2-fsa/sherpa-onnx

Diarizer engines implement load() and diarize(input_path, num_speakers)
instead of synthesize(). The result is a list of speaker turns in seconds:
{"turns": [{"start": 0.0, "end": 2.5, "speaker": 0}, ...]}.

Models are downloaded into models/ on first use:
- pyannote segmentation 3.0 (MIT), finds where speech of one person starts and ends;
- a voice embedding model (3D-Speaker, Apache-2.0, or WeSpeaker, CC-BY-4.0)
  that tells voices apart.
Everything runs on the CPU through onnxruntime; no PyTorch, no tokens.
"""

import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import numpy as np
import sherpa_onnx
import soundfile as sf

from clustering import group_turns


MODELS_DIR = Path(__file__).resolve().parent / "models"
RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEGMENTATION = {
    "archive": f"{RELEASES}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
    "member": "sherpa-onnx-pyannote-segmentation-3-0/model.onnx",
    "file": "pyannote-segmentation-3-0.onnx",
}
EMBEDDINGS = {
    "3dspeaker_campplus_zh_en": "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
    "3dspeaker_eres2net_base": "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
}

_diarizer = None
_extractor = None
_settings = {}


def _download(url, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url}", file=sys.stderr, flush=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False, suffix=".part") as tmp:
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed GitHub release URLs
            shutil.copyfileobj(response, tmp)
    os.replace(tmp.name, destination)


def segmentation_model():
    path = MODELS_DIR / SEGMENTATION["file"]
    if not path.is_file():
        archive = MODELS_DIR / "segmentation.tar.bz2"
        _download(SEGMENTATION["archive"], archive)
        with tarfile.open(archive, "r:bz2") as tar:
            member = tar.getmember(SEGMENTATION["member"])
            with tar.extractfile(member) as source, path.open("wb") as target:
                shutil.copyfileobj(source, target)
        archive.unlink(missing_ok=True)
    return path


def embedding_model(name):
    file_name = EMBEDDINGS.get(name) or EMBEDDINGS["3dspeaker_campplus_zh_en"]
    path = MODELS_DIR / file_name
    if not path.is_file():
        _download(f"{RELEASES}/speaker-recongition-models/{file_name}", path)
    return path


def _threads():
    return max(1, min(8, (os.cpu_count() or 2) // 2))


def _embedding_config():
    return sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(embedding_model(_settings.get("embedding_model"))), num_threads=_threads())


def load(settings, device):
    global _diarizer, _extractor, _settings
    _settings = dict(settings)
    # sherpa's own clustering only proposes turn boundaries; voices are grouped in diarize().
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(segmentation_model())),
            num_threads=_threads(),
        ),
        embedding=_embedding_config(),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.5),
        min_duration_on=float(_settings.get("min_turn") or 0.3),
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError("Invalid sherpa-onnx diarization config (are the models downloaded?).")
    _diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
    _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(_embedding_config())
    return {"sample_rate": int(_diarizer.sample_rate), "device": "cpu"}


def _read_mono(path, rate):
    audio, source_rate = sf.read(str(path), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if source_rate != rate:
        # The core passes 16 kHz audio already; this is a fallback for other callers.
        duration = len(audio) / float(source_rate)
        target = np.linspace(0.0, duration, int(round(duration * rate)), endpoint=False)
        audio = np.interp(target, np.arange(len(audio)) / float(source_rate), audio).astype(np.float32)
    return np.ascontiguousarray(audio, dtype=np.float32)


def _progress(done, total):
    # The first half of the bar is segmentation, the second half voice prints.
    print(f"PROGRESS {int(done)}/{max(1, int(total)) * 2} Определение говорящих", flush=True)
    return 0


def diarize(input_path, num_speakers=None):
    """Speaker turns of a recording; num_speakers=None finds the number itself.

    sherpa-onnx finds where each turn starts and ends; the turns are then
    grouped by a voice print of the whole turn, which tells similar voices
    apart much better than prints of short overlapping windows.
    """
    rate = _diarizer.sample_rate
    audio = _read_mono(input_path, rate)
    result = _diarizer.process(audio, callback=_progress).sort_by_start_time()
    turns = [(float(item.start), float(item.end)) for item in result if item.end - item.start > 0.05]
    if not turns:
        return {"turns": [], "speakers": 0}

    prints = []
    for index, (start, end) in enumerate(turns, start=1):
        stream = _extractor.create_stream()
        stream.accept_waveform(rate, audio[int(start * rate):int(end * rate)])
        stream.input_finished()
        prints.append(np.asarray(_extractor.compute(stream), dtype=np.float64))
        print(f"PROGRESS {len(turns) + index}/{len(turns) * 2} Определение говорящих", flush=True)
    labels = group_turns(np.stack(prints), [end - start for start, end in turns], num_speakers,
                         float(_settings.get("threshold") or 0.6))
    result_turns = [{"start": round(start, 3), "end": round(end, 3), "speaker": label}
                    for (start, end), label in zip(turns, labels)]
    return {"turns": result_turns, "speakers": len(set(labels))}


def unload():
    global _diarizer, _extractor
    _diarizer = _extractor = None
