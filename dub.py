"""Dubbing core: voice a *.dub.jsonl plan with any TTS engine and mix one WAV.

Engine-independent work lives here: timing slots, auto-fit (engine speed retry
or pitch-preserving time-stretch), clipping with fades, pause cleanup, mixing
and normalization. The engine only turns text into audio (see engines/).

Plan format: one JSON object per line
  {"id": "unit_0001", "text": "...", "duration": 2.4,
   "source_start": 0.0, "source_end": 2.4,
   "language_id": "en", "ref_audio": "voice.wav", "ref_text": "...",
   "instruct": "...", "emotion": "calm"}

Examples:
  python dub.py outputs/x.en.dub.jsonl --engine omnivoice --auto-fit
  python dub.py outputs/x.en.dub.jsonl --engine omnivoice --set num_step=32
"""

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from tts_engines import EngineError, EngineSession, load_app_settings, load_manifest, resolve_settings


# --------------------------------------------------------------------------
# Plan


def read_jsonl(path):
    items = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            text = str(item.get("text") or "").strip()
            if not text:
                raise ValueError(f"Empty text in JSONL line {line_no}; refusing to synthesize.")
            items.append(item)
    if not items:
        raise ValueError(f"No items found in {path}")
    return items


def default_output_path(jsonl_path):
    name = jsonl_path.name
    base = name[: -len(".jsonl")] if name.endswith(".jsonl") else jsonl_path.stem
    if base.endswith(".dub"):
        return jsonl_path.with_name(base + ".wav")
    return jsonl_path.with_name(base + ".dub.wav")


# --------------------------------------------------------------------------
# Audio helpers


