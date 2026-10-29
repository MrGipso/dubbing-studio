import argparse
import subprocess
from pathlib import Path


def clamp_music_db(value):
    return max(-30.0, min(0.0, float(value)))


def default_output_path(voice_audio):
    path = Path(voice_audio)
    return path.with_name(path.stem + ".music.wav")


def main():
    parser = argparse.ArgumentParser(description="Loop background music under a generated voice track.")
    parser.add_argument("--voice-audio", required=True, help="Generated dubbing audio track.")
    parser.add_argument("--music-audio", required=True, help="Background music track to loop.")
    parser.add_argument("--output", default="", help="Output WAV path.")
    parser.add_argument("--music-volume-db", type=float, default=-18.0, help="Music gain from -30 dB to 0 dB.")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg executable.")
    args = parser.parse_args()

    voice_path = Path(args.voice_audio)
    music_path = Path(args.music_audio)
    if not voice_path.is_file():
        raise FileNotFoundError(f"Voice audio was not found: {voice_path}")
    if not music_path.is_file():
        raise FileNotFoundError(f"Music audio was not found: {music_path}")

    output_path = Path(args.output) if args.output else default_output_path(voice_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    music_db = clamp_music_db(args.music_volume_db)

    filter_graph = (
        f"[0:a]aformat=sample_fmts=fltp:channel_layouts=stereo[voice];"
        f"[1:a]volume={music_db:.2f}dB,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo[music];"
        f"[voice][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        f"alimiter=limit=0.98[out]"
    )
    command = [
        args.ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(voice_path),
        "-stream_loop",
        "-1",
        "-i",
        str(music_path),
        "-filter_complex",
        filter_graph,
        "-map",
        "[out]",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]

    print(f"voice={voice_path.resolve()}", flush=True)
    print(f"music={music_path.resolve()}", flush=True)
    print(f"music_volume_db={music_db:.2f}", flush=True)
    result = subprocess.run(command)
    if result.returncode:
        raise SystemExit(result.returncode)
    print(f"wav={output_path.resolve()}", flush=True)


if __name__ == "__main__":
    main()
