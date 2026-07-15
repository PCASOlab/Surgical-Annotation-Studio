"""
widgets/dashboard_tab.py

Landing tab: shows every case known to the project, how many stitch clips
exist, how many have DLC tracking CSVs, how many have semantic annotation
xlsx files, and lets the user rescan the project directory after files
were added/changed outside the GUI (e.g. copied in from another machine).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QPushButton, QHeaderView,
)

from core.project import ProjectManager


class DashboardTab(QWidget):
    def __init__(self, pm: ProjectManager, parent=None):
        super().__init__(parent)
        self.pm = pm
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.project_label = QLabel()
        self.project_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        top.addWidget(self.project_label)
        top.addStretch(1)
        btn_refresh = QPushButton("Rescan project")
        btn_refresh.clicked.connect(self.refresh)
        top.addWidget(btn_refresh)
        layout.addLayout(top)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([
            "Case ID", "# Stitch clips", "# DLC tracking CSVs", "# Semantic XLSX (raters)"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        info = QLabel(
            "Workflow: 1) Preprocessing tab \u2013 import/standardize case video and cut "
            "into stitch clips.  2) Keypoints tab \u2013 label the 5 needle points (+ optional "
            "tools) for DeepLabCut training.  3) Semantic States tab \u2013 mark the 10-state "
            "event timeline to the millisecond.  4) Clinical tab \u2013 review linked patient "
            "outcome context and enter OSATS/RSS/PJ/yank-jerk scores."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; padding-top: 8px;")
        layout.addWidget(info)

    def refresh(self) -> None:
        self.project_label.setText(f"Project: {self.pm.paths.root}")
        rows = self.pm.status_summary()
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(row["case_id"]))
            self.table.setItem(r, 1, QTableWidgetItem(str(row["n_stitches"])))
            self.table.setItem(r, 2, QTableWidgetItem(str(row["n_dlc_csv"])))
            self.table.setItem(r, 3, QTableWidgetItem(str(row["n_semantic_xlsx"])))
