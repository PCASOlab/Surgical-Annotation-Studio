"""
widgets/main_window.py

Top-level window: on launch, asks the user to open an existing project
folder or create a new one, then builds the 5 tabs (Dashboard,
Preprocessing, Keypoints, Semantic States, Clinical), all sharing the same
ProjectManager instance so every tab sees a consistent, live view of the
project's cases/stitches/annotations.

Window sizing: the app opens maximized by default (most tabs -- video
players, forms, tables -- want as much space as they can get). A View
menu plus F11/Ctrl+M give explicit fullscreen/maximize toggles in case the
window manager's own controls aren't available (e.g. some remote-desktop
or kiosk setups), and a minimum window size stops the layout from being
squeezed into a broken state if someone drags it very small. Each tab is
wrapped in its own QScrollArea so that if the window (or a maximized
screen) is still smaller than a tab's ideal content size, the tab scrolls
instead of overlapping or clipping widgets.
"""
from __future__ import annotations
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QFileDialog, QMessageBox, QInputDialog,
    QScrollArea, QWidget, QTableView,
)

from core.project import ProjectManager, ProjectConfig
from core.version import VERSION
from io_utils import clinical_io, excel_copies, xlsx_mirror
from widgets.save_guard import run_save
from widgets.dashboard_tab import DashboardTab
from widgets.preprocessing_tab import PreprocessingTab
from widgets.existing_clips_tab import ExistingClipsTab
from widgets.keypoint_tab import KeypointTab
from widgets.semantic_tab import SemanticTab
from widgets.clinical_tab import ClinicalTab
from widgets.export_tab import ExportTab

MIN_WINDOW_WIDTH = 1100
MIN_WINDOW_HEIGHT = 700


