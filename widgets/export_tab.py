"""
widgets/export_tab.py

"Share / Export" tab: pick cases, see exactly what will be included and
what's left out (with sizes), then write a zip another lab member can open
with "Open existing project". The original videos and any clinical file
other than the score files are never included -- see io_utils/export.py.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QCheckBox, QPlainTextEdit, QFileDialog,
    QProgressBar, QMessageBox,
)

from core.project import ProjectManager
from io_utils import export
from widgets.save_guard import run_load
from widgets.style import mark_primary
from widgets.worker import FnWorker


class ExportTab(QWidget):
    def __init__(self, pm: ProjectManager, parent=None):
        super().__init__(parent)
        self.pm = pm
        self._plan: Optional[export.ExportPlan] = None
        self._worker: Optional[FnWorker] = None
        self._last_zip: Optional[Path] = None
        self._build_ui()
        self.refresh()

    # -- UI ---------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Pack this project into a zip to share with another lab member: cut clips, "
            "keypoint labels, semantic annotations, scores and project settings. "
            "<b>The original videos are never included</b>, and neither is any clinical "
            "file other than the score files (an outcomes CSV can hold patient "
            "identifiers). The recipient unzips it and uses <i>Open existing project</i>."
            "<br><br>The clips still show patients' surgery: share the zip only through "
            "storage your institution approves for that (e.g. Box or OneDrive)."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        body = QHBoxLayout()
        cases_box = QGroupBox("Cases to export")
        cases_l = QVBoxLayout(cases_box)
        self.case_list = QListWidget()
        self.case_list.itemChanged.connect(lambda _: self._update_plan())
        cases_l.addWidget(self.case_list)
        sel_row = QHBoxLayout()
        btn_all = QPushButton("Select all")
        btn_all.clicked.connect(lambda: self._set_all_checked(True))
        sel_row.addWidget(btn_all)
        btn_none = QPushButton("Select none")
        btn_none.clicked.connect(lambda: self._set_all_checked(False))
        sel_row.addWidget(btn_none)
        cases_l.addLayout(sel_row)

        cases_l.addWidget(QLabel("Options:"))
        self.drafts_check = QCheckBox("Include clips not cut yet (unfinished clip lists)")
        self.drafts_check.setChecked(True)
        self.drafts_check.toggled.connect(lambda _: self._update_plan())
        cases_l.addWidget(self.drafts_check)
        self.std_check = QCheckBox("Include standardized full-case videos\n"
                                   "(a 640×360 copy of each whole case video)")
        self.std_check.toggled.connect(lambda _: self._update_plan())
        cases_l.addWidget(self.std_check)
        body.addWidget(cases_box, stretch=1)

        summary_box = QGroupBox("What the zip will contain")
        summary_l = QVBoxLayout(summary_box)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        summary_l.addWidget(self.summary)
        body.addWidget(summary_box, stretch=2)
        layout.addLayout(body, stretch=1)

        action_row = QHBoxLayout()
        btn_refresh = QPushButton("↻ Refresh")
        btn_refresh.clicked.connect(self.refresh)
        action_row.addWidget(btn_refresh)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        action_row.addWidget(self.progress, stretch=1)
        self.status_label = QLabel("")
        action_row.addWidget(self.status_label, stretch=1)
        self.btn_open_folder = QPushButton("Open folder")
        self.btn_open_folder.setEnabled(False)
        self.btn_open_folder.clicked.connect(self._open_folder)
        action_row.addWidget(self.btn_open_folder)
        self.btn_export = mark_primary(QPushButton("Export to zip…"))
        self.btn_export.clicked.connect(self._export)
        action_row.addWidget(self.btn_export)
        layout.addLayout(action_row)

    # -- cases + plan -------------------------------------------------------
    def refresh(self) -> None:
        """Re-read the project (call when the tab is shown)."""
        checked = {self.case_list.item(i).text() for i in range(self.case_list.count())
                   if self.case_list.item(i).checkState() == Qt.CheckState.Checked}
        first_time = self.case_list.count() == 0
        cases = sorted(set(self.pm.list_cases())
                       | {m.case_id for m in self.pm.all_stitch_meta()})
        self.case_list.blockSignals(True)
        self.case_list.clear()
        for case in cases:
            item = QListWidgetItem(case)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            on = first_time or case in checked
            item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            self.case_list.addItem(item)
        self.case_list.blockSignals(False)
        self._update_plan()

    def _set_all_checked(self, on: bool) -> None:
        self.case_list.blockSignals(True)
        for i in range(self.case_list.count()):
            self.case_list.item(i).setCheckState(
                Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.case_list.blockSignals(False)
        self._update_plan()

    def _selected_cases(self) -> list[str]:
        return [self.case_list.item(i).text() for i in range(self.case_list.count())
                if self.case_list.item(i).checkState() == Qt.CheckState.Checked]

    def _update_plan(self) -> None:
        cases = self._selected_cases()
        self._plan = run_load(self, "The project files", lambda: export.plan_export(
            self.pm, cases, include_drafts=self.drafts_check.isChecked(),
            include_standardized=self.std_check.isChecked()), default=None)
        busy = self._worker is not None and self._worker.isRunning()
        self.btn_export.setEnabled(bool(cases) and self._plan is not None and not busy)
        if self._plan is None:
            self.summary.setPlainText("Couldn't read the project files.")
            return
        if not cases:
            self.summary.setPlainText("Select at least one case.")
            return
        hs = export.human_size
        lines = [f"Cases: {', '.join(cases)}", "", "Included:"]
        for kind, (n, size) in sorted(self._plan.size_by_kind().items()):
            lines.append(f"  ✔ {kind}: {n} file(s), {hs(size)}")
        lines.append(f"  Total: {hs(self._plan.total_size)} before zipping "
                     f"(clips and images barely shrink)")
        lines += ["", "Left out:"]
        if self._plan.left_out:
            lines += [f"  ✖ {what} ({hs(size)}): {why}"
                      for what, size, why in self._plan.left_out]
        else:
            lines.append("  nothing")
        self.summary.setPlainText("\n".join(lines))

    # -- export ---------------------------------------------------------------
    def _export(self) -> None:
        self._update_plan()
        plan = self._plan
        if plan is None or not plan.cases:
            return
        suggested = Path.home() / f"{plan.project_name}_share_{date.today():%Y-%m-%d}.zip"
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Export project to zip", str(suggested), "Zip files (*.zip)")
        if not path_str:
            return
        dest = Path(path_str)
        if dest.suffix.lower() != ".zip":
            dest = dest.with_suffix(".zip")
        try:
            dest.resolve().relative_to(self.pm.paths.root.resolve())
            QMessageBox.warning(self, "Choose another folder",
                                "Save the zip outside the project folder.")
            return
        except ValueError:
            pass  # outside the project: good

        self.btn_export.setEnabled(False)
        self.btn_open_folder.setEnabled(False)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.status_label.setText(f"Writing {dest.name} …")
        self._worker = FnWorker(export.write_export_zip, plan, dest)
        self._worker.progress.connect(lambda c, t: self.progress.setValue(c))
        self._worker.finished_ok.connect(self._on_export_done)
        self._worker.failed.connect(self._on_export_failed)
        self._worker.start()

    def _on_export_done(self, dest: Path) -> None:
        self.progress.setVisible(False)
        self._last_zip = Path(dest)
        size = export.human_size(self._last_zip.stat().st_size)
        self.status_label.setText(f"✔ Saved {self._last_zip.name} ({size})")
        self.btn_open_folder.setEnabled(True)
        self.btn_export.setEnabled(True)

    def _on_export_failed(self, msg: str) -> None:
        self.progress.setVisible(False)
        self.status_label.setText("✗ Export failed")
        self.btn_export.setEnabled(True)
        QMessageBox.warning(self, "Export failed",
                            f"The zip was not written (nothing partial was left behind).\n\n{msg}")

    def _open_folder(self) -> None:
        if self._last_zip is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_zip.parent)))
