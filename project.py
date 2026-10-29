"""Dubbing projects: one folder per video, every stage keeps its files there.

projects/<id>/
  project.json     what the user chose and how far the project got
  source/          the original video or audio
  work/            audio.wav, vocals.wav, background.wav, transcript.*, one plan per language
  voice/           voice samples (cut from the original or uploaded)
  output/          finished files per language: video with the dub, mix, voice track, subtitles

A project has one source and any number of target languages. Separation,
Whisper, finding the speakers and their voice samples are done once;
translation, voicing and export are done per language ("tracks").

A project can have several speakers ("speakers" in project.json): every
phrase belongs to one of them, and every speaker has their own voice.
speaker_count: 1 = one voice for everyone, 0 = find the speakers, N = exactly N.

Stages:
  prepare   extract audio -> separate voice/background -> Whisper -> speakers -> voice samples -> translate every language
  translate translate languages that are not translated yet (reuses Whisper)
  voice     synthesize every translated language with the chosen TTS engine (dub.py)
  export    mix voice + original background (+ music) and put it back into the video
  all       do whatever is not done yet, in order
  shorten   ask the LLM to shorten too-long phrases (active language)
  revoice   re-voice edited phrases and rebuild the result (active language)

CLI:
  python project.py create clip.mp4 --target English --target Spanish
  python project.py run projects/<id> all
"""

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

import tts_engines
from app_settings import llm_script_args, load_settings, progress, stage
from i18n import tr
from languages import language_id_for, language_name_for


ROOT = Path(__file__).resolve().parent
PROJECTS_DIR = ROOT / "projects"
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac"}
MP4_FRIENDLY = {".mp4", ".mov", ".m4v"}

DEFAULT_VOICE = {
    "mode": "original", "file": "", "ref_text": "", "voice_id": "",
    "auto_file": "", "auto_text": "", "auto_range": [], "candidates": [], "auto_index": 0,
}
VOICE_FIELDS = ("mode", "file", "ref_text", "voice_id")  # what the UI may change
MAX_SPEAKERS = 8
DEFAULT_PROJECT = {
    "name": "",
    "source_language": "auto",
    "separate": True,
    "speaker_count": 1,
    "targets": ["English"],
    "active": "English",
    "speakers": {},
    "timing": {"speed": 1.0, "tail_room": 0.6, "max_auto_speed": 1.18, "post_clean": "soft"},
    "mix": {"voice": 1.0, "background": 0.8, "music": 0.25, "music_file": "", "loudnorm": True},
    "transcribed": False,
    "diarized": False,
    "separated": False,
    "detected_language": "",
    "tracks": {},
}
DEFAULT_TRACK = {
    "name": "",
    "translated": False,
    "voiced": False,
    "exported": False,
    "edited": [],
    "dirty": [],
    "voice_track": "",
    "mix_track": "",
    "video": "",
    "subtitles": "",
}
TRACK_FILES = ("voice_track", "mix_track", "video", "subtitles")


class ProjectError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Storage


def slugify(value, fallback="project"):
    value = re.sub(r"\s+", "_", str(value or "").strip())
    value = re.sub(r"[^\w-]+", "_", value).strip("_")
    return value[:48] or fallback


def code_for(target):
    """'English' -> 'en': used in file names and as the TTS language."""
    return language_id_for(target) or slugify(target, "xx").lower()


def project_dir(project_id):
    path = (PROJECTS_DIR / str(project_id)).resolve()
    if path.parent != PROJECTS_DIR.resolve() or not (path / "project.json").is_file():
        raise ProjectError(f"Project not found: {project_id}")
    return path


def merge_defaults(data):
    merged = json.loads(json.dumps(DEFAULT_PROJECT))
    for key, value in (data or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict) and key not in ("tracks", "speakers"):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def migrate(directory, data):
    """Older project.json layouts: one 'target' at the top level, one 'voice' for the whole video."""
    if not data.get("tracks") and "target" in data:
        target = data.pop("target") or "English"
        old_state = data.pop("state", {}) or {}
        track = dict(DEFAULT_TRACK, name=target)
        track["translated"] = bool(old_state.get("prepared"))
        track["voiced"] = bool(old_state.get("voiced"))
        track["exported"] = bool(old_state.get("exported"))
        for key in ("edited", "dirty", *TRACK_FILES):
            if key in data:
                track[key] = data.pop(key)
        data["tracks"] = {code_for(target): track}
        data["targets"] = [target]
        data["active"] = target
        data["transcribed"] = (Path(directory) / "work" / "transcript.json").is_file()
    if "voice" in data and not data.get("speakers"):
        data["speakers"] = {"S1": {"name": "", "voice": data.pop("voice") or {}}}
    # Projects transcribed before speakers existed have one voice for every phrase.
    data.setdefault("diarized", bool(data.get("transcribed")))
    return data


def speaker_number(speaker_id):
    digits = re.sub(r"\D", "", str(speaker_id))
    return int(digits) if digits else 0


def sync_speakers(data):
    """At least one speaker; every speaker has a name and a complete voice entry."""
    speakers = data.get("speakers") if isinstance(data.get("speakers"), dict) else {}
    result = {}
    for speaker_id in sorted(speakers or {"S1": {}}, key=speaker_number):
        item = (speakers or {}).get(speaker_id) or {}
        voice = json.loads(json.dumps(DEFAULT_VOICE))
        voice.update(item.get("voice") or {})
        result[speaker_id] = {"name": str(item.get("name") or ""), "voice": voice}
    data["speakers"] = result
    try:
        count = int(data.get("speaker_count", 1))
    except (TypeError, ValueError):
        count = 1
    data["speaker_count"] = max(0, min(MAX_SPEAKERS, count))
    return data


def sync_tracks(data):
    """One track per target language; the active language is always one of the targets."""
    targets = []
    for target in data.get("targets") or []:
        target = str(target).strip()
        if target and target not in targets:
            targets.append(target)
    data["targets"] = targets or ["English"]
    tracks = data.setdefault("tracks", {})
    for target in data["targets"]:
        code = code_for(target)
        track = tracks.setdefault(code, {})
        for key, value in DEFAULT_TRACK.items():
            track.setdefault(key, json.loads(json.dumps(value)))
        track["name"] = target
    wanted = {code_for(target) for target in data["targets"]}
    for code in list(tracks):
        if code not in wanted:
            tracks.pop(code)
    if data.get("active") not in data["targets"]:
        data["active"] = data["targets"][0]
    return data


def summary_state(data):
    tracks = [data["tracks"][code_for(target)] for target in data["targets"]]
    return {
        "prepared": bool(data.get("transcribed")) and all(track["translated"] for track in tracks),
        "voiced": all(track["voiced"] for track in tracks),
        "exported": all(track["exported"] for track in tracks),
    }


