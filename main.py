#!/usr/bin/env python3
"""
Surgical Annotation Studio -- entry point.

Run with:
    python main.py
or, after chmod +x main.py:
    ./main.py

Optionally pass a project directory directly to skip the open/create
dialog:
    python main.py /path/to/project
"""
from __future__ import annotations
import ctypes.util
import os
import sys
from pathlib import Path

# Linux only (Windows and macOS never set DISPLAY, so this would make the app
# silently headless there):
# - no display at all (container, SSH): use the offscreen backend.
# - a desktop, but Qt's X11 backend needs libxcb-cursor0, which Ubuntu often
#   lacks: use Wayland if the session has it, otherwise say what to install
#   (falling back to offscreen here would run the app invisibly).
if sys.platform.startswith("linux") and not os.environ.get("QT_QPA_PLATFORM"):
    has_x11 = bool(os.environ.get("DISPLAY"))
    has_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    has_xcb_cursor = ctypes.util.find_library("xcb-cursor") is not None
    if not has_x11 and not has_wayland:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    elif not has_xcb_cursor:
        if has_wayland:
            os.environ["QT_QPA_PLATFORM"] = "wayland"
        else:
            print("Surgical Annotation Studio needs the libxcb-cursor0 package on X11:\n"
                  "    sudo apt install libxcb-cursor0", file=sys.stderr)
from PySide6.QtWidgets import QApplication

from core.project import ProjectManager
from widgets.main_window import MainWindow, choose_or_create_project
from widgets.style import apply_app_style
from widgets.save_guard import install_excepthook


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Surgical Annotation Studio")
    apply_app_style(app)
    install_excepthook()

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