def _wrap_scrollable(widget: QWidget) -> QScrollArea:
    """Wrap a tab's content widget in a resizable QScrollArea so the tab
    scrolls gracefully instead of squeezing/overlapping its contents when
    the window is smaller than the tab's ideal size."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidget(widget)
    return scroll


class MainWindow(QMainWindow):
    def __init__(self, pm: ProjectManager):
        super().__init__()
        self.pm = pm
        self.setWindowTitle(f"Surgical Annotation Studio {VERSION} \u2014 {pm.paths.root.name}")
        self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.resize(1400, 900)
        self._migrate_score_files()
        xlsx_mirror.failure_hook = self._on_excel_copy_failed
        excel_copies.refresh_stale(pm)  # copies missed while open in Excel

        self.tabs = QTabWidget()
        self.dashboard_tab = DashboardTab(pm)
        self.preprocessing_tab = PreprocessingTab(pm)
        self.existing_clips_tab = ExistingClipsTab(pm)
        self.keypoint_tab = KeypointTab(pm)
        self.semantic_tab = SemanticTab(pm)
        self.clinical_tab = ClinicalTab(pm)
        self.export_tab = ExportTab(pm)

        self.tabs.addTab(_wrap_scrollable(self.dashboard_tab), "Dashboard")
        self.tabs.addTab(_wrap_scrollable(self.preprocessing_tab), "1. Preprocessing")
        self.tabs.addTab(_wrap_scrollable(self.existing_clips_tab), "1.5 Existing Clips")
        self.tabs.addTab(_wrap_scrollable(self.keypoint_tab), "2. Keypoints (DLC)")
        self.tabs.addTab(_wrap_scrollable(self.semantic_tab), "3. Semantic States")
        self.tabs.addTab(_wrap_scrollable(self.clinical_tab), "4. Clinical")
        self.tabs.addTab(_wrap_scrollable(self.export_tab), "Share / Export")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)
        # zebra-striped rows make wide tables easier to follow
        for table in self.findChildren(QTableView):
            table.setAlternatingRowColors(True)

        self._build_view_menu()
        self._was_maximized_before_fullscreen = True

    def _migrate_score_files(self) -> None:
        """Projects from before scores were filed per rater: split their
        score files once, before any tab reads them."""
        clinical = self.pm.paths.clinical
        if not clinical_io.legacy_score_files(clinical):
            return
        report: list[str] = []
        if not run_save(None, "Converting the score files to one file per rater",
                        lambda: report.extend(clinical_io.migrate_legacy_score_files(clinical))):
            return  # old files stay in place and are still shown in the Clinical tab
        QMessageBox.information(
            None, "Score files converted",
            "Scores are now saved in one file per case and rater "
            "(clinical/score_entries_<case>_rater-<rater>.csv). Converted:\n\n"
            + "\n".join(report)
            + "\n\nThe old files were kept with a .migrated ending.")

    def _on_excel_copy_failed(self, path, _err) -> None:
        # the CSV was saved; only its Excel copy is behind
        self.statusBar().showMessage(
            f"Saved -- but the Excel copy {path.name} is open in another program, so it "
            f"wasn't updated. It will be updated on the next save after you close it.",
            20000)

    # -- window state: fullscreen / maximize ------------------------------
    def _build_view_menu(self) -> None:
        menu = self.menuBar().addMenu("&View")

        act_fullscreen = menu.addAction("Toggle &Full Screen")
        act_fullscreen.setShortcut(QKeySequence(Qt.Key.Key_F11))
        act_fullscreen.triggered.connect(self.toggle_fullscreen)

        act_maximize = menu.addAction("Toggle &Maximize")
        act_maximize.setShortcut(QKeySequence("Ctrl+M"))
        act_maximize.triggered.connect(self.toggle_maximize)

        # Esc backs out of full screen (doesn't close the app -- only
        # acts while actually in full-screen mode).
        self._esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc_shortcut.activated.connect(self._exit_fullscreen_if_active)

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            if self._was_maximized_before_fullscreen:
                self.showMaximized()
        else:
            self._was_maximized_before_fullscreen = self.isMaximized()
            self.showFullScreen()

    def toggle_maximize(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        elif self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _exit_fullscreen_if_active(self) -> None:
        if self.isFullScreen():
            self.toggle_fullscreen()

    def _on_tab_changed(self, idx: int) -> None:
        # Refresh dropdowns whenever a tab becomes visible, so clips cut in
        # the Preprocessing tab immediately show up elsewhere. Each tab is
        # wrapped in a QScrollArea (see _wrap_scrollable), so unwrap it to
        # get back the actual tab widget.
        wrapper = self.tabs.widget(idx)
        widget = wrapper.widget() if isinstance(wrapper, QScrollArea) else wrapper
        if isinstance(widget, DashboardTab):
            widget.refresh()
        elif isinstance(widget, ExistingClipsTab):
            widget.refresh_cases()
        elif isinstance(widget, KeypointTab):
            widget.refresh_cases()
        elif isinstance(widget, SemanticTab):
            widget.refresh_cases()
        elif isinstance(widget, ClinicalTab):
            widget.refresh_cases()
        elif isinstance(widget, ExportTab):
            widget.refresh()


def choose_or_create_project() -> ProjectManager | None:
    box = QMessageBox()
    box.setWindowTitle(f"Surgical Annotation Studio {VERSION}")
    box.setText("Open an existing project or create a new one?")
    open_btn = box.addButton("Open existing\u2026", QMessageBox.ButtonRole.AcceptRole)
    new_btn = box.addButton("Create new\u2026", QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.exec()

    clicked = box.clickedButton()
    if clicked == open_btn:
        path_str = QFileDialog.getExistingDirectory(None, "Open project folder", str(Path.home()))
        if not path_str:
            return None
        return ProjectManager.load(Path(path_str))
    elif clicked == new_btn:
        path_str = QFileDialog.getExistingDirectory(
            None, "Choose (empty) folder for new project", str(Path.home())
        )
        if not path_str:
            return None
        name, ok = QInputDialog.getText(None, "Project name", "Project name:")
        cfg = ProjectConfig(project_name=name or "surgical_annotation_project")
        return ProjectManager.create(Path(path_str), cfg)
    return None