def read_audio(path):
    audio, sample_rate = sf.read(str(path), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return np.asarray(audio, dtype=np.float32), int(sample_rate)


def resample(audio, source_rate, target_rate):
    if source_rate == target_rate or not len(audio):
        return audio
    duration = len(audio) / float(source_rate)
    target_len = max(1, int(round(duration * target_rate)))
    source_x = np.linspace(0.0, duration, num=len(audio), endpoint=False)
    target_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
    return np.interp(target_x, source_x, audio).astype(np.float32)


def time_stretch(audio, sample_rate, factor):
    """Speed speech up by `factor` without changing pitch (ffmpeg atempo)."""
    if factor <= 1.001 or not len(audio):
        return audio
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("  WARNING: ffmpeg not found, cannot time-stretch; the phrase will be clipped.", flush=True)
        return audio
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.wav"
        dst = Path(tmp) / "out.wav"
        sf.write(str(src), audio, sample_rate, subtype="FLOAT")
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
             "-filter:a", f"atempo={factor:.4f}", "-ar", str(sample_rate), str(dst)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not dst.is_file():
            print(f"  WARNING: time-stretch failed: {result.stderr.strip()[:200]}", flush=True)
            return audio
        stretched, _ = read_audio(dst)
        return stretched


def apply_fade(audio, sample_rate, fade_ms):
    fade_samples = int(round(max(0.0, fade_ms) * sample_rate / 1000.0))
    fade_samples = min(fade_samples, len(audio) // 2)
    if fade_samples <= 1:
        return audio
    audio = np.asarray(audio, dtype=np.float32).copy()
    audio[:fade_samples] *= np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
    audio[-fade_samples:] *= np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
    return audio


def fade_to_zero(audio, start, end):
    start = max(0, min(len(audio), int(start)))
    end = max(start, min(len(audio), int(end)))
    if end > start:
        audio[start:end] *= np.linspace(1.0, 0.0, end - start, dtype=np.float32)


def fade_from_zero(audio, start, end):
    start = max(0, min(len(audio), int(start)))
    end = max(start, min(len(audio), int(end)))
    if end > start:
        audio[start:end] *= np.linspace(0.0, 1.0, end - start, dtype=np.float32)


def fit_to_duration(audio, sample_rate, target_samples, max_samples, fade_ms=30.0):
    audio = np.asarray(audio, dtype=np.float32)
    if target_samples <= 0:
        return audio[:0]
    if len(audio) > max_samples:
        # Hard cut: fade the tail so the clip does not click.
        audio = audio[:max_samples].copy()
        fade = min(len(audio), int(round(fade_ms * sample_rate / 1000.0)))
        if fade > 1:
            audio[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
        return audio
    if len(audio) >= target_samples:
        return audio
    return np.pad(audio, (0, target_samples - len(audio)))


POST_CLEAN = {
    "soft": {"abs_db": -54.0, "relative": 0.008, "head_keep": 0.04, "tail_keep": 0.24},
    "normal": {"abs_db": -48.0, "relative": 0.015, "head_keep": 0.035, "tail_keep": 0.16},
    "hard": {"abs_db": -42.0, "relative": 0.025, "head_keep": 0.025, "tail_keep": 0.09},
}


def merge_active_regions(active, merge_gap):
    blocks = []
    for start, end in active:
        if blocks and start - blocks[-1][1] <= merge_gap:
            blocks[-1][1] = max(blocks[-1][1], end)
        else:
            blocks.append([start, end])
    return blocks


def clean_segment_audio(audio, sample_rate, args):
    settings = POST_CLEAN.get((args.post_clean or "off").strip().lower())
    if not settings:
        return audio

    audio = np.asarray(audio, dtype=np.float32).copy()
    if not len(audio):
        return audio

    fade_samples = int(round(max(0.0, args.post_fade_ms) * sample_rate / 1000.0))
    frame = max(1, int(round(0.02 * sample_rate)))
    hop = max(1, int(round(0.01 * sample_rate)))
    peak = float(np.max(np.abs(audio)))
    if peak <= 1e-6:
        return audio

    threshold = max(10.0 ** (settings["abs_db"] / 20.0), peak * settings["relative"])
    active = []
    for start in range(0, max(1, len(audio) - frame + 1), hop):
        chunk = audio[start : start + frame]
        rms = float(np.sqrt(np.mean(chunk * chunk))) if len(chunk) else 0.0
        if rms >= threshold:
            active.append((start, min(len(audio), start + frame)))

    if not active:
        return apply_fade(audio, sample_rate, args.post_fade_ms)

    # Merge active frames into blocks, then silence short isolated blobs
    # (breaths, clicks, word fragments) far from real speech.
    blocks = merge_active_regions(active, int(round(0.12 * sample_rate)))
    min_speech = int(round(0.30 * sample_rate))
    attach_gap = int(round(0.30 * sample_rate))
    speech = [b for b in blocks if b[1] - b[0] >= min_speech]
    kept = blocks
    if speech:
        kept = []
        for block in blocks:
            if block[1] - block[0] >= min_speech:
                kept.append(block)
                continue
            for sp in speech:
                if sp[0] >= block[1]:
                    distance = sp[0] - block[1]
                elif block[0] >= sp[1]:
                    distance = block[0] - sp[1]
                else:
                    distance = 0
                if distance <= attach_gap:
                    kept.append(block)
                    break
        kept_ids = {id(block) for block in kept}
        for block in blocks:
            if id(block) in kept_ids:
                continue
            fade_to_zero(audio, max(0, block[0] - fade_samples), block[0])
            audio[block[0]:block[1]] = 0.0
            fade_from_zero(audio, block[1], min(len(audio), block[1] + fade_samples))

    first = max(0, kept[0][0] - int(round(settings["head_keep"] * sample_rate)))
    last = min(len(audio), kept[-1][1] + int(round(settings["tail_keep"] * sample_rate)))
    min_pause_samples = int(round(max(0.0, args.post_min_pause) * sample_rate))

    if first > min_pause_samples:
        audio[:first] = 0.0
        fade_from_zero(audio, first, first + fade_samples)
    if len(audio) - last > min_pause_samples:
        fade_to_zero(audio, max(first, last - fade_samples), last)
        audio[last:] = 0.0

    return apply_fade(audio, sample_rate, args.post_fade_ms)


def mix_segment(track, segment, start_sample):
    end_sample = min(len(track), start_sample + len(segment))
    if end_sample <= start_sample:
        return
    track[start_sample:end_sample] = np.clip(
        track[start_sample:end_sample] + segment[: end_sample - start_sample], -1.0, 1.0
    )


# --------------------------------------------------------------------------
# Synthesis


class Synthesizer:
    """Wraps an engine session: per-item voice/language, resampling, auto-fit."""

    def __init__(self, session, manifest, args, work_dir):
        self.session = session
        self.supports = manifest.get("supports") or {}
        self.args = args
        self.work_dir = work_dir
        self.sample_rate = session.sample_rate or 24000
        self.counter = 0
        self.last_fit = {}

    def voice_for(self, item):
        voice = {}
        ref_audio = self.args.ref_audio or item.get("ref_audio")
        ref_text = self.args.ref_text or item.get("ref_text")
        if ref_audio:
            voice["ref_audio"] = str(Path(ref_audio).resolve())
        if ref_text:
            voice["ref_text"] = ref_text
        if self.args.voice_id:
            voice["voice_id"] = self.args.voice_id
        return voice

    def synthesize(self, item, text, duration, speed):
        self.counter += 1
        out = self.work_dir / f"raw_{self.counter:05d}.wav"
        self.session.synthesize(
            text,
            out,
            language=self.args.language or item.get("language_id") or None,
            voice=self.voice_for(item),
            duration=duration if self.supports.get("duration") else None,
            speed=speed if self.supports.get("speed") else 1.0,
            hints={key: item[key] for key in ("instruct", "emotion") if item.get(key)},
        )
        audio, rate = read_audio(out)
        out.unlink(missing_ok=True)
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        if peak > 0.98:
            audio = audio * (0.98 / peak)  # some engines overshoot; avoid clipping in the mix
        if not self.supports.get("speed") and abs(speed - 1.0) > 0.01:
            audio = time_stretch(audio, rate, speed)
        return resample(audio, rate, self.sample_rate)

    def fit(self, item, text, duration, target_samples, max_samples):
        args = self.args
        audio = self.synthesize(item, text, duration, args.speed)
        natural_seconds = len(audio) / self.sample_rate
        self.last_fit = {"natural_seconds": round(natural_seconds, 3), "speedup": 1.0, "clipped_seconds": 0.0}
        if args.auto_fit and max_samples > 0 and len(audio) > max_samples:
            # Total speed-up (engine speed x time-stretch) never exceeds max_auto_speed.
            budget = args.max_auto_speed / max(args.speed, 0.01)
            first_len = len(audio)
            factor = min(first_len / max_samples, budget)
            if factor > 1.01 and self.supports.get("speed"):
                print(f"  auto-fit: regenerate at speed {args.speed * factor:.2f} "
                      f"(raw {first_len / self.sample_rate:.2f}s, slot {max_samples / self.sample_rate:.2f}s)", flush=True)
                audio = self.synthesize(item, text, duration, args.speed * factor)
                budget /= max(1.0, first_len / max(1, len(audio)))
            factor = min(len(audio) / max_samples, budget)
            if factor > 1.01:
                # Engine has no speed control, or ignored it: stretch the rest.
                print(f"  auto-fit: time-stretch x{factor:.2f} "
                      f"(raw {len(audio) / self.sample_rate:.2f}s, slot {max_samples / self.sample_rate:.2f}s)", flush=True)
                audio = time_stretch(audio, self.sample_rate, factor)
        raw_len = len(audio)
        if raw_len:
            self.last_fit["speedup"] = round(max(1.0, natural_seconds * self.sample_rate / raw_len), 3)
        audio = fit_to_duration(audio, self.sample_rate, target_samples, max_samples)
        clipped = max(0, raw_len - len(audio)) / self.sample_rate
        self.last_fit["clipped_seconds"] = round(clipped, 3)
        if clipped > 0.05:
            print(f"  WARNING: phrase is {clipped:.2f}s too long for its slot and was cut. "
                  f"Shorten the translation or raise Tail room / Max auto speed.", flush=True)
        return audio


def parse_set_options(values):
    settings = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(f"--set expects key=value, got: {value}")
        key, raw = value.split("=", 1)
        try:
            settings[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            settings[key.strip()] = raw
    return settings


def main():
    parser = argparse.ArgumentParser(description="Voice a *.dub.jsonl plan with any TTS engine.")
    parser.add_argument("jsonl", help="*.dub.jsonl from the translation or script stage")
    parser.add_argument("--engine", default="", help="Engine id from engines/ (default: saved setting or omnivoice).")
    parser.add_argument("--engine-settings", default="", help="JSON object with engine settings.")
    parser.add_argument("--set", action="append", default=[], help="Engine setting override key=value (repeatable).")
    parser.add_argument("--device", default="", help="auto, cuda, cuda:1, cpu (default: saved setting or auto).")
    parser.add_argument("--language", default="", help="Override language for every line.")
    parser.add_argument("--ref-audio", default="", help="Override voice reference for every line.")
    parser.add_argument("--ref-text", default="", help="Transcript of --ref-audio.")
    parser.add_argument("--voice-id", default="", help="Voice name for engines with a voice list.")
    parser.add_argument("--output", default="", help="Final WAV path")
    parser.add_argument("--segments-dir", default="", help="Directory for per-segment WAV files")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--tail-room", type=float, default=0.45, help="Extra seconds allowed after each timed segment to avoid clipped phrase endings.")
    parser.add_argument("--auto-fit", action="store_true", help="Speed up overlong segments (engine speed or time-stretch) before clipping.")
    parser.add_argument("--natural-duration", action="store_true", help="Let the engine speak at its own pace and lay segments sequentially, keeping the pauses between them. Recommended for script voiceover.")
    parser.add_argument("--max-auto-speed", type=float, default=1.18, help="Maximum speed-up Auto Fit may use.")
    parser.add_argument("--post-clean", choices=["off", "soft", "normal", "hard"], default="off", help="Clean non-speech noise from generated pauses.")
    parser.add_argument("--post-fade-ms", type=float, default=40.0, help="Short fade used on cleaned segment edges.")
    parser.add_argument("--post-min-pause", type=float, default=0.15, help="Minimum silence kept around cleaned speech, seconds.")
    parser.add_argument("--post-normalize", action="store_true", help="Peak-normalize final WAV after cleanup.")
    parser.add_argument("--keep-segments", action="store_true")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    saved_tts = load_app_settings().get("tts") or {}
    engine_id = args.engine or saved_tts.get("engine") or "omnivoice"
    device = args.device or saved_tts.get("device") or "auto"
    manifest = load_manifest(engine_id)
    overrides = json.loads(args.engine_settings) if args.engine_settings else {}
    settings = resolve_settings(manifest, overrides, parse_set_options(args.set))

    jsonl_path = Path(args.jsonl)
    items = read_jsonl(jsonl_path)
    out_path = Path(args.output) if args.output else default_output_path(jsonl_path)
    segments_dir = Path(args.segments_dir) if args.segments_dir else out_path.with_name(out_path.stem + "_segments")
    segments_dir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Engine: {manifest['name']} | device: {device} | settings: {json.dumps(settings, ensure_ascii=False)}", flush=True)
    with EngineSession(engine_id) as session, tempfile.TemporaryDirectory(prefix="dub_") as tmp:
        session.load(settings, device)
        synth = Synthesizer(session, manifest, args, Path(tmp))
        sample_rate = synth.sample_rate
        if not manifest.get("supports", {}).get("duration") and not args.natural_duration:
            print("Engine has no duration control: phrases are generated naturally and fitted to their slots.", flush=True)

        total_seconds = max(float(item.get("source_end") or 0) for item in items)
        if total_seconds <= 0:
            total_seconds = sum(float(item.get("duration") or 0) for item in items)
        track = np.zeros(max(1, int(math.ceil(total_seconds * sample_rate)) + sample_rate), dtype=np.float32)

        cursor = 0
        prev_source_end = None
        report = []
        for index, item in enumerate(items, start=1):
            seg_id = str(item.get("id") or f"seg_{index:04d}")
            text = str(item["text"]).strip()
            duration = float(item.get("duration") or 0)
            start = float(item.get("source_start") or 0)
            if duration <= 0:
                duration = max(0.25, float(item.get("source_end") or start) - start)
            if not item.get("source_start") and not item.get("source_end"):
                start = cursor / sample_rate

            if args.natural_duration:
                # Keep the intended pause between segments, but let the engine
                # choose its own speech length instead of filling a forced slot.
                gap = max(0.0, start if prev_source_end is None else start - prev_source_end)
                prev_source_end = float(item.get("source_end") or (start + duration))
                print(f"[{index}/{len(items)}] {seg_id}: natural (pause {gap:.2f}s) {text[:80]}", flush=True)
                audio = synth.synthesize(item, text, None, args.speed)
                start = cursor / sample_rate + gap
                synth.last_fit = {"natural_seconds": round(len(audio) / sample_rate, 3), "speedup": 1.0, "clipped_seconds": 0.0}
            else:
                print(f"[{index}/{len(items)}] {seg_id}: {duration:.2f}s {text[:80]}", flush=True)
                target_samples = int(round(duration * sample_rate))
                max_samples = int(round((duration + max(0.0, args.tail_room)) * sample_rate))
                audio = synth.fit(item, text, duration, target_samples, max_samples)
            audio = clean_segment_audio(audio, sample_rate, args)

            sf.write(str(segments_dir / f"{seg_id}.wav"), audio, sample_rate)
            start_sample = int(round(start * sample_rate))
            if start_sample + len(audio) > len(track):
                track = np.pad(track, (0, start_sample + len(audio) - len(track)))
            mix_segment(track, audio, start_sample)
            cursor = max(cursor, start_sample + len(audio))
            report.append({
                "id": seg_id,
                "text": text,
                "start": round(start, 3),
                "slot_seconds": round(duration, 3),
                "seconds": round(len(audio) / sample_rate, 3),
                **synth.last_fit,
            })
            print(f"PROGRESS {index}/{len(items)} Озвучка фраз", flush=True)

    peak = float(np.max(np.abs(track))) if len(track) else 0.0
    if args.post_normalize and peak > 1e-6:
        track = track / peak * 0.89
    elif peak > 0.98:
        track = track / peak * 0.98
    sf.write(str(out_path), track, sample_rate)
    # Per-phrase fit report: the UI uses it to mark phrases that were sped up or cut.
    report_path = out_path.with_name(out_path.stem + ".report.json")
    report_path.write_text(json.dumps({"engine": engine_id, "segments": report}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wav={out_path.resolve()}", flush=True)
    print(f"report={report_path.resolve()}", flush=True)
    print(f"segments_dir={segments_dir.resolve()}", flush=True)

    if not args.keep_segments:
        for path in segments_dir.glob("*.wav"):
            path.unlink()
        try:
            segments_dir.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    try:
        main()
    except EngineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