def track(data, target=None):
    return data["tracks"][code_for(target or data["active"])]


def load_project(directory):
    directory = Path(directory)
    raw = json.loads((directory / "project.json").read_text(encoding="utf-8"))
    data = sync_speakers(sync_tracks(merge_defaults(migrate(directory, raw))))
    data["state"] = summary_state(data)
    data["id"] = directory.name
    return data


def save_project(directory, data):
    directory = Path(directory)
    data = sync_speakers(sync_tracks(dict(data)))
    data["state"] = summary_state(data)
    data.pop("id", None)
    data["updated"] = time.time()
    tmp = directory / "project.json.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(directory / "project.json")


def reset_track(item, keep_translation=False):
    if not keep_translation:
        item.update(translated=False, edited=[], dirty=[])
    item.update(voiced=False, exported=False)
    if not keep_translation:
        item["dirty"] = []


def merge_speakers(data, changes):
    """Names and voice choices of existing speakers; True when a voice changed."""
    voice_changed = False
    for speaker_id, change in (changes or {}).items():
        item = data["speakers"].get(speaker_id)
        if item is None or not isinstance(change, dict):
            continue
        if "name" in change:
            item["name"] = str(change["name"] or "").strip()[:60]
        for key, value in (change.get("voice") or {}).items():
            if key in VOICE_FIELDS and item["voice"].get(key) != str(value or ""):
                item["voice"][key] = str(value or "")
                voice_changed = True
    return voice_changed


def update_project(directory, changes):
    """Merge changes from the UI and invalidate whatever they make stale."""
    changes = dict(changes or {})
    if isinstance(changes.get("voice"), dict):  # old clients: one voice for the whole video
        changes.setdefault("speakers", {}).setdefault("S1", {})["voice"] = changes.pop("voice")
    data = load_project(directory)
    old_count = data["speaker_count"]
    allowed = {"name", "source_language", "targets", "active", "separate", "speaker_count", "timing", "mix"}
    voice_changed = merge_speakers(data, changes.get("speakers"))
    for key, value in changes.items():
        if key not in allowed:
            continue
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value
    if "target" in changes:  # old clients: a single target language
        data["targets"] = [changes["target"]]
        data["active"] = changes["target"]
    sync_speakers(sync_tracks(data))

    if {"source_language", "separate"} & set(changes):
        # A different input invalidates everything that came out of it.
        data["transcribed"] = data["diarized"] = False
        for item in data["tracks"].values():
            reset_track(item)
    elif data["speaker_count"] != old_count:
        # Phrases are split and merged per speaker, so the translation is redone.
        data["diarized"] = False
        for item in data["tracks"].values():
            reset_track(item)
    elif voice_changed or "timing" in changes:
        for item in data["tracks"].values():
            reset_track(item, keep_translation=True)
            item["dirty"] = []
    elif "mix" in changes:
        for item in data["tracks"].values():
            item["exported"] = False
    save_project(directory, data)
    return load_project(directory)


def new_project(name, source_name):
    PROJECTS_DIR.mkdir(exist_ok=True)
    base = f"{time.strftime('%Y%m%d-%H%M%S')}_{slugify(name or Path(source_name).stem)}"
    directory = PROJECTS_DIR / base
    counter = 2
    while directory.exists():
        directory = PROJECTS_DIR / f"{base}_{counter}"
        counter += 1
    for sub in ("source", "work", "voice", "output"):
        (directory / sub).mkdir(parents=True)
    data = json.loads(json.dumps(DEFAULT_PROJECT))
    data["name"] = name or Path(source_name).stem
    data["created"] = time.time()
    save_project(directory, data)
    return directory


def attach_source(directory, source_path):
    """Probe the file in source/ and record it in project.json."""
    directory = Path(directory)
    source_path = Path(source_path)
    info = probe(source_path)
    data = load_project(directory)
    data["source"] = {
        "file": source_path.relative_to(directory).as_posix(),
        "original_name": source_path.name,
        **info,
    }
    save_project(directory, data)
    return load_project(directory)


def create_from_file(path, name="", targets=("English",), speaker_count=1):
    path = Path(path)
    if isinstance(targets, str):
        targets = [targets]
    directory = new_project(name, path.name)
    destination = directory / "source" / path.name
    shutil.copy2(path, destination)
    data = attach_source(directory, destination)
    changes = {"speaker_count": speaker_count}
    if targets:
        changes.update(targets=list(targets), active=list(targets)[0])
    data = update_project(directory, changes)
    return directory, data


def list_projects():
    if not PROJECTS_DIR.is_dir():
        return []
    projects = []
    for directory in PROJECTS_DIR.iterdir():
        if not directory.is_dir() or directory.name.startswith((".", "_")) or not (directory / "project.json").is_file():
            continue
        try:
            projects.append(summary(load_project(directory)))
        except (OSError, json.JSONDecodeError):
            continue
    projects.sort(key=lambda item: item.get("updated") or 0, reverse=True)
    return projects


def summary(data):
    source = data.get("source") or {}
    return {
        "id": data["id"],
        "name": data.get("name"),
        "targets": data.get("targets"),
        "speakers": len(data.get("speakers") or {}) if data.get("diarized") else 0,
        "source_name": source.get("original_name"),
        "duration": source.get("duration"),
        "has_video": source.get("has_video"),
        "state": data.get("state"),
        "error": data.get("error"),
        "updated": data.get("updated"),
    }


def delete_project(directory):
    """Move to projects/.trash instead of deleting, so a click is never fatal."""
    directory = Path(directory)
    trash = PROJECTS_DIR / ".trash"
    trash.mkdir(exist_ok=True)
    target = trash / directory.name
    if target.exists():
        target = trash / f"{directory.name}_{int(time.time())}"
    shutil.move(str(directory), str(target))
    return target


# --------------------------------------------------------------------------
# Media helpers


def ffmpeg_path(name="ffmpeg"):
    found = shutil.which(name)
    if not found:
        raise ProjectError(f"{name} was not found. Install ffmpeg and add it to PATH.")
    return found


