"""Prepare the desktop build: a clean copy of the Python app and the uv binary.

  uv run --no-project python desktop/prepare.py

Tauri runs it before `cargo tauri build` / `cargo tauri dev`. It copies the app
(without environments, projects, outputs, caches or local settings) into
desktop/src-tauri/app/, uv into desktop/src-tauri/binaries/ and a ready Python
into desktop/src-tauri/python/, where tauri.conf.json picks them up. These
folders are git-ignored.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAURI = Path(__file__).resolve().parent / "src-tauri"
APP = TAURI / "app"

# What the app needs at runtime, relative to the repository root.
FILES = ["pyproject.toml", "uv.lock", "README.md"]
FOLDERS = ["ui", "locales", "engines"]
CONFIG_FILES = ["config/llm_providers.json"]
IGNORE = shutil.ignore_patterns(
    ".venv", "models", "__pycache__", "*.pyc", "engine.local.json", "*.local.json", ".pytest_cache",
)


def copy_app():
    if APP.exists():
        shutil.rmtree(APP)
    APP.mkdir(parents=True)
    for script in sorted(ROOT.glob("*.py")):
        shutil.copy2(script, APP / script.name)
    for name in FILES:
        shutil.copy2(ROOT / name, APP / name)
    for name in FOLDERS:
        shutil.copytree(ROOT / name, APP / name, ignore=IGNORE)
    for name in CONFIG_FILES:
        (APP / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, APP / name)
    count = sum(1 for path in APP.rglob("*") if path.is_file())
    print(f"app: {count} files -> {APP}")


def host_triple():
    try:
        output = subprocess.run(["rustc", "-vV"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return "x86_64-pc-windows-msvc" if sys.platform == "win32" else ""
    return next(line.split(":", 1)[1].strip() for line in output.splitlines() if line.startswith("host:"))


def copy_uv():
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv was not found in PATH: https://docs.astral.sh/uv/")
    suffix = ".exe" if sys.platform == "win32" else ""
    target = TAURI / "binaries" / f"uv-{host_triple()}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(uv, target)
    version = subprocess.run([uv, "--version"], capture_output=True, text=True).stdout.strip()
    print(f"uv: {version} -> {target}")


PYTHON_VERSION = "3.12"


def copy_python():
    """A ready Python for the installer (python-build-standalone, fetched by uv).

    The app creates its environments from it, so uv never installs Python on
    the user's computer. That also avoids a uv failure seen when it installs
    Python inside some Windows profile folders.
    """
    stage = TAURI / ".python-stage"
    target = TAURI / "python"
    env = {**os.environ, "UV_PYTHON_INSTALL_DIR": str(stage),
           "UV_PYTHON_INSTALL_REGISTRY": "0", "UV_PYTHON_INSTALL_BIN": "0"}
    subprocess.run([shutil.which("uv"), "python", "install", PYTHON_VERSION, "--no-bin", "--no-registry"],
                   env=env, check=True)
    builds = sorted(path for path in stage.iterdir()
                    if path.is_dir() and path.name.startswith(f"cpython-{PYTHON_VERSION}.") and not path.is_symlink())
    if not builds:
        raise SystemExit(f"uv did not install Python {PYTHON_VERSION} into {stage}")
    build = builds[-1]
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(build, target, ignore=shutil.ignore_patterns("__pycache__", "test", "tests", "idle_test"))
    (target / ".version").write_text(build.name + "\n", encoding="utf-8")
    print(f"python: {build.name} -> {target}")


if __name__ == "__main__":
    copy_app()
    copy_uv()
    copy_python()
