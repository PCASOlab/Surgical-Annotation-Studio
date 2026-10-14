"""
io_utils/safe_io.py

Every project file the app saves goes through atomic_write(): the new
content is written to a temporary file next to the target and then swapped
in with os.replace(), so a failed save never leaves a half-written file --
the old version stays intact.

Windows makes this matter: Excel locks any CSV/XLSX it has open (for
reading too), and sync/antivirus tools can hold a file for a moment. Short
locks are retried; a lasting one raises FileBusyError with a message the
GUI can show as-is (see widgets/save_guard.py).
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

# total wait ~1 s before giving up on a locked file
RETRY_DELAYS = (0.1, 0.2, 0.3, 0.4)


class FileBusyError(OSError):
    """The file is locked by another program (typically Excel)."""

    def __init__(self, path: Path, action: str, cause: Exception):
        self.path = Path(path)
        super().__init__(
            f"'{self.path.name}' couldn't be {action} because it is open in another "
            f"program (for example Excel) or locked by sync/antivirus software. "
            f"Close it and try again.\n\n({self.path})"
        )
        self.__cause__ = cause


def _with_retries(path: Path, action: str, fn: Callable[[], T]) -> T:
    for delay in (*RETRY_DELAYS, None):
        try:
            return fn()
        except PermissionError as e:
            if delay is None:
                raise FileBusyError(path, action, e) from e
            time.sleep(delay)
    raise AssertionError("unreachable")


def temp_path_for(path: Path) -> Path:
    """Temp file in the same folder (so os.replace stays on one drive). The
    leading '~' keeps it out of the app's glob patterns; the original suffix
    is kept because some writers (openpyxl) care about it."""
    return path.with_name(f"~{path.stem}.{os.getpid()}.tmp{path.suffix}")


def atomic_write(path: Path, write: Callable[[Path], None]) -> None:
    """Call write(tmp_path) to produce the new content, then swap it in."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp_path_for(path)
    try:
        write(tmp)
        _with_retries(path, "saved", lambda: os.replace(tmp, path))
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write(path, lambda tmp: tmp.write_text(text, encoding="utf-8"))


def read_locked_aware(path: Path, read: Callable[[], T]) -> T:
    """Run a read of `path`, retrying briefly and raising FileBusyError if
    another program keeps it locked."""
    return _with_retries(Path(path), "read", read)
