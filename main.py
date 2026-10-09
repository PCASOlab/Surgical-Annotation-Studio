#!/usr/bin/env python3
"""
Surgical Annotation Studio -- entry point.

Run with:
    python3 main.py
or, after chmod +x main.py:
    ./main.py

Optionally pass a project directory directly to skip the open/create
dialog:
    python3 main.py /path/to/project
"""
from __future__ import annotations
import ctypes.util
import os
import sys
from pathlib import Path

# On Linux, Qt may use the XCB backend even when no real display is available,
# and the xcb-cursor library is often missing in slim/container images. Fall
# back to the offscreen backend in those cases so the app still launches.
if not os.environ.get("QT_QPA_PLATFORM"):
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    has_xcb_cursor = ctypes.util.find_library("xcb-cursor") is not None
    if not has_display or not has_xcb_cursor:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication

from core.project import ProjectManager
from widgets.main_window import MainWindow, choose_or_create_project


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Surgical Annotation Studio")

    if len(sys.argv) > 1:
        pm = ProjectManager.load(Path(sys.argv[1]))
    else:
        pm = choose_or_create_project()
        if pm is None:
            return 0

    window = MainWindow(pm)
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
