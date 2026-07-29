"""
io_utils/ffmpeg_utils.py

Thin, dependency-free wrappers around the ffmpeg/ffprobe CLI. Kept as
plain functions returning (success, message) so a QThread worker can call
them without importing any Qt types here.

Binary resolution order (see `_find_binary`):
  1. A bundled copy under `resources/ffmpeg/<platform>/` next to the app
     (or inside the PyInstaller bundle) -- this is what lets a packaged
     .app/.exe work without the user installing ffmpeg separately. See
     `packaging/README.md` for how to add these binaries before building.
  2. Whatever `ffmpeg`/`ffprobe` is found on the system PATH -- this is
     the normal path when running from source with
     `sudo apt install ffmpeg` / `brew install ffmpeg` / etc.
  3. A handful of common install locations, checked directly even if not
     on PATH. This matters because a macOS app launched by double-clicking
     (Finder/Dock/Launchpad) gets a bare launchd PATH, NOT the shell's PATH
     -- so Homebrew's /opt/homebrew/bin or /usr/local/bin (added to PATH
     by .zshrc/.bash_profile) are invisible to it even though `ffmpeg`
     works fine from Terminal.
"""
from __future__ import annotations
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable


class FFmpegNotFound(RuntimeError):
    pass


def _app_root() -> Path:
    """Directory the running app is based in -- the PyInstaller bundle
    directory when frozen, otherwise the repo root (two levels up from
    this file: io_utils/ffmpeg_utils.py -> repo root)."""
    if getattr(sys, "frozen", False):
        # PyInstaller: sys._MEIPASS for onefile extraction dir, else the
        # directory containing the executable for onedir builds.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _platform_dir_name() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "mac"
    if system == "windows":
        return "windows"
    return "linux"


def _bundled_binary(name: str) -> Optional[Path]:
    """Look for a bundled ffmpeg/ffprobe under
    resources/ffmpeg/<platform>/<name>[.exe]."""
    exe_name = f"{name}.exe" if platform.system().lower() == "windows" else name
    candidate = _app_root() / "resources" / "ffmpeg" / _platform_dir_name() / exe_name
    if candidate.exists():
        return candidate
    return None


def _common_install_dirs() -> list[str]:
    system = platform.system().lower()
    if system == "darwin":
        return ["/opt/homebrew/bin", "/usr/local/bin"]
    if system == "windows":
        return []  # Windows GUI apps do inherit the system/user PATH normally
    return ["/usr/local/bin", "/usr/bin", "/snap/bin"]


def _find_binary(name: str) -> Optional[str]:
    bundled = _bundled_binary(name)
    if bundled is not None:
        return str(bundled)
    on_path = shutil.which(name)
    if on_path:
        return on_path
    exe_name = f"{name}.exe" if platform.system().lower() == "windows" else name
    for d in _common_install_dirs():
        candidate = Path(d) / exe_name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def install_hint() -> str:
    """One-line, platform-appropriate instructions for getting ffmpeg
    installed -- used both in the raised exception and in the GUI's
    warning banner, so the two never drift out of sync or show the wrong
    OS's instructions."""
    system = platform.system().lower()
    if system == "darwin":
        return (
            "Install with 'brew install ffmpeg' (Homebrew: https://brew.sh). "
            "Already installed but still seeing this in a packaged app? Launch it from "
            "Terminal (`open \"Surgical Annotation Studio.app\"`) instead of double-clicking -- "
            "apps opened from Finder/Dock don't inherit Homebrew's PATH."
        )
    if system == "windows":
        return ("Download a build from https://www.gyan.dev/ffmpeg/builds/ "
                 "(grab 'release essentials', unzip, and add its bin/ folder to PATH).")
    return "Install with 'sudo apt install ffmpeg' (or your distro's equivalent)."


def ffmpeg_available() -> bool:
    return _find_binary("ffmpeg") is not None and _find_binary("ffprobe") is not None


def _require_ffmpeg() -> tuple[str, str]:
    ffmpeg_bin = _find_binary("ffmpeg")
    ffprobe_bin = _find_binary("ffprobe")
    if not ffmpeg_bin or not ffprobe_bin:
        raise FFmpegNotFound(
            f"ffmpeg/ffprobe not found. {install_hint()} "
            "If this is a packaged app, see packaging/README.md to bundle ffmpeg instead."
        )
    return ffmpeg_bin, ffprobe_bin


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    fps: float
    duration_sec: float
    nframes: int
    codec: str


