"""
widgets/clinical_tab.py

Two panels:
  - Left: load merged_surgical_data_v2.csv (or any case-outcome CSV keyed
    by case_id) and browse read-only clinical/outcome context per case
    (gland texture, duct size, EBL, FRS, POPF, Clavien-Dindo, etc). This
    data is never edited or re-written by the GUI -- it comes from chart
    review, not video annotation.
  - Right: a read-only review of every score entry -- case-level and
    per-stitch subitems alike, one row per subitem regardless of which
    procedure's rubric it came from (see core.config.RUBRICS). Each
    CASE's scores live in their own file
    (clinical/score_entries_<case_id>.csv); this table merges all of them
    for review, with an optional Case type filter. Scoring itself happens
    in the Preprocessing tab (while cutting clips) or the 1.5 Existing
    Clips tab (for clips already cut) -- this tab is for
    reviewing/auditing what's been recorded, not a second entry point.

PII columns (MRN, Progressive_Number) are stripped on load and never
surfaced anywhere in this tab.
"""
from __future__ import annotations
from pathlib import Path

import pandas as pd
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView,
    QCheckBox,
)
from widgets.no_scroll_combo import NoScrollComboBox

from core.config import CASE_TYPES, CASE_TYPE_LABELS
from core.project import ProjectManager
from io_utils import clinical_io


