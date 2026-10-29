"""Helper tools the app sets up by itself.

  python tools_setup.py ffmpeg     put ffmpeg and ffprobe into the data folder (Windows),
                                   unless they are already in PATH

The ffmpeg build is BtbN's LGPL build from GitHub: the app only cuts, mixes
and muxes (the video stream is copied, never re-encoded), so it needs no GPL
encoders.
"""

import argparse
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import paths
from i18n import tr


FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip"


def ffmpeg_available():
    paths.add_tools_to_path()
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def download(url, destination, label):
    with urllib.request.urlopen(url, timeout=60) as response, open(destination, "wb") as output:  # noqa: S310 - fixed URL
        total = int(response.headers.get("Content-Length") or 0)
        done, shown = 0, -1
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            output.write(chunk)
            done += len(chunk)
            if total and done * 100 // total != shown:
                shown = done * 100 // total
                print(f"PROGRESS {shown}/100 {label}", flush=True)


def install_ffmpeg(force=False):
    if not force and ffmpeg_available():
        print(tr("ffmpeg уже есть: {path}", path=shutil.which("ffmpeg")), flush=True)
        return
    if os.name != "nt":
        raise SystemExit(tr("Установите ffmpeg менеджером пакетов: brew install ffmpeg (macOS) или sudo apt install ffmpeg (Linux)."))
    target = paths.ffmpeg_bin_dir()
    target.mkdir(parents=True, exist_ok=True)
    print(tr("Скачиваю ffmpeg (около 180 МБ): {url}", url=FFMPEG_URL), flush=True)
    with tempfile.TemporaryDirectory(dir=paths.TOOLS_DIR) as tmp:
        archive = Path(tmp) / "ffmpeg.zip"
        download(FFMPEG_URL, archive, tr("Скачиваю ffmpeg"))
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.namelist():
                name = Path(member).name
                if "/bin/" in member and name.lower().endswith((".exe", ".dll")):
                    with bundle.open(member) as source, open(target / name, "wb") as output:
                        shutil.copyfileobj(source, output)
    if not ffmpeg_available():
        raise SystemExit(f"ffmpeg was downloaded to {target}, but it cannot be started.")
    print(tr("ffmpeg установлен: {path}", path=target), flush=True)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Set up helper tools (ffmpeg).")
    parser.add_argument("tool", choices=["ffmpeg"])
    parser.add_argument("--force", action="store_true", help="Download even if the tool is already in PATH.")
    args = parser.parse_args()
    install_ffmpeg(force=args.force)


if __name__ == "__main__":
    main()
