import argparse
from pathlib import Path


def import_audio_runtime():
    try:
        import numpy as np
        import soundfile as sf
    except ModuleNotFoundError as exc:
        raise RuntimeError(f"Missing audio dependency: {exc.name}") from exc
    return np, sf


def mode_settings(mode):
    mode = (mode or "soft").strip().lower()
    settings = {
        "soft": {"abs_db": -54.0, "relative": 0.008},
        "normal": {"abs_db": -48.0, "relative": 0.015},
        "hard": {"abs_db": -42.0, "relative": 0.025},
    }
    return settings.get(mode, settings["soft"])


def fade_to_zero(np, audio, start, end):
    start = max(0, min(len(audio), int(start)))
    end = max(start, min(len(audio), int(end)))
    if end <= start:
        return
    ramp = np.linspace(1.0, 0.0, end - start, dtype=np.float32)
    audio[start:end] *= ramp[:, None]


def fade_from_zero(np, audio, start, end):
    start = max(0, min(len(audio), int(start)))
    end = max(start, min(len(audio), int(end)))
    if end <= start:
        return
    ramp = np.linspace(0.0, 1.0, end - start, dtype=np.float32)
    audio[start:end] *= ramp[:, None]


def db_to_gain(db):
    return 10.0 ** (db / 20.0)


def biquad_filter(np, audio, sample_rate, kind, freq, q=0.707, gain_db=0.0):
    x = audio.astype(np.float32, copy=False)
    if len(x) < 8:
        return x.copy()
    freqs = np.fft.rfftfreq(len(x), 1.0 / sample_rate)
    safe_freqs = np.maximum(freqs, 1.0)
    cutoff = max(10.0, min(freq, sample_rate * 0.45))
    response = np.ones_like(freqs, dtype=np.float32)

    if kind == "highpass":
        response = (safe_freqs / np.sqrt(safe_freqs * safe_freqs + cutoff * cutoff)).astype(np.float32)
    elif kind == "lowpass":
        response = (cutoff / np.sqrt(safe_freqs * safe_freqs + cutoff * cutoff)).astype(np.float32)
    elif kind == "peaking":
        gain = db_to_gain(gain_db)
        width = max(0.15, 1.0 / max(q, 0.05))
        distance = np.log2(safe_freqs / cutoff)
        bell = np.exp(-0.5 * (distance / width) ** 2)
        response = (1.0 + (gain - 1.0) * bell).astype(np.float32)
    elif kind == "highshelf":
        gain = db_to_gain(gain_db)
        transition = 1.0 / (1.0 + np.exp(-6.0 * np.log2(safe_freqs / cutoff)))
        response = (1.0 + (gain - 1.0) * transition).astype(np.float32)
    else:
        return x.copy()

    spectrum = np.fft.rfft(x, axis=0)
    spectrum *= response[:, None]
    return np.fft.irfft(spectrum, n=len(x), axis=0).astype(np.float32)


def smooth_envelope(np, signal, sample_rate, attack_ms=4.0, release_ms=80.0):
    window = max(1, int(round(sample_rate * max(attack_ms, release_ms * 0.35) / 1000.0)))
    padded = np.pad(signal.astype(np.float32) ** 2, (window, 0), mode="edge")
    cumsum = np.cumsum(padded, dtype=np.float64)
    power = (cumsum[window:] - cumsum[:-window]) / window
    return np.sqrt(np.maximum(power[: len(signal)], 0.0)).astype(np.float32)


def apply_deesser(np, audio, sample_rate, amount, freq, strength):
    high = biquad_filter(np, audio, sample_rate, "highpass", freq, 0.8)
    mono_high = np.mean(np.abs(high), axis=1)
    envelope = smooth_envelope(np, mono_high, sample_rate, 2.0, 55.0)
    threshold = max(0.015, float(np.percentile(envelope, 82)) * 1.15)
    strength = max(0.0, min(1.0, strength / 100.0)) * (0.35 + 0.65 * amount)
    reduction = np.clip((envelope - threshold) / max(threshold, 1e-6), 0.0, 1.0) * strength
    return audio - high * reduction[:, None]


def apply_voice_eq(np, audio, sample_rate, amount, mud_db, clarity_db, air_db, softer=False, radio=False):
    y = biquad_filter(np, audio, sample_rate, "highpass", 70.0 if not radio else 95.0, 0.707)
    scale = 0.35 + 0.65 * amount
    y = biquad_filter(np, y, sample_rate, "peaking", 260.0, 1.0, mud_db * scale)
    clarity = clarity_db * (0.75 if softer else 1.0)
    air = -abs(air_db) if softer else air_db
    y = biquad_filter(np, y, sample_rate, "peaking", 3200.0, 0.9, clarity * scale)
    y = biquad_filter(np, y, sample_rate, "highshelf", 7800.0, 0.7, air * scale)
    if radio:
        y = biquad_filter(np, y, sample_rate, "lowpass", 10500.0, 0.707)
    return y