class ClinicalTab(QWidget):
    def __init__(self, pm: ProjectManager, parent=None):
        super().__init__(parent)
        self.pm = pm
        self._clinical_df = None
        self._build_ui()
        self._try_autoload_clinical_csv()
        self.refresh_cases()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)

        left = QGroupBox("Clinical / outcome context (read-only)")
        left_l = QVBoxLayout(left)
        top = QHBoxLayout()
        btn_load = QPushButton("Load clinical CSV\u2026")
        btn_load.clicked.connect(self._load_csv)
        top.addWidget(btn_load)
        top.addWidget(QLabel("Case:"))
        self.case_combo = NoScrollComboBox()
        self.case_combo.currentTextChanged.connect(self._on_case_changed)
        top.addWidget(self.case_combo)
        top.addStretch(1)
        left_l.addLayout(top)

        self.context_table = QTableWidget(0, 2)
        self.context_table.setHorizontalHeaderLabels(["Field", "Value"])
        self.context_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.context_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        left_l.addWidget(self.context_table)
        layout.addWidget(left, stretch=1)

        right = QGroupBox("Score entries (entered in the Preprocessing tab)")
        right_l = QVBoxLayout(right)
        row = QHBoxLayout()
        self.all_cases_check = QCheckBox("Show all cases")
        self.all_cases_check.toggled.connect(self._refresh_entries_table)
        row.addWidget(self.all_cases_check)
        row.addWidget(QLabel("Case type:"))
        self.case_type_filter = NoScrollComboBox()
        self.case_type_filter.addItem("All", userData=None)
        for ct in CASE_TYPES:
            self.case_type_filter.addItem(CASE_TYPE_LABELS.get(ct, ct), userData=ct)
        self.case_type_filter.currentIndexChanged.connect(self._refresh_entries_table)
        row.addWidget(self.case_type_filter)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.refresh_cases)
        row.addWidget(btn_refresh)
        row.addStretch(1)
        right_l.addLayout(row)

        self.entries_table = QTableWidget(0, 10)
        self.entries_table.setHorizontalHeaderLabels(
            ["case_id", "case_type", "stitch", "rater", "scale", "subitem",
             "score", "start", "stop", "notes"]
        )
        self.entries_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.entries_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        right_l.addWidget(self.entries_table)
        note = QLabel(
            "One row per subitem, for any procedure's rubric. Case-level rows "
            "(e.g. scale = OSATS/RSS/GEARS) have a blank stitch column; "
            "\"subitem\" shows which one (e.g. osats_gentle, rss_knot, "
            "gears_autonomy). Stitch-level rows (e.g. PJ, yank_factor, "
            "stitch_location) show the stitch ID and its start/stop offset "
            "(seconds) into the original video. Each case is saved to its "
            "own file (clinical/score_entries_<case_id>.csv)."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #888;")
        right_l.addWidget(note)
        layout.addWidget(right, stretch=1)

    # -- clinical CSV loading -------------------------------------------------
    def _try_autoload_clinical_csv(self) -> None:
        candidates = list(self.pm.paths.clinical.glob("*.csv"))
        merged = [c for c in candidates if "merged" in c.name.lower()]
        if merged:
            self._clinical_df = clinical_io.load_clinical_csv(merged[0])

    def refresh_cases(self) -> None:
        """Populate the Case selector from every case the project knows
        about, from three sources -- not just cases present in a loaded
        clinical CSV:
          1. pm.list_cases() -- cases with at least one cut clip
          2. score_entries.csv's own case_id column -- covers a case that's
             been scored (e.g. case-level OSATS/RSS) before any clip has
             been cut yet, which pm.list_cases() doesn't know about
          3. a loaded clinical CSV's case ids, if one was loaded
        """
        current = self.case_combo.currentText()
        known = set(self.pm.list_cases())
        score_df = clinical_io.load_score_entries(self.pm.paths.clinical)
        if not score_df.empty and "case_id" in score_df.columns:
            known |= set(score_df["case_id"].dropna().astype(str))
        if self._clinical_df is not None:
            known |= set(clinical_io.case_ids(self._clinical_df))
        ids = sorted(known)

        self.case_combo.blockSignals(True)
        self.case_combo.clear()
        self.case_combo.addItems(ids)
        self.case_combo.blockSignals(False)

        if current in ids:
            self.case_combo.setCurrentText(current)
            self._on_case_changed(current)
        elif ids:
            self.case_combo.setCurrentIndex(0)
            self._on_case_changed(ids[0])
        else:
            self.context_table.setRowCount(0)
            self._refresh_entries_table()

    def _load_csv(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Load clinical CSV", str(Path.home()), "CSV files (*.csv)"
        )
        if not path_str:
            return
        src = Path(path_str)
        dst = self.pm.paths.clinical / src.name
        self.pm.paths.clinical.mkdir(parents=True, exist_ok=True)
        if src.resolve() != dst.resolve():
            dst.write_bytes(src.read_bytes())
        self._clinical_df = clinical_io.load_clinical_csv(dst)
        self.refresh_cases()

    def _on_case_changed(self, case_id: str) -> None:
        self.context_table.setRowCount(0)
        if self._clinical_df is not None and case_id:
            row = clinical_io.case_display_row(self._clinical_df, case_id)
            self.context_table.setRowCount(len(row))
            for i, (k, v) in enumerate(row.items()):
                self.context_table.setItem(i, 0, QTableWidgetItem(str(k)))
                self.context_table.setItem(i, 1, QTableWidgetItem("" if v is None else str(v)))
        self._refresh_entries_table()

    # -- score entry review ---------------------------------------------------
    def _refresh_entries_table(self) -> None:
        case_id = self.case_combo.currentText()
        if case_id and not self.all_cases_check.isChecked():
            df = clinical_io.load_score_entries(self.pm.paths.clinical, case_id=case_id)
        else:
            df = clinical_io.load_score_entries(self.pm.paths.clinical)

        case_type = self.case_type_filter.currentData()
        if case_type and not df.empty and "case_type" in df.columns:
            df = df[df["case_type"] == case_type]

        self.entries_table.setRowCount(len(df))

        def clean(val) -> str:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return ""
            return str(val)

        for i, (_, r) in enumerate(df.iterrows()):
            self.entries_table.setItem(i, 0, QTableWidgetItem(clean(r.get("case_id"))))
            self.entries_table.setItem(i, 1, QTableWidgetItem(clean(r.get("case_type"))))
            self.entries_table.setItem(i, 2, QTableWidgetItem(clean(r.get("video_id_annot"))))
            self.entries_table.setItem(i, 3, QTableWidgetItem(clean(r.get("rater"))))
            self.entries_table.setItem(i, 4, QTableWidgetItem(clean(r.get("scale"))))
            self.entries_table.setItem(i, 5, QTableWidgetItem(clean(r.get("hogg_var"))))
            self.entries_table.setItem(i, 6, QTableWidgetItem(clean(r.get("score"))))
            self.entries_table.setItem(i, 7, QTableWidgetItem(clean(r.get("start"))))
            self.entries_table.setItem(i, 8, QTableWidgetItem(clean(r.get("stop"))))
            self.entries_table.setItem(i, 9, QTableWidgetItem(clean(r.get("notes"))))
