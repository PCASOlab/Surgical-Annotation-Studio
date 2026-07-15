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
import sys
from pathlib import Path

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
