import argparse
import sys
from pathlib import Path
from faster_whisper import WhisperModel


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
    parser.add_argument('--language', default='ru', help="Language code or 'auto' for detection")
    parser.add_argument('--device', default='cuda')
    parser.add_argument('--compute-type', default='float16')
    parser.add_argument('--output-dir', default='outputs')
    parser.add_argument('--output-prefix', default='', help='Output filename prefix. Defaults to input audio stem.')
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    audio_path = Path(args.audio)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    language = None if args.language.lower() in ('auto', '') else args.language
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=True,
        beam_size=5,
    )

    stem = args.output_prefix.strip() or audio_path.stem
    txt_path = out_dir / f'{stem}.txt'
    srt_path = out_dir / f'{stem}.srt'

    lines = []
    srt_blocks = []
    for i, seg in enumerate(segments, start=1):
        text = seg.text.strip()
        if not text:
            continue
        lines.append(text)
        srt_blocks.append(
            f"{i}\n{format_srt_time(seg.start)} --> {format_srt_time(seg.end)}\n{text}\n"
        )
        print(f"[{seg.start:7.2f} - {seg.end:7.2f}] {text}")

    txt_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    srt_path.write_text('\n'.join(srt_blocks), encoding='utf-8')

    print(f"language={info.language}, probability={info.language_probability:.3f}")
    print(f"txt={txt_path.resolve()}")
    print(f"srt={srt_path.resolve()}")


if __name__ == '__main__':
    main()
