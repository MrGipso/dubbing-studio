"""Dubbing projects: one folder per video, every stage keeps its files there.

projects/<id>/
  project.json     what the user chose and how far the project got
  source/          the original video or audio
  work/            audio.wav, vocals.wav, background.wav, transcript.*, <lang> plan
  voice/           voice samples (cut from the original or uploaded)
  output/          finished files: video with the dub, mixed audio, voice track, subtitles

Stages (each can be re-run on its own):
  prepare  extract audio -> separate voice/background -> Whisper -> translate -> voice sample
  voice    synthesize the translation with the chosen TTS engine (dub.py)
  export   mix voice + original background (+ music) and put it back into the video

CLI:
  python project.py create clip.mp4 --target English
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
from languages import language_id_for, language_name_for


ROOT = Path(__file__).resolve().parent
PROJECTS_DIR = ROOT / "projects"
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac"}
MP4_FRIENDLY = {".mp4", ".mov", ".m4v"}

DEFAULT_PROJECT = {
    "name": "",
    "source_language": "auto",
    "target": "English",
    "separate": True,
    "voice": {"mode": "original", "file": "", "ref_text": "", "voice_id": "", "auto_file": "", "auto_text": ""},
    "timing": {"speed": 1.0, "tail_room": 0.6, "max_auto_speed": 1.18, "post_clean": "soft"},
    "mix": {"voice": 1.0, "background": 0.8, "music": 0.25, "music_file": "", "loudnorm": True},
    "state": {"prepared": False, "voiced": False, "exported": False},
    "edited": [],
    "dirty": [],
}


class ProjectError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Storage


def slugify(value, fallback="project"):
    value = re.sub(r"\s+", "_", str(value or "").strip())
    value = re.sub(r"[^\w-]+", "_", value).strip("_")
    return value[:48] or fallback


def project_dir(project_id):
    path = (PROJECTS_DIR / str(project_id)).resolve()
    if path.parent != PROJECTS_DIR.resolve() or not (path / "project.json").is_file():
        raise ProjectError(f"Project not found: {project_id}")
    return path


def merge_defaults(data):
    merged = json.loads(json.dumps(DEFAULT_PROJECT))
    for key, value in (data or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def load_project(directory):
    directory = Path(directory)
    data = json.loads((directory / "project.json").read_text(encoding="utf-8"))
    data = merge_defaults(data)
    data["id"] = directory.name
    return data


def save_project(directory, data):
    directory = Path(directory)
    data = dict(data)
    data.pop("id", None)
    data["updated"] = time.time()
    tmp = directory / "project.json.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(directory / "project.json")


def update_project(directory, changes):
    """Merge changes from the UI (nested dicts are merged, not replaced)."""
    data = load_project(directory)
    allowed = {"name", "source_language", "target", "separate", "voice", "timing", "mix"}
    for key, value in (changes or {}).items():
        if key not in allowed:
            continue
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value
    if {"source_language", "target", "separate"} & set(changes or {}):
        # Changing what goes in invalidates what came out.
        data["state"] = {"prepared": False, "voiced": False, "exported": False}
    elif "voice" in (changes or {}) or "timing" in (changes or {}):
        data["state"]["voiced"] = False
        data["state"]["exported"] = False
    elif "mix" in (changes or {}):
        data["state"]["exported"] = False
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


def create_from_file(path, name="", target="English"):
    path = Path(path)
    directory = new_project(name, path.name)
    destination = directory / "source" / path.name
    shutil.copy2(path, destination)
    data = attach_source(directory, destination)
    if target:
        update_project(directory, {"target": target})
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
        "target": data.get("target"),
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
        raise ProjectError(f"{name} was not found. Install ffmpeg and add it to PATH (winget install Gyan.FFmpeg).")
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
        raise ProjectError(f"{label or command[0]} failed with exit code {result.returncode}.")


def run_ffmpeg(args, label):
    run([ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args], label)


# --------------------------------------------------------------------------
# Voice sample from the original


def pick_reference(segments, min_len=6.0, target=12.0, max_len=18.0, max_gap=0.8):
    """Choose the cleanest run of consecutive speech for voice cloning.

    segments: Whisper segments with start, end, text, avg_logprob, no_speech_prob.
    Returns (start, end, text) or None.
    """
    good = [s for s in segments if s.get("no_speech_prob", 0) < 0.6 and s["end"] > s["start"]]
    best = None
    for i in range(len(good)):
        start = good[i]["start"]
        confidence = 0.0
        texts = []
        for j in range(i, len(good)):
            if j > i and good[j]["start"] - good[j - 1]["end"] > max_gap:
                break
            end = good[j]["end"]
            span = end - start
            if span > max_len:
                break
            length = good[j]["end"] - good[j]["start"]
            confidence += math.exp(good[j].get("avg_logprob", -1.0)) * length
            texts.append(good[j]["text"].strip())
            if span >= min_len:
                # Average confidence, slightly preferring spans close to the target length.
                score = confidence / span * min(1.0, span / target)
                if best is None or score > best[0]:
                    best = (score, start, end, " ".join(texts))
    if best:
        return best[1], best[2], best[3]
    longest = max(good, key=lambda s: s["end"] - s["start"], default=None)
    if longest and longest["end"] - longest["start"] >= 2.5:
        return longest["start"], longest["end"], longest["text"].strip()
    return None


def cut_reference(source_wav, start, end, destination, margin=0.12):
    audio, rate = sf.read(str(source_wav), dtype="float32", always_2d=True)
    a = max(0, int((start - margin) * rate))
    b = min(len(audio), int((end + margin) * rate))
    clip = audio[a:b].mean(axis=1)
    peak = float(np.max(np.abs(clip))) if len(clip) else 0.0
    if peak > 1e-6:
        clip = clip / peak * 0.9
    fade = min(len(clip) // 4, int(0.02 * rate))
    if fade > 1:
        clip[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
        clip[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(destination), clip, rate)
    return len(clip) / rate


# --------------------------------------------------------------------------
# Stages


def target_code(data):
    return language_id_for(data.get("target")) or slugify(data.get("target"), "xx").lower()


def plan_path(directory, data):
    import translate_srt

    return Path(directory) / "work" / f"transcript.{translate_srt.slugify(data['target'])}.dub.jsonl"


def output_base(data):
    return slugify(data.get("name") or "dub")


def stage_extract(directory, data):
    source = directory / data["source"]["file"]
    run_ffmpeg(["-i", str(source), "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(directory / "work" / "audio.wav")],
               "Извлекаю звук из исходника")


def stage_separate(directory, data, settings):
    work = directory / "work"
    for name in ("vocals.wav", "background.wav"):
        (work / name).unlink(missing_ok=True)
    data["separated"] = False
    if not data.get("separate"):
        print("Отделение фона выключено: распознаю исходный звук целиком.", flush=True)
        return
    engine_id = settings["separation"].get("engine") or "demucs"
    try:
        manifest = tts_engines.load_manifest(engine_id)
    except tts_engines.EngineError:
        print(f"WARNING: движок отделения фона '{engine_id}' не найден, продолжаю без него.", flush=True)
        return
    if not tts_engines.engine_status(manifest).get("installed"):
        print(f"WARNING: {manifest['name']} не установлен (Настройки → Отделение фона). Продолжаю без отделения фона.", flush=True)
        return
    with tts_engines.EngineSession(engine_id) as session:
        session.load(tts_engines.resolve_settings(manifest), settings["separation"].get("device") or "auto")
        session.separate(work / "audio.wav", work)
    data["separated"] = (work / "vocals.wav").is_file() and (work / "background.wav").is_file()


def stage_transcribe(directory, data, settings):
    work = directory / "work"
    speech = work / ("vocals.wav" if data.get("separated") else "audio.wav")
    asr = settings["asr"]
    language = language_id_for(data.get("source_language")) or "auto"
    run([
        sys.executable, str(ROOT / "transcribe.py"), str(speech),
        "--model", str(asr.get("model") or "medium"),
        "--language", language,
        "--device", str(asr.get("device") or "cuda"),
        "--compute-type", str(asr.get("computeType") or "float16"),
        "--output-dir", str(work),
        "--output-prefix", "transcript",
    ], "Распознаю речь (Whisper)")
    transcript = json.loads((work / "transcript.json").read_text(encoding="utf-8"))
    data["detected_language"] = transcript.get("language")
    if not transcript.get("segments"):
        raise ProjectError("Whisper не нашёл речь в исходнике.")


def stage_translate(directory, data, settings):
    work = directory / "work"
    source_language = language_name_for(
        data.get("source_language") if data.get("source_language") not in (None, "", "auto") else data.get("detected_language")
    ) or "the original language"
    plan = plan_path(directory, data)
    for stale in (plan, plan.with_name(plan.stem + ".partial.jsonl")):
        stale.unlink(missing_ok=True)
    run([
        sys.executable, str(ROOT / "translate_srt.py"), str(work / "transcript.srt"),
        "--target-language", str(data["target"]),
        "--source-language", source_language,
        "--target-language-id", target_code(data),
        "--output-dir", str(work),
        *llm_script_args(settings),
    ], "Перевожу фразы")
    if not plan.is_file():
        raise ProjectError("Перевод не завершён: план озвучки не создан. Подробности в логе выше.")


def stage_voice_sample(directory, data):
    """Cut a voice sample of the original speaker (used when voice mode = original)."""
    work = directory / "work"
    transcript = json.loads((work / "transcript.json").read_text(encoding="utf-8"))
    choice = pick_reference(transcript.get("segments") or [])
    voice = data["voice"]
    voice["auto_file"] = ""
    voice["auto_text"] = ""
    if not choice:
        print("WARNING: не нашёл подходящего отрывка речи для образца голоса.", flush=True)
        return
    start, end, text = choice
    speech = work / ("vocals.wav" if data.get("separated") else "audio.wav")
    seconds = cut_reference(speech, start, end, directory / "voice" / "original.wav")
    voice["auto_file"] = "voice/original.wav"
    voice["auto_text"] = text
    voice["auto_range"] = [round(start, 2), round(end, 2)]
    print(f"Образец голоса: {seconds:.1f} с ({start:.1f}–{end:.1f} с исходника)", flush=True)


def transcribe_sample(directory, path, settings):
    """Transcript of an uploaded voice sample (cloning works best with it)."""
    asr = settings["asr"]
    run([
        sys.executable, str(ROOT / "transcribe.py"), str(path),
        "--model", str(asr.get("model") or "medium"),
        "--language", "auto",
        "--device", str(asr.get("device") or "cuda"),
        "--compute-type", str(asr.get("computeType") or "float16"),
        "--output-dir", str(directory / "voice"),
        "--output-prefix", "sample_transcript",
    ], "Распознаю образец голоса")
    return (directory / "voice" / "sample_transcript.txt").read_text(encoding="utf-8").strip()


def stage_voice(directory, data, settings, only=None):
    plan = plan_path(directory, data)
    if not plan.is_file():
        raise ProjectError("Сначала выполните шаг «Исходник»: распознать и перевести.")
    tts = settings["tts"]
    engine_id = tts.get("engine") or "omnivoice"
    manifest = tts_engines.load_manifest(engine_id)
    voice = data["voice"]
    command = [
        sys.executable, str(ROOT / "dub.py"), str(plan),
        "--engine", engine_id,
        "--device", str(tts.get("device") or "auto"),
        "--engine-settings", json.dumps(tts_engines.resolve_settings(manifest), ensure_ascii=False),
        "--language", target_code(data),
    ]
    mode = voice.get("mode") or "original"
    if mode == "original" and voice.get("auto_file"):
        command += ["--ref-audio", str(directory / voice["auto_file"]), "--ref-text", voice.get("auto_text") or ""]
    elif mode == "file" and voice.get("file"):
        sample = directory / voice["file"]
        if not sample.is_file():
            raise ProjectError("Файл образца голоса не найден, загрузите его заново на шаге «Голос».")
        if not voice.get("ref_text"):
            voice["ref_text"] = transcribe_sample(directory, sample, settings)
            save_project(directory, data)
        command += ["--ref-audio", str(sample), "--ref-text", voice["ref_text"]]
    elif mode == "engine" and voice.get("voice_id"):
        command += ["--voice-id", voice["voice_id"]]
    elif mode == "original":
        print("WARNING: образца голоса оригинала нет, движок возьмёт голос по умолчанию.", flush=True)

    timing = data["timing"]
    code = target_code(data)
    output = directory / "output" / f"{output_base(data)}.{code}.voice.wav"
    command += [
        "--speed", str(float(timing.get("speed") or 1.0)),
        "--tail-room", str(float(timing.get("tail_room") or 0.6)),
        "--max-auto-speed", str(float(timing.get("max_auto_speed") or 1.18)),
        "--auto-fit",
        "--post-clean", str(timing.get("post_clean") or "soft"),
        "--post-normalize",
        "--keep-segments",
        "--segments-dir", str(directory / "work" / f"segments_{code}"),
        "--output", str(output),
    ]
    if only is not None:
        command += ["--only", ",".join(unit_key(unit_id) for unit_id in only)]
    run(command, f"Озвучиваю: {manifest['name']}")
    data["voice_track"] = output.relative_to(directory).as_posix()


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


def stage_mix(directory, data):
    voice_track = directory / (data.get("voice_track") or "")
    if not data.get("voice_track") or not voice_track.is_file():
        raise ProjectError("Сначала озвучьте перевод (шаг «Голос»).")
    background = directory / "work" / "background.wav" if data.get("separated") else None
    music = directory / data["mix"]["music_file"] if data["mix"].get("music_file") else None
    if music is not None and not music.is_file():
        music = None
    code = target_code(data)
    mix_path = directory / "output" / f"{output_base(data)}.{code}.mix.wav"
    duration = float(data["source"].get("duration") or 0) or sf.info(str(voice_track)).duration
    run_ffmpeg(build_mix_command(voice_track, background, music, duration, data["mix"], mix_path), "Свожу голос с фоном")
    data["mix_track"] = mix_path.relative_to(directory).as_posix()

    subtitles = plan_path(directory, data).with_name(plan_path(directory, data).name.replace(".dub.jsonl", ".srt"))
    if subtitles.is_file():
        destination = directory / "output" / f"{output_base(data)}.{code}.srt"
        shutil.copy2(subtitles, destination)
        data["subtitles"] = destination.relative_to(directory).as_posix()


def stage_mux(directory, data):
    if not data["source"].get("has_video"):
        data["video"] = ""
        print("Исходник без видео: готовый результат — сведённый звук.", flush=True)
        return
    source = directory / data["source"]["file"]
    suffix = ".mp4" if source.suffix.lower() in MP4_FRIENDLY else ".mkv"
    code = target_code(data)
    output = directory / "output" / f"{output_base(data)}.{code}{suffix}"
    args = ["-i", str(source), "-i", str(directory / data["mix_track"]),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-metadata:s:a:0", f"language={code}", "-shortest"]
    if suffix == ".mp4":
        args += ["-movflags", "+faststart"]
    run_ffmpeg([*args, str(output)], "Собираю видео с дубляжом")
    data["video"] = output.relative_to(directory).as_posix()


# --------------------------------------------------------------------------
# Phrases: edit, shorten, re-voice


def units_path(directory, data):
    plan = plan_path(directory, data)
    return plan.with_name(plan.name.replace(".dub.jsonl", ".segments.json"))


def translated_srt_path(directory, data):
    plan = plan_path(directory, data)
    return plan.with_name(plan.name.replace(".dub.jsonl", ".srt"))


def segments_dir(directory, data):
    return Path(directory) / "work" / f"segments_{target_code(data)}"


def unit_key(unit_id):
    return f"unit_{int(unit_id):04d}"


def load_units(directory, data):
    path = units_path(directory, data)
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("translation_units") or []


def phrase_report(directory, data):
    """Fit report of the last voicing: {"unit_0003": {...}}."""
    if not data.get("voice_track"):
        return {}
    track = Path(directory) / data["voice_track"]
    report = track.with_name(track.stem + ".report.json")
    if not report.is_file():
        return {}
    return {item["id"]: item for item in json.loads(report.read_text(encoding="utf-8")).get("segments") or []}


def set_translations(directory, data, changes, edited=True):
    """Write new translations into the units file, the voice plan and the subtitles."""
    import translate_srt

    changes = {int(key): str(value).strip() for key, value in (changes or {}).items()}
    if any(not value for value in changes.values()):
        raise ProjectError("Перевод фразы не может быть пустым.")
    units_file = units_path(directory, data)
    plan = plan_path(directory, data)
    if not units_file.is_file() or not plan.is_file():
        raise ProjectError("Сначала распознайте и переведите исходник.")

    payload = json.loads(units_file.read_text(encoding="utf-8"))
    units = payload.get("translation_units") or []
    known = {int(unit["id"]) for unit in units}
    missing = sorted(set(changes) - known)
    if missing:
        raise ProjectError(f"Нет фраз с номерами: {missing}")
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
    translate_srt.write_srt(translated_srt_path(directory, data), units)

    if edited:
        data["edited"] = sorted(set(data.get("edited") or []) | changed)
    if data["state"].get("voiced"):
        data["dirty"] = sorted(set(data.get("dirty") or []) | changed)
        data["state"]["exported"] = False
    save_project(directory, data)
    return data


def long_phrase_ids(directory, data, max_speedup=1.1):
    """Phrases that had to be sped up noticeably or were cut during the last voicing."""
    report = phrase_report(directory, data)
    result = []
    for unit in load_units(directory, data):
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


def shorten_phrases(directory, data, ids, settings, batch_size=8):
    """Ask the translation model for shorter variants that fit the phrase slots."""
    import translate_srt

    ids = [int(value) for value in ids]
    units = {int(unit["id"]): unit for unit in load_units(directory, data)}
    report = phrase_report(directory, data)
    wanted = [units[unit_id] for unit_id in ids if unit_id in units]
    if not wanted:
        print("Нет фраз для сокращения.", flush=True)
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
                    "language": data.get("target"),
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
            progress(min(len(wanted), offset + len(batch)), len(wanted), "Сокращение фраз")
    finally:
        llm.finish()

    for unit_id, new_text in results.items():
        print(f"  {unit_id}: {units[unit_id].get('translation')} -> {new_text}", flush=True)
    set_translations(directory, data, results)
    return results


PLAN = {
    "prepare": [
        ("extract", "Извлечение звука"),
        ("separate", "Отделение голоса от фона"),
        ("transcribe", "Распознавание речи"),
        ("translate", "Перевод"),
        ("sample", "Образец голоса"),
    ],
    "voice": [("voice", "Озвучка")],
    "export": [("mix", "Сведение"), ("mux", "Сборка видео")],
    "shorten": [("shorten", "Сокращение длинных фраз")],
    "revoice": [("revoice", "Переозвучка изменённых фраз"), ("mix", "Сведение"), ("mux", "Сборка видео")],
}
PLAN["all"] = PLAN["prepare"] + PLAN["voice"] + PLAN["export"]


def run_stages(directory, which, ids=None):
    directory = Path(directory).resolve()
    data = load_project(directory)
    if not data.get("source"):
        raise ProjectError("У проекта нет исходника.")
    settings = load_settings()
    steps = PLAN[which]
    data["error"] = ""
    data["running"] = which
    save_project(directory, data)
    try:
        for index, (key, label) in enumerate(steps, start=1):
            stage(index, len(steps), label)
            if key == "extract":
                data["state"] = {"prepared": False, "voiced": False, "exported": False}
                stage_extract(directory, data)
            elif key == "separate":
                stage_separate(directory, data, settings)
            elif key == "transcribe":
                stage_transcribe(directory, data, settings)
            elif key == "translate":
                stage_translate(directory, data, settings)
                data["edited"] = []
                data["dirty"] = []
            elif key == "sample":
                stage_voice_sample(directory, data)
                data["state"]["prepared"] = True
            elif key == "voice":
                data["state"]["voiced"] = data["state"]["exported"] = False
                stage_voice(directory, data, settings)
                data["state"]["voiced"] = True
                data["dirty"] = []
            elif key == "shorten":
                targets = ids or long_phrase_ids(directory, data)
                if not targets:
                    print("Все фразы укладываются в тайминг, сокращать нечего.", flush=True)
                shorten_phrases(directory, data, targets, settings)
                data = load_project(directory)
            elif key == "revoice":
                if not data["state"].get("voiced"):
                    raise ProjectError("Сначала озвучьте перевод целиком (шаг «Голос»).")
                targets = sorted(set(ids or data.get("dirty") or []))
                if targets:
                    stage_voice(directory, data, settings, only=targets)
                else:
                    print("Изменённых фраз нет: пересобираю ролик из готовой озвучки.", flush=True)
                data["dirty"] = [unit_id for unit_id in data.get("dirty") or [] if unit_id not in targets]
            elif key == "mix":
                data["state"]["exported"] = False
                stage_mix(directory, data)
            elif key == "mux":
                stage_mux(directory, data)
                data["state"]["exported"] = True
            save_project(directory, data)
            progress(1, 1, label)
    except Exception as exc:
        data["error"] = str(exc)
        raise
    finally:
        data["running"] = ""
        save_project(directory, data)
    print("Готово.", flush=True)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Dubbing projects.")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="Create a project from a video or audio file.")
    create.add_argument("file")
    create.add_argument("--name", default="")
    create.add_argument("--target", default="English")
    runner = sub.add_parser("run", help="Run stages of a project.")
    runner.add_argument("project", help="Project folder or id")
    runner.add_argument("stage", choices=sorted(PLAN))
    runner.add_argument("--ids", default="", help="Phrase numbers for shorten / revoice, e.g. 3,5,8.")
    sub.add_parser("list", help="List projects.")
    args = parser.parse_args()

    try:
        if args.command == "create":
            directory, data = create_from_file(args.file, args.name, args.target)
            print(f"project={directory}")
        elif args.command == "run":
            directory = Path(args.project)
            if not (directory / "project.json").is_file():
                directory = project_dir(args.project)
            ids = [int(value) for value in args.ids.split(",") if value.strip()] if args.ids else None
            run_stages(directory, args.stage, ids)
        else:
            for item in list_projects():
                print(f"{item['id']}  {item['name']}  -> {item['target']}  {item['state']}")
    except ProjectError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
    except tts_engines.EngineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