def probe(path):
    result = subprocess.run(
        [ffmpeg_path("ffprobe"), "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise ProjectError(f"Cannot read media file: {result.stderr.strip()[:300]}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video" and s.get("codec_name") not in ("mjpeg", "png")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not audio:
        raise ProjectError("The file has no audio track.")
    return {
        "duration": round(float((data.get("format") or {}).get("duration") or 0), 3),
        "has_video": bool(video),
        "video_codec": (video or {}).get("codec_name", ""),
        "audio_codec": audio.get("codec_name", ""),
    }


def run(command, label=""):
    """Run a child process; its output goes straight into the job log."""
    if label:
        print(f"> {label}", flush=True)
    result = subprocess.run(command, cwd=str(ROOT))
    if result.returncode != 0:
        raise ProjectError(tr("«{label}» завершилось с ошибкой (код {code}).", label=label or command[0], code=result.returncode))


def run_ffmpeg(args, label):
    run([ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args], label)


# --------------------------------------------------------------------------
# Voice sample from the original


def is_speech(segment):
    """Whisper's own rule: silence only when no_speech_prob is high AND the text is unsure.

    no_speech_prob alone is per 30 s window and is often high for clear speech.
    """
    return segment["end"] > segment["start"] and not (
        segment.get("no_speech_prob", 0) > 0.6 and segment.get("avg_logprob", 0) < -1.0)


def reference_candidates(segments, count=5, min_len=6.0, target=12.0, max_len=18.0, max_gap=0.8, speaker=None):
    """Best non-overlapping runs of clean consecutive speech for voice cloning.

    segments: Whisper segments with start, end, text, avg_logprob, no_speech_prob
    (and speaker). With `speaker`, a run only has that person's phrases.
    Returns [(start, end, text), ...], best first.
    """
    good = [s for s in segments if is_speech(s)]

    def own(segment):
        return speaker is None or segment.get("speaker", "S1") == speaker

    windows = []
    for i in range(len(good)):
        if not own(good[i]):
            continue
        start = good[i]["start"]
        confidence = 0.0
        texts = []
        for j in range(i, len(good)):
            if not own(good[j]):
                break  # somebody else speaks in between
            if j > i and good[j]["start"] - good[j - 1]["end"] > max_gap:
                break
            end = good[j]["end"]
            span = end - start
            if span > max_len:
                break
            confidence += math.exp(good[j].get("avg_logprob", -1.0)) * (good[j]["end"] - good[j]["start"])
            texts.append(good[j]["text"].strip())
            if span >= min_len:
                # Average confidence, slightly preferring spans close to the target length.
                windows.append((confidence / span * min(1.0, span / target), start, end, " ".join(texts)))
    chosen = []
    for score, start, end, text in sorted(windows, key=lambda item: item[0], reverse=True):
        if all(end <= other[0] or start >= other[1] for other in chosen):
            chosen.append((start, end, text))
        if len(chosen) >= count:
            break
    if not chosen:
        longest = max((s for s in good if own(s)), key=lambda s: s["end"] - s["start"], default=None)
        if longest and longest["end"] - longest["start"] >= 2.5:
            chosen.append((longest["start"], longest["end"], longest["text"].strip()))
    return chosen


def stitched_sample(segments, speaker=None, target=12.0, min_total=3.0):
    """Short phrases of one speaker glued together, for someone without a long run of speech."""
    own = [s for s in segments if is_speech(s) and s["end"] - s["start"] >= 0.8
           and (speaker is None or s.get("speaker", "S1") == speaker)]
    picked, total = [], 0.0
    for segment in sorted(own, key=lambda s: s.get("avg_logprob", -1.0), reverse=True):
        if total >= target:
            break
        picked.append(segment)
        total += segment["end"] - segment["start"]
    if total < min_total:
        return None
    picked.sort(key=lambda s: s["start"])
    return {
        "start": round(picked[0]["start"], 2),
        "end": round(picked[-1]["end"], 2),
        "text": " ".join(s["text"].strip() for s in picked),
        "parts": [[round(s["start"], 2), round(s["end"], 2)] for s in picked],
    }


def sample_candidates(segments, speaker=None):
    """Voice sample options for one speaker, best first."""
    candidates = [{"start": round(start, 2), "end": round(end, 2), "text": text}
                  for start, end, text in reference_candidates(segments, speaker=speaker)]
    if not candidates or candidates[0]["end"] - candidates[0]["start"] < 6.0:
        stitched = stitched_sample(segments, speaker)
        if stitched:
            candidates.insert(0, stitched)
    return candidates


def pick_reference(segments, **kwargs):
    candidates = reference_candidates(segments, count=1, **kwargs)
    return candidates[0] if candidates else None


def cut_reference(source_wav, start, end, destination, margin=0.12, parts=None):
    """Cut [start, end] (or several `parts`, joined by short pauses) into a voice sample."""
    audio, rate = sf.read(str(source_wav), dtype="float32", always_2d=True)
    pieces = []
    for part_start, part_end in parts or [(start, end)]:
        a = max(0, int((part_start - margin) * rate))
        b = min(len(audio), int((part_end + margin) * rate))
        piece = audio[a:b].mean(axis=1)
        fade = min(len(piece) // 4, int(0.02 * rate))
        if fade > 1:
            piece[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
            piece[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
        if pieces:
            pieces.append(np.zeros(int(0.3 * rate), dtype=np.float32))
        pieces.append(piece)
    clip = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
    peak = float(np.max(np.abs(clip))) if len(clip) else 0.0
    if peak > 1e-6:
        clip = clip / peak * 0.9
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(destination), clip, rate)
    return len(clip) / rate


def speech_wav(directory, data):
    return Path(directory) / "work" / ("vocals.wav" if data.get("separated") else "audio.wav")


def speaker_or_error(data, speaker_id):
    if speaker_id not in data["speakers"]:
        raise ProjectError(tr("Нет говорящего {speaker}.", speaker=speaker_id))
    return data["speakers"][speaker_id]


def use_sample(directory, data, index, speaker_id="S1"):
    """Cut candidate number `index` of a speaker into voice/original_<speaker>.wav."""
    voice = speaker_or_error(data, speaker_id)["voice"]
    candidates = voice.get("candidates") or []
    if not candidates:
        raise ProjectError(tr("Нет отрывков для образца голоса: сначала распознайте исходник."))
    index = max(0, min(int(index), len(candidates) - 1))
    candidate = candidates[index]
    relative = f"voice/original_{speaker_id.lower()}.wav"
    seconds = cut_reference(speech_wav(directory, data), candidate["start"], candidate["end"],
                            Path(directory) / relative, parts=candidate.get("parts"))
    voice.update(auto_file=relative, auto_text=candidate["text"],
                 auto_range=[candidate["start"], candidate["end"]], auto_index=index)
    return seconds


def choose_sample(directory, index, speaker_id="S1"):
    """UI: switch a speaker's original-voice sample to another fragment of the video."""
    data = load_project(directory)
    use_sample(directory, data, index, speaker_id)
    if data["speakers"][speaker_id]["voice"].get("mode", "original") == "original":
        for item in data["tracks"].values():
            reset_track(item, keep_translation=True)
            item["dirty"] = []
    save_project(directory, data)
    return load_project(directory)


# --------------------------------------------------------------------------
# Speakers


def transcript_path(directory):
    """Phrases with speakers: what translation, samples and the phrase table use."""
    return Path(directory) / "work" / "transcript.json"


def load_phrases(directory):
    path = transcript_path(directory)
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("segments") or []


def speaker_at(turns, start, end):
    """Speaker of the turn that covers most of [start, end]; the nearest turn when none overlaps."""
    best, best_overlap = None, 0.0
    for turn in turns:
        overlap = min(end, turn["end"]) - max(start, turn["start"])
        if overlap > best_overlap:
            best, best_overlap = turn["speaker"], overlap
    if best is None and turns:
        middle = (start + end) / 2
        best = min(turns, key=lambda turn: max(turn["start"] - middle, middle - turn["end"], 0.0))["speaker"]
    return best


def absorb_short_runs(runs, min_seconds=0.6):
    """A word or two given to another voice inside a phrase is usually a slip of the diarizer."""
    def seconds(run):
        return run["words"][-1]["end"] - run["words"][0]["start"]

    while len(runs) > 1:
        short = next((i for i, run in enumerate(runs) if seconds(run) < min_seconds and len(run["words"]) <= 2), None)
        if short is None:
            break
        if short == 0 or (short < len(runs) - 1 and seconds(runs[short + 1]) > seconds(runs[short - 1])):
            runs[short + 1]["words"][:0] = runs[short]["words"]
        else:
            runs[short - 1]["words"].extend(runs[short]["words"])
        del runs[short]
        merged = [runs[0]]
        for run in runs[1:]:
            if run["speaker"] == merged[-1]["speaker"]:
                merged[-1]["words"].extend(run["words"])
            else:
                merged.append(run)
        runs = merged
    return runs


def assign_speakers(segments, turns):
    """Label Whisper phrases with speakers S1, S2, ...; a phrase where the voice changes is split by words."""
    if not turns:
        return [dict(segment, speaker="S1") for segment in segments]
    phrases = []
    for segment in segments:
        words = [word for word in segment.get("words") or [] if str(word.get("word") or "").strip()]
        if not words:
            phrases.append(dict(segment, speaker=speaker_at(turns, segment["start"], segment["end"])))
            continue
        runs = []
        for word in words:
            who = speaker_at(turns, word["start"], max(word["end"], word["start"] + 0.01))
            if runs and runs[-1]["speaker"] == who:
                runs[-1]["words"].append(word)
            else:
                runs.append({"speaker": who, "words": [word]})
        runs = absorb_short_runs(runs)
        if len(runs) == 1:
            phrases.append(dict(segment, speaker=runs[0]["speaker"]))
            continue
        for run in runs:
            phrase = {key: segment[key] for key in ("avg_logprob", "no_speech_prob") if key in segment}
            phrase.update(start=run["words"][0]["start"], end=run["words"][-1]["end"],
                          text="".join(word["word"] for word in run["words"]).strip(),
                          speaker=run["speaker"], words=run["words"])
            phrases.append(phrase)
    order = {}
    for phrase in phrases:
        order.setdefault(phrase["speaker"], f"S{len(order) + 1}")
    for phrase in phrases:
        phrase["speaker"] = order[phrase["speaker"]]
    return [phrase for phrase in phrases if phrase["text"]]


def find_speaker_turns(directory, data, settings, count):
    """Speaker turns from the diarization engine; [] (one voice) when it is not available."""
    section = settings.get("diarization") or {}
    engine_id = section.get("engine") or "sherpa_diarization"
    try:
        manifest = tts_engines.load_manifest(engine_id)
    except tts_engines.EngineError:
        print("WARNING: " + tr("движок разделения на говорящих «{engine}» не найден, все фразы озвучатся одним голосом.",
                               engine=engine_id), flush=True)
        return []
    if not tts_engines.engine_status(manifest).get("installed"):
        print("WARNING: " + tr("{name} не установлен (Настройки → Разделение на говорящих). Все фразы озвучатся одним голосом.",
                               name=manifest["name"]), flush=True)
        return []
    speech = Path(directory) / "work" / "speech_16k.wav"
    run_ffmpeg(["-i", str(speech_wav(directory, data)), "-ac", "1", "-ar", "16000", str(speech)],
               tr("Готовлю звук для определения говорящих"))
    try:
        with tts_engines.EngineSession(engine_id) as session:
            session.load(tts_engines.resolve_settings(manifest), section.get("device") or "cpu")
            result = session.diarize(speech, None if count == 0 else count)
    finally:
        speech.unlink(missing_ok=True)
    return result.get("turns") or []


def stage_diarize(directory, data, settings):
    """Who says what: work/whisper.json -> work/transcript.json (+ .srt) with a speaker per phrase."""
    import translate_srt

    work = Path(directory) / "work"
    raw_path = work / "whisper.json"
    if not raw_path.is_file():
        if not transcript_path(directory).is_file():
            raise ProjectError(tr("Сначала распознайте и переведите исходник."))
        shutil.copy2(transcript_path(directory), raw_path)  # projects transcribed before speakers existed
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    segments = raw.get("segments") or []
    count = data["speaker_count"]
    turns = find_speaker_turns(directory, data, settings, count) if count != 1 and segments else []
    phrases = assign_speakers(segments, turns)
    speaker_ids = list(dict.fromkeys(phrase["speaker"] for phrase in phrases)) or ["S1"]
    transcript = {key: raw[key] for key in ("language", "language_probability", "duration") if key in raw}
    transcript.update(speakers=speaker_ids, segments=phrases)
    transcript_path(directory).write_text(json.dumps(transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    translate_srt.write_srt(work / "transcript.srt",
                            [{"start": p["start"], "end": p["end"], "source": p["text"]} for p in phrases])
    # Names and voice choices survive a re-run for speakers that still exist.
    old = data["speakers"]
    data["speakers"] = {speaker_id: old.get(speaker_id, {}) for speaker_id in speaker_ids}
    sync_speakers(data)
    data["diarized"] = True
    if count != 1:
        print(tr("Говорящих: {count}", count=len(speaker_ids)), flush=True)


def set_phrase_speakers(directory, data, changes):
    """Give phrases (ids of the active language) to another speaker, in every language."""
    changes = {int(key): str(value) for key, value in (changes or {}).items()}
    for speaker_id in set(changes.values()):
        speaker_or_error(data, speaker_id)
    units = {int(unit["id"]): unit for unit in load_units(directory, data["active"])}
    missing = sorted(set(changes) - set(units))
    if missing:
        raise ProjectError(tr("Нет фраз с номерами: {ids}", ids=missing))
    transcript = json.loads(transcript_path(directory).read_text(encoding="utf-8"))
    phrases = transcript.get("segments") or []
    for unit_id, speaker_id in changes.items():
        for segment_id in units[unit_id].get("segment_ids") or [unit_id]:
            if 0 < int(segment_id) <= len(phrases):
                phrases[int(segment_id) - 1]["speaker"] = speaker_id
    transcript_path(directory).write_text(json.dumps(transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for target in data["targets"]:
        units_file = units_path(directory, target)
        if not units_file.is_file():
            continue
        payload = json.loads(units_file.read_text(encoding="utf-8"))
        changed = {}
        for unit in payload.get("translation_units") or []:
            segment_ids = unit.get("segment_ids") or [unit["id"]]
            first = int(segment_ids[0])
            speaker_id = phrases[first - 1].get("speaker", "S1") if 0 < first <= len(phrases) else unit.get("speaker")
            if speaker_id and unit.get("speaker", "S1") != speaker_id:
                unit["speaker"] = speaker_id
                changed[unit_key(unit["id"])] = speaker_id
        if not changed:
            continue
        units_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        plan = plan_path(directory, target)
        if plan.is_file():
            lines = []
            for line in plan.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    if item.get("id") in changed:
                        item["speaker"] = changed[item["id"]]
                    lines.append(json.dumps(item, ensure_ascii=False))
            plan.write_text("\n".join(lines) + "\n", encoding="utf-8")
        item = track(data, target)
        if item.get("voiced"):
            ids = {int(key.split("_")[-1]) for key in changed}
            item["dirty"] = sorted(set(item.get("dirty") or []) | ids)
            item["exported"] = False
    save_project(directory, data)
    return data


# --------------------------------------------------------------------------
# Paths per language


def plan_path(directory, target):
    import translate_srt

    return Path(directory) / "work" / f"transcript.{translate_srt.slugify(target)}.dub.jsonl"


def units_path(directory, target):
    plan = plan_path(directory, target)
    return plan.with_name(plan.name.replace(".dub.jsonl", ".segments.json"))


def translated_srt_path(directory, target):
    plan = plan_path(directory, target)
    return plan.with_name(plan.name.replace(".dub.jsonl", ".srt"))


def segments_dir(directory, target):
    return Path(directory) / "work" / f"segments_{code_for(target)}"


def output_base(data):
    return slugify(data.get("name") or "dub")


def unit_key(unit_id):
    return f"unit_{int(unit_id):04d}"


def load_units(directory, target):
    path = units_path(directory, target)
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("translation_units") or []


def phrase_report(directory, data, target):
    """Fit report of the last voicing of a language: {"unit_0003": {...}}."""
    voice_track = track(data, target).get("voice_track")
    if not voice_track:
        return {}
    report = (Path(directory) / voice_track).with_name(Path(voice_track).stem + ".report.json")
    if not report.is_file():
        return {}
    return {item["id"]: item for item in json.loads(report.read_text(encoding="utf-8")).get("segments") or []}


# --------------------------------------------------------------------------
# Stages


def stage_extract(directory, data):
    source = directory / data["source"]["file"]
    run_ffmpeg(["-i", str(source), "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(directory / "work" / "audio.wav")],
               tr("Извлекаю звук из исходника"))


def stage_separate(directory, data, settings):
    work = directory / "work"
    for name in ("vocals.wav", "background.wav"):
        (work / name).unlink(missing_ok=True)
    data["separated"] = False
    if not data.get("separate"):
        print(tr("Отделение фона выключено: распознаю исходный звук целиком."), flush=True)
        return
    engine_id = settings["separation"].get("engine") or "demucs"
    try:
        manifest = tts_engines.load_manifest(engine_id)
    except tts_engines.EngineError:
        print("WARNING: " + tr("движок отделения фона «{engine}» не найден, продолжаю без него.", engine=engine_id), flush=True)
        return
    if not tts_engines.engine_status(manifest).get("installed"):
        print("WARNING: " + tr("{name} не установлен (Настройки → Отделение фона). Продолжаю без отделения фона.",
                               name=manifest["name"]), flush=True)
        return
    with tts_engines.EngineSession(engine_id) as session:
        session.load(tts_engines.resolve_settings(manifest), settings["separation"].get("device") or "auto")
        session.separate(work / "audio.wav", work)
    data["separated"] = (work / "vocals.wav").is_file() and (work / "background.wav").is_file()


def stage_transcribe(directory, data, settings):
    work = directory / "work"
    asr = settings["asr"]
    language = language_id_for(data.get("source_language")) or "auto"
    run([
        sys.executable, str(ROOT / "transcribe.py"), str(speech_wav(directory, data)),
        "--model", str(asr.get("model") or "medium"),
        "--language", language,
        "--device", str(asr.get("device") or "cuda"),
        "--compute-type", str(asr.get("computeType") or "float16"),
        "--output-dir", str(work),
        "--output-prefix", "whisper",
        "--word-timestamps",  # lets a phrase be split where the speaker changes
    ], tr("Распознаю речь (Whisper)"))
    transcript = json.loads((work / "whisper.json").read_text(encoding="utf-8"))
    data["detected_language"] = transcript.get("language")
    if not transcript.get("segments"):
        raise ProjectError(tr("Whisper не нашёл речь в исходнике."))
    data["transcribed"] = True
    data["diarized"] = False


def stage_voice_sample(directory, data):
    """Find fragments of every speaker for cloning and cut the best one of each."""
    segments = load_phrases(directory)
    for speaker_id, item in data["speakers"].items():
        voice = item["voice"]
        voice["candidates"] = sample_candidates(segments, speaker_id)
        voice.update(auto_file="", auto_text="", auto_range=[], auto_index=0)
        label = item.get("name") or speaker_id
        if not voice["candidates"]:
            print("WARNING: " + tr("{speaker}: не нашёл подходящего отрывка речи для образца голоса.", speaker=label), flush=True)
            continue
        seconds = use_sample(directory, data, 0, speaker_id)
        start, end = voice["auto_range"]
        print(tr("Образец голоса {speaker}: {seconds:.1f} с ({start:.1f}–{end:.1f} с исходника), вариантов: {count}",
                 speaker=label, seconds=seconds, start=start, end=end, count=len(voice["candidates"])), flush=True)


def stage_translate(directory, data, settings, target):
    work = directory / "work"
    source_language = language_name_for(
        data.get("source_language") if data.get("source_language") not in (None, "", "auto") else data.get("detected_language")
    ) or "the original language"
    plan = plan_path(directory, target)
    for stale in (plan, plan.with_name(plan.stem + ".partial.jsonl")):
        stale.unlink(missing_ok=True)
    run([
        sys.executable, str(ROOT / "translate_srt.py"), str(transcript_path(directory)),
        "--target-language", str(target),
        "--source-language", source_language,
        "--target-language-id", code_for(target),
        "--output-dir", str(work),
        *llm_script_args(settings),
    ], tr("Перевожу фразы: {target}", target=target))
    if not plan.is_file():
        raise ProjectError(tr("Перевод не завершён: план озвучки не создан. Подробности в логе выше."))
    item = track(data, target)
    reset_track(item)
    item["translated"] = True


def transcribe_sample(directory, path, settings, speaker_id="S1"):
    """Transcript of an uploaded voice sample (cloning works best with it)."""
    asr = settings["asr"]
    run([
        sys.executable, str(ROOT / "transcribe.py"), str(path),
        "--model", str(asr.get("model") or "medium"),
        "--language", "auto",
        "--device", str(asr.get("device") or "cuda"),
        "--compute-type", str(asr.get("computeType") or "float16"),
        "--output-dir", str(directory / "voice"),
        "--output-prefix", f"sample_transcript_{speaker_id.lower()}",
    ], tr("Распознаю образец голоса"))
    return (directory / "voice" / f"sample_transcript_{speaker_id.lower()}.txt").read_text(encoding="utf-8").strip()


def speaker_voices(directory, data, settings):
    """{speaker: {"ref_audio", "ref_text"} or {"voice_id"}} for dub.py --voices."""
    voices = {}
    for speaker_id, item in data["speakers"].items():
        voice = item["voice"]
        mode = voice.get("mode") or "original"
        label = item.get("name") or speaker_id
        if mode == "original" and voice.get("auto_file") and (directory / voice["auto_file"]).is_file():
            voices[speaker_id] = {"ref_audio": str(directory / voice["auto_file"]), "ref_text": voice.get("auto_text") or ""}
        elif mode == "file" and voice.get("file"):
            sample = directory / voice["file"]
            if not sample.is_file():
                raise ProjectError(tr("Файл образца голоса не найден, загрузите его заново на шаге «Голос»."))
            if not voice.get("ref_text"):
                voice["ref_text"] = transcribe_sample(directory, sample, settings, speaker_id)
                save_project(directory, data)
            voices[speaker_id] = {"ref_audio": str(sample), "ref_text": voice["ref_text"]}
        elif mode == "engine" and voice.get("voice_id"):
            voices[speaker_id] = {"voice_id": voice["voice_id"]}
        elif mode == "original":
            print("WARNING: " + tr("{speaker}: образца голоса оригинала нет, движок возьмёт голос по умолчанию.",
                                   speaker=label), flush=True)
    return voices


def stage_voice(directory, data, settings, target, only=None):
    plan = plan_path(directory, target)
    if not plan.is_file():
        raise ProjectError(tr("Сначала переведите исходник на {target} (шаг «Исходник»).", target=target))
    tts = settings["tts"]
    engine_id = tts.get("engine") or "omnivoice"
    manifest = tts_engines.load_manifest(engine_id)
    code = code_for(target)
    voices_file = directory / "work" / "voices.json"
    voices_file.write_text(json.dumps(speaker_voices(directory, data, settings), ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    command = [
        sys.executable, str(ROOT / "dub.py"), str(plan),
        "--engine", engine_id,
        "--device", str(tts.get("device") or "auto"),
        "--engine-settings", json.dumps(tts_engines.resolve_settings(manifest), ensure_ascii=False),
        "--language", code,
        "--voices", str(voices_file),
    ]

    timing = data["timing"]
    output = directory / "output" / f"{output_base(data)}.{code}.voice.wav"
    command += [
        "--speed", str(float(timing.get("speed") or 1.0)),
        "--tail-room", str(float(timing.get("tail_room") or 0.6)),
        "--max-auto-speed", str(float(timing.get("max_auto_speed") or 1.18)),
        "--auto-fit",
        "--post-clean", str(timing.get("post_clean") or "soft"),
        "--post-normalize",
        "--keep-segments",
        "--segments-dir", str(segments_dir(directory, target)),
        "--output", str(output),
    ]
    if only is not None:
        command += ["--only", ",".join(unit_key(unit_id) for unit_id in only)]
    run(command, tr("Озвучиваю ({target}): {engine}", target=target, engine=manifest["name"]))
    track(data, target)["voice_track"] = output.relative_to(directory).as_posix()


def build_mix_command(voice, background, music, duration, mix, output):
    """ffmpeg arguments that mix the voice track with the original background and optional music."""
    inputs = ["-i", str(voice)]
    filters = [f"[0:a]aresample=48000,aformat=channel_layouts=stereo,volume={float(mix.get('voice', 1.0)):.3f}[a0]"]
    labels = ["[a0]"]
    if background and float(mix.get("background", 0)) > 0:
        inputs += ["-i", str(background)]
        index = len(labels)
        filters.append(f"[{index}:a]aresample=48000,aformat=channel_layouts=stereo,volume={float(mix['background']):.3f}[a{index}]")
        labels.append(f"[a{index}]")
    if music and float(mix.get("music", 0)) > 0:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        index = len(labels)
        fade_start = max(0.0, duration - 2.0)
        filters.append(
            f"[{index}:a]aresample=48000,aformat=channel_layouts=stereo,volume={float(mix['music']):.3f},"
            f"afade=t=out:st={fade_start:.2f}:d=2[a{index}]"
        )
        labels.append(f"[a{index}]")
    chain = f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0"
    if mix.get("loudnorm", True):
        chain += ",loudnorm=I=-16:TP=-1.5:LRA=11"
    chain += ",alimiter=limit=0.95,apad[out]"
    filters.append(chain)
    return [*inputs, "-filter_complex", ";".join(filters), "-map", "[out]", "-t", f"{duration:.3f}",
            "-ar", "48000", "-c:a", "pcm_s16le", str(output)]


def stage_mix(directory, data, target):
    item = track(data, target)
    voice_track = directory / (item.get("voice_track") or "")
    if not item.get("voice_track") or not voice_track.is_file():
        raise ProjectError(tr("Сначала озвучьте перевод на {target} (шаг «Голос»).", target=target))
    background = directory / "work" / "background.wav" if data.get("separated") else None
    music = directory / data["mix"]["music_file"] if data["mix"].get("music_file") else None
    if music is not None and not music.is_file():
        music = None
    code = code_for(target)
    mix_path = directory / "output" / f"{output_base(data)}.{code}.mix.wav"
    duration = float(data["source"].get("duration") or 0) or sf.info(str(voice_track)).duration
    run_ffmpeg(build_mix_command(voice_track, background, music, duration, data["mix"], mix_path),
               tr("Свожу голос с фоном ({target})", target=target))
    item["mix_track"] = mix_path.relative_to(directory).as_posix()

    subtitles = translated_srt_path(directory, target)
    if subtitles.is_file():
        destination = directory / "output" / f"{output_base(data)}.{code}.srt"
        shutil.copy2(subtitles, destination)
        item["subtitles"] = destination.relative_to(directory).as_posix()


def stage_mux(directory, data, target):
    item = track(data, target)
    if not data["source"].get("has_video"):
        item["video"] = ""
        print(tr("Исходник без видео: готовый результат — сведённый звук."), flush=True)
        return
    source = directory / data["source"]["file"]
    suffix = ".mp4" if source.suffix.lower() in MP4_FRIENDLY else ".mkv"
    code = code_for(target)
    output = directory / "output" / f"{output_base(data)}.{code}{suffix}"
    args = ["-i", str(source), "-i", str(directory / item["mix_track"]),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-metadata:s:a:0", f"language={code}", "-shortest"]
    if suffix == ".mp4":
        args += ["-movflags", "+faststart"]
    run_ffmpeg([*args, str(output)], tr("Собираю видео с дубляжом ({target})", target=target))
    item["video"] = output.relative_to(directory).as_posix()


# --------------------------------------------------------------------------
# Phrases: edit, shorten, re-voice


def set_translations(directory, data, changes, target=None, edited=True):
    """Write new translations into the units file, the voice plan and the subtitles."""
    import translate_srt

    target = target or data["active"]
    changes = {int(key): str(value).strip() for key, value in (changes or {}).items()}
    if any(not value for value in changes.values()):
        raise ProjectError(tr("Перевод фразы не может быть пустым."))
    units_file = units_path(directory, target)
    plan = plan_path(directory, target)
    if not units_file.is_file() or not plan.is_file():
        raise ProjectError(tr("Сначала распознайте и переведите исходник."))

    payload = json.loads(units_file.read_text(encoding="utf-8"))
    units = payload.get("translation_units") or []
    known = {int(unit["id"]) for unit in units}
    missing = sorted(set(changes) - known)
    if missing:
        raise ProjectError(tr("Нет фраз с номерами: {ids}", ids=missing))
    changed = set()
    for unit in units:
        new_text = changes.get(int(unit["id"]))
        if new_text is not None and new_text != (unit.get("translation") or "").strip():
            unit["translation"] = new_text
            changed.add(int(unit["id"]))
    if not changed:
        return data
    units_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = []
    for line in plan.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        for unit_id in changed:
            if item.get("id") == unit_key(unit_id):
                item["text"] = changes[unit_id]
        lines.append(json.dumps(item, ensure_ascii=False))
    plan.write_text("\n".join(lines) + "\n", encoding="utf-8")
    translate_srt.write_srt(translated_srt_path(directory, target), units)

    item = track(data, target)
    if edited:
        item["edited"] = sorted(set(item.get("edited") or []) | changed)
    if item.get("voiced"):
        item["dirty"] = sorted(set(item.get("dirty") or []) | changed)
        item["exported"] = False
    save_project(directory, data)
    return data


def long_phrase_ids(directory, data, target=None, max_speedup=1.1):
    """Phrases that had to be sped up noticeably or were cut during the last voicing."""
    target = target or data["active"]
    report = phrase_report(directory, data, target)
    result = []
    for unit in load_units(directory, target):
        entry = report.get(unit_key(unit["id"])) or {}
        if (entry.get("clipped_seconds") or 0) > 0.05 or (entry.get("speedup") or 1.0) >= max_speedup:
            result.append(int(unit["id"]))
    return result


def llm_for(settings):
    import llm_client

    llm = settings["llm"]
    return llm_client.LLMClient(
        provider=llm.get("provider"),
        base_url=llm.get("url") or "",
        model=llm.get("model") or "",
        context_length=int(llm.get("contextLength") or 8192),
        auto_load=bool(llm.get("autoLoad", True)),
        unload_others=bool(llm.get("unloadOthers", True)),
        unload_when_done=bool(llm.get("unloadWhenDone", True)),
        timeout=180,
    )


def shorten_phrases(directory, data, ids, settings, target=None, batch_size=8):
    """Ask the translation model for shorter variants that fit the phrase slots."""
    import translate_srt

    target = target or data["active"]
    ids = [int(value) for value in ids]
    units = {int(unit["id"]): unit for unit in load_units(directory, target)}
    report = phrase_report(directory, data, target)
    wanted = [units[unit_id] for unit_id in ids if unit_id in units]
    if not wanted:
        print(tr("Нет фраз для сокращения."), flush=True)
        return {}

    llm = llm_for(settings)
    results = {}
    try:
        llm.prepare()
        for offset in range(0, len(wanted), batch_size):
            batch = wanted[offset:offset + batch_size]
            items = []
            for unit in batch:
                entry = report.get(unit_key(unit["id"])) or {}
                slot = float(unit.get("duration") or 0) or 1.0
                spoken = float(entry.get("natural_seconds") or 0)
                words = len(str(unit.get("translation") or "").split())
                ratio = min(0.95, slot / spoken) if spoken > slot else 0.8
                items.append({
                    "id": int(unit["id"]),
                    "source": unit.get("source"),
                    "translation": unit.get("translation"),
                    "slot_seconds": round(slot, 2),
                    "spoken_seconds": round(spoken, 2) if spoken else None,
                    "max_words": max(2, int(words * ratio)),
                })
            messages = [
                {"role": "system", "content": (
                    "You are a dubbing editor. Shorten each translation so it can be spoken within slot_seconds. "
                    "Keep the meaning, names and numbers, keep the same language and a natural spoken style. "
                    "Return only valid JSON."
                )},
                {"role": "user", "content": json.dumps({
                    "task": "shorten_dubbing_lines",
                    "language": target,
                    "rules": [
                        "Return a JSON object only: {\"items\": [{\"id\": number, \"translation\": string}]}.",
                        "Use at most max_words words per item.",
                        "Drop filler words first, then rephrase more compactly.",
                    ],
                    "dubbing_units": items,
                }, ensure_ascii=False)},
            ]
            content = llm.complete(messages, temperature=0.3, schema=translate_srt.TRANSLATION_SCHEMA)
            for item in translate_srt.extract_json_array(content):
                if isinstance(item, dict) and int(item.get("id", -1)) in units and str(item.get("translation") or "").strip():
                    results[int(item["id"])] = str(item["translation"]).strip()
            progress(min(len(wanted), offset + len(batch)), len(wanted), tr("Сокращение фраз"))
    finally:
        llm.finish()

    for unit_id, new_text in results.items():
        print(f"  {unit_id}: {units[unit_id].get('translation')} -> {new_text}", flush=True)
    set_translations(directory, data, results, target)
    return results


# --------------------------------------------------------------------------
# Running stages

STAGES = ("prepare", "translate", "voice", "export", "all", "shorten", "revoice")
LABELS = {
    "extract": "Извлечение звука",
    "separate": "Отделение голоса от фона",
    "transcribe": "Распознавание речи",
    "diarize": "Определение говорящих",
    "sample": "Образцы голосов",
    "translate": "Перевод",
    "voice": "Озвучка",
    "mix": "Сведение",
    "mux": "Сборка видео",
    "shorten": "Сокращение длинных фраз",
    "revoice": "Переозвучка изменённых фраз",
}


def plan_steps(data, which, targets=None):
    """[(key, target_or_None), ...] for a stage name; 'all' only does what is not done yet."""
    targets = [t for t in (targets or data["targets"]) if t in data["targets"]] or list(data["targets"])
    tracks = {t: track(data, t) for t in targets}
    source_steps = [("extract", None), ("separate", None), ("transcribe", None), ("diarize", None), ("sample", None)]
    speaker_steps = [] if data.get("diarized") or not data.get("transcribed") else [("diarize", None), ("sample", None)]
    export_steps = lambda items: [step for t in items for step in (("mix", t), ("mux", t))]  # noqa: E731
    if which == "prepare":
        return source_steps + [("translate", t) for t in targets]
    if which == "translate":
        pending = [t for t in targets if not tracks[t]["translated"]] or targets
        return speaker_steps + [("translate", t) for t in pending]
    if which == "voice":
        return [("voice", t) for t in targets]
    if which == "export":
        return export_steps(targets)
    if which == "shorten":
        return [("shorten", data["active"])]
    if which == "revoice":
        return [("revoice", data["active"]), ("mix", data["active"]), ("mux", data["active"])]
    if which == "all":
        steps = speaker_steps if data.get("transcribed") else list(source_steps)
        fresh = bool(steps)  # new phrases: everything after them is redone
        steps += [("translate", t) for t in targets if fresh or not tracks[t]["translated"]]
        steps += [("voice", t) for t in targets if fresh or not tracks[t]["voiced"]]
        steps += export_steps([t for t in targets if fresh or not tracks[t]["exported"]])
        return steps
    raise ProjectError(f"Unknown stage: {which}")


def gpu_heavy_before_voice(settings, which):
    """True when the stage will run separation or a local LLM on this machine."""
    import llm_client

    if which in ("prepare", "all"):
        return True  # vocal separation and Whisper
    return bool(llm_client.provider_preset(settings["llm"].get("provider")).get("local", True))


def run_stages(directory, which, ids=None, targets=None):
    directory = Path(directory).resolve()
    data = load_project(directory)
    if not data.get("source"):
        raise ProjectError(tr("У проекта нет исходника."))
    settings = load_settings()
    steps = plan_steps(data, which, targets)
    if not steps:
        print(tr("Всё уже готово: делать нечего."), flush=True)
        return
    data["error"] = ""
    data["running"] = which
    save_project(directory, data)
    if which in ("prepare", "all", "translate", "shorten") and gpu_heavy_before_voice(settings, which):
        # Separation and a local LLM need the VRAM a warm TTS model is holding.
        if tts_engines.stop_resident():
            print(tr("Модель озвучки выгружена, чтобы освободить видеопамять."), flush=True)
    try:
        for index, (key, target) in enumerate(steps, start=1):
            label = tr(LABELS[key]) + (f" · {target}" if target and len(data["targets"]) > 1 else "")
            stage(index, len(steps), label)
            item = track(data, target) if target else None
            if key == "extract":
                data["transcribed"] = data["diarized"] = False
                for value in data["tracks"].values():
                    reset_track(value)
                stage_extract(directory, data)
            elif key == "separate":
                stage_separate(directory, data, settings)
            elif key == "transcribe":
                stage_transcribe(directory, data, settings)
            elif key == "diarize":
                stage_diarize(directory, data, settings)
            elif key == "sample":
                stage_voice_sample(directory, data)
            elif key == "translate":
                stage_translate(directory, data, settings, target)
            elif key == "voice":
                item.update(voiced=False, exported=False)
                stage_voice(directory, data, settings, target)
                item.update(voiced=True, dirty=[])
            elif key == "shorten":
                wanted = ids or long_phrase_ids(directory, data, target)
                if not wanted:
                    print(tr("Все фразы укладываются в тайминг, сокращать нечего."), flush=True)
                shorten_phrases(directory, data, wanted, settings, target)
                data = load_project(directory)
            elif key == "revoice":
                if not item.get("voiced"):
                    raise ProjectError(tr("Сначала озвучьте перевод целиком (шаг «Голос»)."))
                wanted = sorted(set(ids or item.get("dirty") or []))
                if wanted:
                    stage_voice(directory, data, settings, target, only=wanted)
                else:
                    print(tr("Изменённых фраз нет: пересобираю ролик из готовой озвучки."), flush=True)
                item["dirty"] = [unit_id for unit_id in item.get("dirty") or [] if unit_id not in wanted]
            elif key == "mix":
                item["exported"] = False
                stage_mix(directory, data, target)
            elif key == "mux":
                stage_mux(directory, data, target)
                item["exported"] = True
            save_project(directory, data)
            progress(1, 1, label)
    except Exception as exc:
        data["error"] = str(exc)
        raise
    finally:
        data["running"] = ""
        save_project(directory, data)
    print(tr("Готово."), flush=True)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Dubbing projects.")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="Create a project from a video or audio file.")
    create.add_argument("file")
    create.add_argument("--name", default="")
    create.add_argument("--target", action="append", default=[], help="Target language (repeat for several).")
    create.add_argument("--speakers", type=int, default=1, help="1 = one voice, 0 = find the speakers, N = exactly N.")
    runner = sub.add_parser("run", help="Run stages of a project.")
    runner.add_argument("project", help="Project folder or id")
    runner.add_argument("stage", choices=STAGES)
    runner.add_argument("--ids", default="", help="Phrase numbers for shorten / revoice, e.g. 3,5,8.")
    runner.add_argument("--targets", default="", help="Limit to these languages, e.g. English,Spanish.")
    sub.add_parser("list", help="List projects.")
    args = parser.parse_args()

    try:
        if args.command == "create":
            directory, _ = create_from_file(args.file, args.name, args.target or ["English"], args.speakers)
            print(f"project={directory}")
        elif args.command == "run":
            directory = Path(args.project)
            if not (directory / "project.json").is_file():
                directory = project_dir(args.project)
            ids = [int(value) for value in args.ids.split(",") if value.strip()] if args.ids else None
            targets = [value.strip() for value in args.targets.split(",") if value.strip()] or None
            run_stages(directory, args.stage, ids, targets)
        else:
            for item in list_projects():
                print(f"{item['id']}  {item['name']}  -> {', '.join(item['targets'])}  {item['state']}")
    except ProjectError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
    except tts_engines.EngineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