def probe_video(path: Path) -> VideoInfo:
    _, ffprobe_bin = _require_ffmpeg()
    cmd = [
        ffprobe_bin, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate,"
                          "nb_frames,duration,codec_name",
        "-show_entries", "format=duration",
        "-of", "json", str(path),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    stream = data["streams"][0]
    fmt = data.get("format", {})

    def _parse_rate(r: str) -> float:
        if "/" in r:
            num, den = r.split("/")
            den = float(den)
            return float(num) / den if den else 0.0
        return float(r)

    fps = _parse_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1")
    duration = float(stream.get("duration") or fmt.get("duration") or 0.0)
    nframes_raw = stream.get("nb_frames")
    nframes = int(nframes_raw) if nframes_raw and nframes_raw.isdigit() else int(round(duration * fps))
    return VideoInfo(
        path=str(path),
        width=int(stream.get("width", 0)),
        height=int(stream.get("height", 0)),
        fps=fps,
        duration_sec=duration,
        nframes=nframes,
        codec=stream.get("codec_name", ""),
    )


def _run(cmd: list[str], on_log: Optional[Callable[[str], None]] = None) -> None:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1)
    assert proc.stdout is not None
    for line in proc.stdout:
        if on_log:
            on_log(line.rstrip())
    ret = proc.wait()
    if ret != 0:
        raise RuntimeError(f"ffmpeg failed (exit {ret}): {' '.join(cmd)}")


def standardize_video(
    src: Path, dst: Path,
    target_fps: int = 30, target_width: int = 640, target_height: int = 360,
    crf: int = 18, strip_audio: bool = True,
    on_log: Optional[Callable[[str], None]] = None,
) -> None:
    """Re-encode `src` to a consistent fps/resolution, matching the
    LANCZOS-resize convention already used for DLC training frames."""
    ffmpeg_bin, _ = _require_ffmpeg()
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = f"fps={target_fps},scale={target_width}:{target_height}:flags=lanczos"
    cmd = [
        ffmpeg_bin, "-y", "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-pix_fmt", "yuv420p",
    ]
    cmd += ["-an"] if strip_audio else ["-c:a", "aac"]
    cmd += [str(dst)]
    _run(cmd, on_log)


def cut_clip(
    src: Path, dst: Path, start_sec: float, end_sec: float,
    target_fps: Optional[int] = None,
    target_width: Optional[int] = None, target_height: Optional[int] = None,
    crf: int = 18, strip_audio: bool = True,
    on_log: Optional[Callable[[str], None]] = None,
) -> None:
    """Cut [start_sec, end_sec) out of `src`. Re-encodes (rather than
    stream-copying) so the cut point lands on the exact requested time --
    stream copy can only cut on keyframes, which silently shifts stitch
    boundaries relative to the semantic-annotation timebase."""
    ffmpeg_bin, _ = _require_ffmpeg()
    dst.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.0, end_sec - start_sec)
    cmd = [ffmpeg_bin, "-y", "-ss", f"{start_sec:.3f}", "-i", str(src), "-t", f"{duration:.3f}"]
    vf_parts = []
    if target_fps:
        vf_parts.append(f"fps={target_fps}")
    if target_width and target_height:
        vf_parts.append(f"scale={target_width}:{target_height}:flags=lanczos")
    if vf_parts:
        cmd += ["-vf", ",".join(vf_parts)]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p"]
    cmd += ["-an"] if strip_audio else ["-c:a", "aac"]
    cmd += [str(dst)]
    _run(cmd, on_log)


def extract_frame_png(src: Path, dst_png: Path, frame_idx: int, fps: float) -> None:
    """Extract a single frame by index (assuming constant fps) to PNG.
    Used as a fallback; the keypoint tab normally grabs frames directly via
    OpenCV for interactive display, and only shells out to ffmpeg here if a
    bit-exact re-extraction from the *original* source file is requested."""
    ffmpeg_bin, _ = _require_ffmpeg()
    dst_png.parent.mkdir(parents=True, exist_ok=True)
    t = frame_idx / fps if fps else 0.0
    cmd = [ffmpeg_bin, "-y", "-ss", f"{t:.4f}", "-i", str(src), "-frames:v", "1", str(dst_png)]
    _run(cmd)