def apply_compressor(np, audio, sample_rate, amount, threshold_db, ratio, makeup_db, radio=False):
    if radio:
        threshold_db = min(threshold_db, -24.0)
        ratio = max(ratio, 3.8)
        makeup_db = max(makeup_db, 3.5)
    ratio = max(1.0, ratio)
    makeup_db = makeup_db * (0.35 + 0.65 * amount)
    mono = np.mean(audio, axis=1)
    envelope = smooth_envelope(np, mono, sample_rate, 8.0, 130.0)
    env_db = 20.0 * np.log10(np.maximum(envelope, 1e-6))
    over = np.maximum(0.0, env_db - threshold_db)
    reduction_db = over * (1.0 - 1.0 / ratio)
    gain = db_to_gain(-reduction_db + makeup_db)
    return audio * gain[:, None]


def apply_saturation(np, audio, amount, drive, mix):
    drive = 1.0 + (max(0.0, drive) / 100.0) * (0.6 + 1.2 * amount)
    wet = max(0.0, min(1.0, mix / 100.0)) * (0.45 + 0.55 * amount)
    saturated = np.tanh(audio * drive) / np.tanh(drive)
    return audio * (1.0 - wet) + saturated * wet


def apply_room(np, audio, sample_rate, amount, mix):
    delays = [0.018, 0.031, 0.047]
    gains = [0.10, 0.06, 0.035]
    wet = max(0.0, min(1.0, mix / 100.0)) * (0.5 + 0.5 * amount)
    y = audio.copy()
    for delay, gain in zip(delays, gains):
        offset = int(round(delay * sample_rate))
        if offset <= 0 or offset >= len(audio):
            continue
        y[offset:] += audio[:-offset] * gain * wet
    return y


def apply_limiter(np, audio, ceiling=0.95):
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak <= ceiling:
        return audio
    y = np.tanh(audio / ceiling) * ceiling
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > ceiling:
        y = y / peak * ceiling
    return y


def apply_loudness(np, audio, target_db=-16.0):
    rms = float(np.sqrt(np.mean(audio * audio))) if len(audio) else 0.0
    if rms <= 1e-6:
        return audio
    current_db = 20.0 * np.log10(rms)
    gain = db_to_gain(target_db - current_db)
    return audio * min(gain, 8.0)


def preset_flags(args):
    preset = (args.preset or "custom").strip().lower()
    flags = {
        "deesser": args.deesser,
        "eq": args.eq,
        "compressor": args.compressor,
        "saturation": args.saturation,
        "limiter": args.limiter,
        "loudness": args.loudness,
        "room": args.room,
        "softer": False,
        "radio": False,
    }
    if preset == "clean":
        flags.update({"limiter": True, "loudness": args.loudness})
    elif preset == "youtube":
        flags.update({"deesser": True, "eq": True, "compressor": True, "saturation": True, "limiter": True, "loudness": True})
    elif preset == "dense":
        flags.update({"deesser": True, "eq": True, "compressor": True, "saturation": True, "limiter": True, "loudness": True})
    elif preset == "soft":
        flags.update({"deesser": True, "eq": True, "compressor": True, "limiter": True, "loudness": True, "softer": True})
    elif preset == "radio":
        flags.update({"deesser": True, "eq": True, "compressor": True, "saturation": True, "limiter": True, "loudness": True, "radio": True})
    return flags


def master_voice(np, audio, sample_rate, args):
    amount = max(0.0, min(1.0, float(args.amount) / 100.0))
    flags = preset_flags(args)
    y = audio.astype(np.float32, copy=True)
    if flags["deesser"]:
        y = apply_deesser(np, y, sample_rate, amount, args.deesser_freq, args.deesser_strength)
    if flags["eq"]:
        y = apply_voice_eq(np, y, sample_rate, amount, args.eq_mud_db, args.eq_clarity_db, args.eq_air_db, softer=flags["softer"], radio=flags["radio"])
    if flags["compressor"]:
        y = apply_compressor(np, y, sample_rate, amount, args.comp_threshold, args.comp_ratio, args.comp_makeup, radio=flags["radio"])
    if flags["saturation"]:
        y = apply_saturation(np, y, amount, args.sat_drive, args.sat_mix)
    if flags["room"]:
        y = apply_room(np, y, sample_rate, amount, args.room_mix)
    if flags["limiter"]:
        y = apply_limiter(np, y, args.limiter_ceiling)
    if flags["loudness"]:
        y = apply_loudness(np, y, args.loudness_target)
        y = apply_limiter(np, y, args.limiter_ceiling)
    return y


