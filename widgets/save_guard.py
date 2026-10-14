"""
widgets/save_guard.py

Wraps file saves/loads triggered from the GUI so a failure (most often a
CSV/XLSX that's open in Excel) shows a Retry / Skip dialog instead of an
unhandled exception that stops the current action halfway.

    if not run_save(self, "Score 'osats_time'", lambda: write_it()):
        ...  # leave the UI in a state that shows it wasn't saved

    rows = run_load(self, "Saved scores", lambda: read_it(), default=[])
"""
from __future__ import annotations

import traceback
from typing import Callable, Optional, TypeVar

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from io_utils.safe_io import FileBusyError

T = TypeVar("T")


def _describe(err: Exception) -> str:
    if isinstance(err, FileBusyError):
        return str(err)
    if isinstance(err, OSError):
        where = f"\n\n({err.filename})" if err.filename else ""
        return f"{err.strerror or err}{where}"
    return f"{type(err).__name__}: {err}"


def _ask_retry(parent: Optional[QWidget], title: str, text: str, detail: str,
               skip_label: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    box.setInformativeText(detail)
    retry = box.addButton("Retry", QMessageBox.ButtonRole.AcceptRole)
    box.addButton(skip_label, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(retry)
    box.exec()
    return box.clickedButton() is retry


def run_save(parent: Optional[QWidget], what: str, action: Callable[[], object]) -> bool:
    """Run `action`; on a file error ask Retry/Skip. True if it succeeded."""
    while True:
        try:
            action()
            return True
        except OSError as e:
            if not _ask_retry(parent, "Not saved", f"{what} was not saved.",
                              _describe(e), "Skip (not saved)"):
                return False


def run_load(parent: Optional[QWidget], what: str, action: Callable[[], T],
             default: T) -> T:
    """Run `action`; on a file error ask Retry/Skip. Returns `default` if skipped."""
    while True:
        try:
            return action()
        except OSError as e:
            if not _ask_retry(parent, "Couldn't read file", f"{what} couldn't be read.",
                              _describe(e), "Skip"):
                return default


def install_excepthook() -> None:
    """Show unexpected errors in a dialog instead of losing them (the
    packaged app has no console) and keep the app running."""
    import sys

    def hook(exc_type, exc, tb) -> None:
        traceback.print_exception(exc_type, exc, tb)
        if QApplication.instance() is None:
            return
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Unexpected error")
        if isinstance(exc, OSError):
            box.setText("A file couldn't be saved or read, so the last action didn't finish.")
            box.setInformativeText(_describe(exc))
        else:
            box.setText("Something went wrong and the last action didn't finish. "
                        "Your saved work is not affected.")
            box.setInformativeText(f"{exc_type.__name__}: {exc}")
        box.setDetailedText("".join(traceback.format_exception(exc_type, exc, tb)))
        box.exec()

    sys.excepthook = hook
