import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def resolve_yt_dlp():
    candidates = []
    env_path = os.environ.get("YT_DLP")
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend(
        [
            ROOT / "tools" / "yt-dlp.exe",
            ROOT / "yt-dlp.exe",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return [str(candidate)]

    found = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if found:
        return [found]

    if importlib.util.find_spec("yt_dlp"):
        return [sys.executable, "-m", "yt_dlp"]

    raise RuntimeError(
        "yt-dlp was not found. Install it with: python -m pip install -U yt-dlp, "
        "or put yt-dlp.exe into the project tools folder."
    )


def safe_prefix(value):
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "_", str(value or "")).strip(" ._")
    return cleaned[:180]


def output_template(prefix):
    cleaned = safe_prefix(prefix)
    if cleaned:
        return f"{cleaned}.%(ext)s"
    return "%(upload_date>%Y%m%d)s_%(title).180B_%(id)s.%(ext)s"


def format_for_quality(quality):
    if quality == "best":
        return "bv*+ba/b"
    if quality in {"2160", "1440", "1080", "720", "480"}:
        return f"bv*[height<={quality}]+ba/b[height<={quality}]/b"
    return "bv*+ba/b"


def run_command(command):
    print("> " + " ".join(command), flush=True)
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line.rstrip("\n"), flush=True)
    code = process.wait()
    if code != 0:
        raise subprocess.CalledProcessError(code, command)


def common_args(args, template):
    command = [
        *resolve_yt_dlp(),
        "--newline",
        "--no-playlist",
        "--windows-filenames",
        "-P",
        str(args.output_dir),
        "-o",
        template,
    ]
    if args.cookies_from_browser != "none":
        command.extend(["--cookies-from-browser", args.cookies_from_browser])
    return command


def download(args):
    args.output_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    template = output_template(args.output_prefix)

    if args.mode in {"video", "video_audio"}:
        command = common_args(args, template)
        command.extend(
            [
                "-f",
                format_for_quality(args.quality),
                "--merge-output-format",
                "mp4",
                args.url,
            ]
        )
        run_command(command)

    if args.mode in {"audio", "video_audio"}:
        command = common_args(args, template)
        command.extend(
            [
                "-x",
                "--audio-format",
                "wav",
                "--audio-quality",
                "0",
                args.url,
            ]
        )
        run_command(command)

    if args.mode == "subtitles":
        command = common_args(args, template)
        command.extend(
            [
                "--write-subs",
                "--write-auto-subs",
                "--sub-langs",
                args.subtitle_languages,
                "--sub-format",
                "srt/vtt/best",
                "--skip-download",
                args.url,
            ]
        )
        run_command(command)

    new_files = [
        path
        for path in sorted(args.output_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True)
        if path.is_file() and path.stat().st_mtime >= started_at - 1
    ]
    if new_files:
        for path in new_files:
            print(f"downloaded={path}", flush=True)
    else:
        print("No new files detected in the output folder.", flush=True)


def check():
    command = [*resolve_yt_dlp(), "--version"]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
    print(f"yt-dlp OK: {(result.stdout or '').strip()}", flush=True)


def update():
    command = [*resolve_yt_dlp(), "-U"]
    run_command(command)


def subtitle_command(args):
    command = [
        *resolve_yt_dlp(),
        "--no-playlist",
        "--no-warnings",
        "--skip-download",
        "--dump-single-json",
    ]
    if args.cookies_from_browser != "none":
        command.extend(["--cookies-from-browser", args.cookies_from_browser])
    command.append(args.url)
    return command


def normalize_subtitles(kind, subtitles):
    items = []
    for code, variants in sorted((subtitles or {}).items(), key=lambda pair: pair[0].lower()):
        if not isinstance(variants, list):
            continue
        formats = sorted({str(item.get("ext") or "").strip() for item in variants if item.get("ext")})
        name = ""
        for item in variants:
            name = str(item.get("name") or "").strip()
            if name:
                break
        items.append(
            {
                "code": code,
                "name": name or code,
                "type": kind,
                "formats": formats,
            }
        )
    return items


def list_subtitles(args):
    if not args.url:
        raise ValueError("--url is required for subtitle detection.")
    command = subtitle_command(args)
    result = subprocess.run(
        command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "yt-dlp subtitle detection failed.").strip()
        raise RuntimeError(message)

    data = json.loads(result.stdout)
    manual = normalize_subtitles("manual", data.get("subtitles") or {})
    automatic = normalize_subtitles("automatic", data.get("automatic_captions") or {})
    print(
        json.dumps(
            {
                "title": data.get("title") or "",
                "id": data.get("id") or "",
                "manual": manual,
                "automatic": automatic,
                "all": manual + automatic,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def main():
    configure_stdio()
    parser = argparse.ArgumentParser(description="Download user-owned YouTube media through yt-dlp.")
    parser.add_argument("--url", default="")
    parser.add_argument("--mode", choices=["video", "audio", "video_audio", "subtitles"], default="video")
    parser.add_argument("--quality", default="best")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "inputs" / "youtube")
    parser.add_argument("--output-prefix", default="")
    parser.add_argument("--subtitle-languages", default="ru,en,bg,es,pt")
    parser.add_argument("--cookies-from-browser", choices=["none", "chrome", "edge", "firefox", "opera", "brave"], default="none")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--list-subs", action="store_true")
    args = parser.parse_args()

    try:
        if args.check:
            check()
            return
        if args.update:
            update()
            return
        if args.list_subs:
            list_subtitles(args)
            return
        if not args.url:
            raise ValueError("--url is required for downloading.")
        download(args)
    except Exception as exc:
        print(str(exc), file=sys.stderr, flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
