import argparse
import json
import os
import sys
from pathlib import Path


def add_cuda_libraries():
    """cuBLAS from the nvidia-cublas-cu12 wheel (the desktop app installs it on NVIDIA machines).

    CTranslate2 ships cuDNN itself but expects cuBLAS from a CUDA Toolkit;
    most people do not have one installed.
    """
    try:
        import nvidia.cublas
    except ImportError:
        return
    for base in getattr(nvidia.cublas, "__path__", []):
        for folder in (Path(base) / "bin", Path(base) / "lib"):
            if folder.is_dir():
                os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
                if hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(str(folder))
                elif sys.platform.startswith("linux"):
                    import ctypes

                    for library in sorted(folder.glob("libcublas*.so*")):
                        ctypes.CDLL(str(library), mode=ctypes.RTLD_GLOBAL)


add_cuda_libraries()

from faster_whisper import WhisperModel  # noqa: E402


def format_srt_time(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours = millis // 3_600_000
    millis %= 3_600_000
    minutes = millis // 60_000
    millis %= 60_000
    secs = millis // 1000
    millis %= 1000
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def main():
    parser = argparse.ArgumentParser(description='Transcribe audio with faster-whisper.')
    parser.add_argument('audio', help='Path to audio file')
    parser.add_argument('--model', default='medium', help='tiny, base, small, medium, large-v3, etc.')
    parser.add_argument('--language', default='auto', help="Language code or 'auto' for detection")
    parser.add_argument('--device', default='cuda')
    parser.add_argument('--compute-type', default='float16')
    parser.add_argument('--output-dir', default='outputs')
    parser.add_argument('--output-prefix', default='', help='Output filename prefix. Defaults to input audio stem.')
    parser.add_argument('--word-timestamps', action='store_true', help='Save word timings (used to split phrases between speakers).')
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    audio_path = Path(args.audio)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    language = None if args.language.lower() in ('auto', '') else args.language

    def start(device, compute_type):
        if device == 'cpu' and 'float16' in compute_type:
            compute_type = 'int8'  # float16 is not supported on CPU
        model = WhisperModel(args.model, device=device, compute_type=compute_type)
        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            vad_filter=True,
            beam_size=5,
            word_timestamps=args.word_timestamps,
        )
        first = next(segments, None)  # the GPU is really used here: fail early, not mid-file

        def all_segments():
            if first is not None:
                yield first
            yield from segments

        return all_segments(), info

    try:
        segments, info = start(args.device, args.compute_type)
    except (RuntimeError, ValueError, OSError) as exc:
        if args.device == 'cpu':
            raise
        print(f"WARNING: Whisper could not use the GPU ({exc}); recognizing on the CPU, it is slower.", flush=True)
        segments, info = start('cpu', 'int8')

    stem = args.output_prefix.strip() or audio_path.stem
    txt_path = out_dir / f'{stem}.txt'
    srt_path = out_dir / f'{stem}.srt'
    json_path = out_dir / f'{stem}.json'

    lines = []
    srt_blocks = []
    records = []
    total = max(1, int(info.duration))
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        lines.append(text)
        srt_blocks.append(
            f"{len(srt_blocks) + 1}\n{format_srt_time(seg.start)} --> {format_srt_time(seg.end)}\n{text}\n"
        )
        record = {
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": text,
            "avg_logprob": round(seg.avg_logprob, 4),
            "no_speech_prob": round(seg.no_speech_prob, 4),
        }
        if seg.words:
            record["words"] = [
                {"start": round(word.start, 3), "end": round(word.end, 3), "word": word.word}
                for word in seg.words
            ]
        records.append(record)
        print(f"[{seg.start:7.2f} - {seg.end:7.2f}] {text}", flush=True)
        print(f"PROGRESS {min(total, int(seg.end))}/{total} Распознавание речи", flush=True)

    txt_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    srt_path.write_text('\n'.join(srt_blocks), encoding='utf-8')
    json_path.write_text(json.dumps({
        "language": info.language,
        "language_probability": round(info.language_probability, 4),
        "duration": round(info.duration, 3),
        "segments": records,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    print(f"language={info.language}, probability={info.language_probability:.3f}")
    print(f"txt={txt_path.resolve()}")
    print(f"srt={srt_path.resolve()}")
    print(f"json={json_path.resolve()}")


if __name__ == '__main__':
    main()