def clean_pauses(np, audio, sample_rate, mode, min_pause, fade_ms):
    if audio.ndim == 1:
        audio = audio[:, None]
    cleaned = audio.astype(np.float32, copy=True)
    mono = np.mean(cleaned, axis=1)
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    if peak <= 1e-6:
        return cleaned

    settings = mode_settings(mode)
    threshold = max(10.0 ** (settings["abs_db"] / 20.0), peak * settings["relative"])
    frame = max(1, int(round(0.02 * sample_rate)))
    hop = max(1, int(round(0.01 * sample_rate)))
    min_samples = int(round(max(0.03, min_pause) * sample_rate))
    fade_samples = int(round(max(0.0, fade_ms) * sample_rate / 1000.0))

    inactive = []
    for start in range(0, max(1, len(mono) - frame + 1), hop):
        chunk = mono[start : start + frame]
        rms = float(np.sqrt(np.mean(chunk * chunk))) if len(chunk) else 0.0
        if rms < threshold:
            inactive.append((start, min(len(mono), start + frame)))

    if not inactive:
        return cleaned

    runs = []
    run_start, run_end = inactive[0]
    for start, end in inactive[1:]:
        if start <= run_end + hop:
            run_end = max(run_end, end)
        else:
            runs.append((run_start, run_end))
            run_start, run_end = start, end
    runs.append((run_start, run_end))

    for start, end in runs:
        if end - start < min_samples:
            continue
        fade_to_zero(np, cleaned, max(0, start - fade_samples), start)
        cleaned[start:end] = 0.0
        fade_from_zero(np, cleaned, end, min(len(cleaned), end + fade_samples))
    return cleaned


def main():
    parser = argparse.ArgumentParser(description="Clean non-speech noise from WAV pauses.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="")
    parser.add_argument("--mode", choices=["soft", "normal", "hard"], default="soft")
    parser.add_argument("--min-pause", type=float, default=0.15)
    parser.add_argument("--fade-ms", type=float, default=40.0)
    parser.add_argument("--normalize", action="store_true")
    parser.add_argument("--preset", choices=["custom", "clean", "youtube", "dense", "soft", "radio"], default="custom")
    parser.add_argument("--amount", type=float, default=55.0)
    parser.add_argument("--deesser", action="store_true")
    parser.add_argument("--eq", action="store_true")
    parser.add_argument("--compressor", action="store_true")
    parser.add_argument("--saturation", action="store_true")
    parser.add_argument("--limiter", action="store_true")
    parser.add_argument("--loudness", action="store_true")
    parser.add_argument("--room", action="store_true")
    parser.add_argument("--deesser-freq", type=float, default=5200.0)
    parser.add_argument("--deesser-strength", type=float, default=55.0)
    parser.add_argument("--eq-mud-db", type=float, default=-1.8)
    parser.add_argument("--eq-clarity-db", type=float, default=2.2)
    parser.add_argument("--eq-air-db", type=float, default=1.1)
    parser.add_argument("--comp-threshold", type=float, default=-20.0)
    parser.add_argument("--comp-ratio", type=float, default=3.0)
    parser.add_argument("--comp-makeup", type=float, default=2.2)
    parser.add_argument("--sat-drive", type=float, default=45.0)
    parser.add_argument("--sat-mix", type=float, default=28.0)
    parser.add_argument("--limiter-ceiling", type=float, default=0.95)
    parser.add_argument("--loudness-target", type=float, default=-16.0)
    parser.add_argument("--room-mix", type=float, default=10.0)
    args = parser.parse_args()

    np, sf = import_audio_runtime()
    input_path = Path(args.input)
    if not input_path.is_file():
        raise RuntimeError(f"WAV file was not found: {input_path}")

    output_path = Path(args.output) if args.output else input_path.with_name(input_path.stem + ".clean.wav")
    audio, sample_rate = sf.read(input_path, dtype="float32", always_2d=True)
    cleaned = clean_pauses(np, audio, sample_rate, args.mode, args.min_pause, args.fade_ms)
    cleaned = master_voice(np, cleaned, sample_rate, args)
    peak = float(np.max(np.abs(cleaned))) if len(cleaned) else 0.0
    if args.normalize and peak > 1e-6:
        cleaned = cleaned / peak * 0.89
    elif peak > 0.98:
        cleaned = cleaned / peak * 0.98
    sf.write(output_path, cleaned, sample_rate)
    print(f"wav={output_path.resolve()}", flush=True)


if __name__ == "__main__":
    main()
