#!/usr/bin/env python3
"""
packaging/build.py

Builds a standalone desktop app (.app on macOS, .exe on Windows, plain
executable on Linux) using PyInstaller. Run from the repo root:

    python3 -m pip install -r requirements.txt -r requirements-build.txt
    python3 packaging/build.py

Output lands in dist/Surgical Annotation Studio/ (onedir build -- faster
startup than a single-file .exe, and easier to inspect/debug).

If resources/ffmpeg/<mac|windows>/ contains ffmpeg(.exe)/ffprobe(.exe),
they're bundled into the app so end users don't need to install ffmpeg
themselves. See packaging/README.md for where to get those binaries. If
that folder is empty/missing, the build still succeeds -- the packaged
app will just require ffmpeg to be installed separately on the machine
that runs it (io_utils/ffmpeg_utils.py falls back to PATH automatically).
"""
from __future__ import annotations
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "Surgical Annotation Studio"


def _platform_dir_name() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "mac"
    if system == "windows":
        return "windows"
    return "linux"


def main() -> int:
    if shutil.which("pyinstaller") is None:
        print("PyInstaller not found. Install it with:\n"
              "  pip install -r requirements-build.txt", file=sys.stderr)
        return 1

    plat_dir = _platform_dir_name()
    ffmpeg_dir = REPO_ROOT / "resources" / "ffmpeg" / plat_dir
    has_bundled_ffmpeg = ffmpeg_dir.exists() and any(ffmpeg_dir.iterdir())

    sep = ";" if platform.system().lower() == "windows" else ":"

    cmd = [
        "pyinstaller",
        "--name", APP_NAME,
        "--windowed",         # no console window
        "--noconfirm",        # overwrite previous build without prompting
        "--clean",
    ]

    if has_bundled_ffmpeg:
        # Bundle the ffmpeg/ffprobe binaries so end users don't need to
        # install ffmpeg themselves; unpacked at
        # resources/ffmpeg/<platform>/ next to the frozen executable.
        cmd += ["--add-data", f"{ffmpeg_dir}{sep}resources/ffmpeg/{plat_dir}"]
        print(f"Bundling ffmpeg binaries from {ffmpeg_dir}")
    else:
        print(f"No bundled ffmpeg found at {ffmpeg_dir} -- the built app will "
              f"require ffmpeg to be installed separately on the target machine.")

    icon_path = REPO_ROOT / "packaging" / plat_dir / (
        "icon.icns" if plat_dir == "mac" else "icon.ico"
    )
    if icon_path.exists():
        cmd += ["--icon", str(icon_path)]

    cmd += [str(REPO_ROOT / "main.py")]

    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        return result.returncode

    print(f"\nBuild complete. Output in: {REPO_ROOT / 'dist' / APP_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
