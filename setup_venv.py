#!/usr/bin/env python3
"""
setup_venv.py -- create the virtual environment with the right Python.

Run it with whatever Python you have; it picks the interpreter for the venv
itself:

    Windows:        py setup_venv.py          (or: python setup_venv.py)
    macOS / Linux:  python3 setup_venv.py

Options:
    --build         also install the packaging tools (requirements-build.txt)
    --recreate      delete and rebuild an existing venv
    --python EXE    use this interpreter instead of picking one
    --venv PATH     put the venv here instead of the default location

What it guards against (all things that bit us on Windows):
  * `python` / `python3` / `py` pointing at different installs -- it lists
    every install via the `py` launcher and picks one explicitly;
  * the Microsoft Store Python, which PyInstaller doesn't handle well;
  * 32-bit Python, which has no PySide6 wheels;
  * the 260-character path limit: PySide6 contains very deep file paths,
    so if Windows long-path support is off and the repo sits in a deep
    folder, the venv goes to a short path (~/.venvs/<repo name>) instead.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

REPO_ROOT = Path(__file__).resolve().parent
MIN_VERSION = (3, 10)
# Newest version the app has been tested on. Newer ones are only picked if
# nothing in the tested range is installed (their wheels may lag behind).
MAX_TESTED_VERSION = (3, 13)
# Longest path PySide6 creates under a venv (Lib/site-packages/PySide6/qml/...)
DEEPEST_VENV_SUBPATH = 170
WINDOWS_MAX_PATH = 259
IS_WINDOWS = sys.platform == "win32"


def fail(msg: str) -> NoReturn:
    print(f"\nERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def is_store_python(path: str) -> bool:
    return "WindowsApps" in path or "PythonSoftwareFoundation.Python" in path


def probe(exe: str) -> tuple[tuple[int, int], int, str] | None:
    """(version, bits, real executable path) of an interpreter, or None."""
    code = ("import sys, struct; "
            "print(sys.version_info[0], sys.version_info[1], struct.calcsize('P') * 8, "
            "getattr(sys, '_base_executable', sys.executable))")
    try:
        out = subprocess.run([exe, "-c", code], capture_output=True, text=True,
                             timeout=30, check=True).stdout.split(maxsplit=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return (int(out[0]), int(out[1])), int(out[2]), out[3].strip()


def windows_candidates() -> list[str]:
    """Every interpreter the `py` launcher knows about."""
    if shutil.which("py") is None:
        return [sys.executable]
    try:
        listing = subprocess.run(["py", "-0p"], capture_output=True, text=True,
                                 timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return [sys.executable]
    paths = []
    for line in listing.splitlines():
        # " -V:3.12 *        C:\...\python.exe"  or  " -3.12-64   C:\...\python.exe"
        m = re.match(r"^\s*-(?:V:)?\S+\s+(?:\*\s+)?(.+\.exe)\s*$", line)
        if m:
            paths.append(m.group(1))
    return paths or [sys.executable]


def pick_python(explicit: str | None) -> str:
    candidates = [explicit] if explicit else (
        windows_candidates() if IS_WINDOWS else [sys.executable])
    usable, rejected = [], []
    for exe in candidates:
        info = probe(exe)
        if info is None:
            rejected.append(f"{exe}: could not run it")
            continue
        version, bits, real = info
        v = f"{version[0]}.{version[1]}"
        if version < MIN_VERSION:
            rejected.append(f"{real}: Python {v} is too old (need 3.10+)")
        elif bits != 64:
            rejected.append(f"{real}: 32-bit Python {v} (PySide6 needs 64-bit)")
        elif IS_WINDOWS and is_store_python(real):
            rejected.append(f"{real}: Microsoft Store Python {v} (not supported for building)")
        else:
            usable.append((version, exe, real))

    for line in rejected:
        print(f"  skipped  {line}")
    if not usable:
        hint = ("Install 64-bit Python 3.12 from https://www.python.org/downloads/ "
                "(tick 'Add python.exe to PATH' and keep the 'py launcher' option), "
                "then run this script again." if IS_WINDOWS else
                "Install Python 3.10+ and run this script with it.")
        fail(f"No suitable Python found. {hint}")

    tested = [u for u in usable if u[0] <= MAX_TESTED_VERSION]
    version, exe, real = max(tested or usable)
    print(f"  using    {real}  (Python {version[0]}.{version[1]})")
    if not tested:
        print(f"  note     newer than the tested {MAX_TESTED_VERSION[0]}.{MAX_TESTED_VERSION[1]}; "
              f"if installing packages fails, install Python 3.12 and rerun.")
    return exe


def long_paths_enabled() -> bool:
    if not IS_WINDOWS:
        return True
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


def default_venv_path() -> Path:
    in_repo = REPO_ROOT / ".venv"
    if long_paths_enabled() or len(str(in_repo)) + DEEPEST_VENV_SUBPATH <= WINDOWS_MAX_PATH:
        return in_repo
    short = Path.home() / ".venvs" / REPO_ROOT.name
    print(f"  note     the repo folder is too deep for PySide6's long file paths and "
          f"Windows long-path support is off,\n           so the venv goes to {short}")
    return short


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def check_existing(venv: Path, recreate: bool) -> bool:
    """True if an existing venv can be reused as-is."""
    if not venv.exists():
        return False
    if recreate:
        print(f"  removing {venv}")
        shutil.rmtree(venv)
        return False
    py = venv_python(venv)
    cfg = venv / "pyvenv.cfg"
    cfg_text = cfg.read_text(encoding="utf-8", errors="replace") if cfg.exists() else ""
    info = probe(str(py)) if py.exists() else None
    problem = None
    if info is None:
        problem = "it is broken or not a venv"
    elif info[0] < MIN_VERSION:
        problem = f"it uses Python {info[0][0]}.{info[0][1]}"
    elif IS_WINDOWS and is_store_python(cfg_text):
        problem = "it was created with the Microsoft Store Python"
    if problem:
        fail(f"{venv} already exists, but {problem}.\n"
             f"       Rerun with --recreate to rebuild it.")
    print(f"  reusing  {venv}")
    return True


def run(cmd: list[str]) -> None:
    print("  $", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    if subprocess.run(cmd).returncode != 0:
        fail("command failed (see output above).")


def main() -> int:
    # keep our messages in order with the output of the commands we run
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--build", action="store_true", help="also install packaging tools")
    ap.add_argument("--recreate", action="store_true", help="rebuild an existing venv")
    ap.add_argument("--python", help="interpreter to create the venv with")
    ap.add_argument("--venv", type=Path, help="where to create the venv")
    args = ap.parse_args()

    print("Choosing Python:")
    base_python = pick_python(args.python)

    venv = (args.venv or default_venv_path()).expanduser().resolve()
    if IS_WINDOWS and not long_paths_enabled() and \
            len(str(venv)) + DEEPEST_VENV_SUBPATH > WINDOWS_MAX_PATH:
        fail(f"{venv} is too long a path for PySide6 while Windows long-path support is off. "
             f"Pick a shorter --venv path, or enable long paths: "
             f"https://pip.pypa.io/warnings/enable-long-paths")

    print("\nVirtual environment:")
    if not check_existing(venv, args.recreate):
        venv.parent.mkdir(parents=True, exist_ok=True)
        run([base_python, "-m", "venv", str(venv)])

    py = str(venv_python(venv))
    print("\nInstalling packages:")
    run([py, "-m", "pip", "install", "--upgrade", "pip"])
    reqs = ["-r", str(REPO_ROOT / "requirements.txt")]
    if args.build:
        reqs += ["-r", str(REPO_ROOT / "requirements-build.txt")]
    run([py, "-m", "pip", "install", *reqs])

    print("\nChecking imports:")
    run([py, "-c", "import PySide6.QtWidgets, cv2, pandas, numpy, openpyxl; print('  ok')"])

    if IS_WINDOWS:
        activate = (f"  PowerShell:  & \"{venv / 'Scripts' / 'Activate.ps1'}\"\n"
                    f"  cmd.exe:     \"{venv / 'Scripts' / 'activate.bat'}\"")
    else:
        activate = f"  source \"{venv / 'bin' / 'activate'}\""
    print(f"\nDone. Activate the venv with:\n{activate}\n"
          f"then run the app with:\n  python main.py")
    if args.build:
        print("or build the standalone app with:\n  python packaging/build.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
