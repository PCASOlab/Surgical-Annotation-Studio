"""
widgets/semantic_tab.py

Frame/millisecond-accurate labeling of the 10-state semantic ontology
(9 explicit event types + implicit BACKGROUND). The annotator scrubs the
stitch clip and clicks (or presses hotkeys 1-9) to drop an event marker at
the exact current playhead position. Saving converts clip-relative
timestamps into the ORIGINAL case video's timebase (using the stitch's
recorded cut offset) and writes them into
semantic/<case_id>_PJ_d2m_semantic_<rater>.xlsx using the exact T/E column
schema of the existing annotation files, so output here is a drop-in
addition to the existing dataset.
"""
from __future__ import annotations
from pathlib import Path
from datetime import time as dtime

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QPlainTextEdit, QTextEdit, QGroupBox,
)
from widgets.no_scroll_combo import NoScrollComboBox

from core.config import EVENT_TYPES, EVENT_HOTKEYS
from core.project import ProjectManager
from io_utils import semantic_io
from widgets.video_widget import VideoPlayerWidget, ms_to_timecode


class SemanticTab(QWidget):
    def __init__(self, pm: ProjectManager, parent=None):
        super().__init__(parent)
        self.pm = pm
        self._current_case: str | None = None
        self._events: list[tuple[float, str]] = []  # (clip_ms, label)
        self._build_ui()
        self.refresh_cases()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # -- UI -----------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Case:"))
        self.case_combo = NoScrollComboBox()
        self.case_combo.currentTextChanged.connect(self._on_case_changed)
        top.addWidget(self.case_combo)
        top.addWidget(QLabel("Stitch clip:"))
        self.stitch_combo = NoScrollComboBox()
        self.stitch_combo.currentTextChanged.connect(self._on_stitch_changed)
        top.addWidget(self.stitch_combo)
        top.addWidget(QLabel("Rater:"))
        self.rater_edit = QLineEdit()
        self.rater_edit.setMaximumWidth(120)
        self.rater_edit.setPlaceholderText("e.g. Saw")
        top.addWidget(self.rater_edit)
        btn_load = QPushButton("Load existing annotation")
        btn_load.clicked.connect(self._load_existing)
        top.addWidget(btn_load)
        top.addStretch(1)
        layout.addLayout(top)

        self.player = VideoPlayerWidget()
        layout.addWidget(self.player, stretch=2)

        event_box = QGroupBox("Mark event at current playhead (hotkeys 1-9)")
        grid = QGridLayout(event_box)
        self._event_buttons: dict[str, QPushButton] = {}
        for key, label in EVENT_HOTKEYS.items():
            btn = QPushButton(f"[{key}] {label}")
            btn.clicked.connect(lambda _, l=label: self._add_event(l))
            row, col = divmod(int(key) - 1, 3)
            grid.addWidget(btn, row, col)
            self._event_buttons[label] = btn
        layout.addWidget(event_box)

        mid = QHBoxLayout()
        self.event_table = QTableWidget(0, 2)
        self.event_table.setHorizontalHeaderLabels(["Time (clip-relative)", "Event"])
        self.event_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        mid.addWidget(self.event_table, stretch=2)

        side = QVBoxLayout()
        btn_update = QPushButton("Move selected event \u2192 playhead")
        btn_update.clicked.connect(self._move_selected_to_playhead)
        side.addWidget(btn_update)
        btn_delete = QPushButton("Delete selected event")
        btn_delete.clicked.connect(self._delete_selected)
        side.addWidget(btn_delete)
        btn_validate = QPushButton("Validate quality checks")
        btn_validate.clicked.connect(self._validate)
        side.addWidget(btn_validate)

        side.addWidget(QLabel("Comments:"))
        self.comments_edit = QTextEdit()
        self.comments_edit.setMaximumHeight(80)
        side.addWidget(self.comments_edit)

        btn_save = QPushButton("\U0001f4be Save to XLSX")
        btn_save.clicked.connect(self._save)
        side.addWidget(btn_save)
        side.addStretch(1)
        mid.addLayout(side, stretch=1)
        layout.addLayout(mid, stretch=1)

        self.validation_view = QPlainTextEdit()
        self.validation_view.setReadOnly(True)
        self.validation_view.setMaximumHeight(90)
        self.validation_view.setPlaceholderText("Quality-check results appear here.")
        layout.addWidget(self.validation_view)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.text()
        if key in EVENT_HOTKEYS:
            self._add_event(EVENT_HOTKEYS[key])
            return
        super().keyPressEvent(event)

    # -- case/stitch selection --------------------------------------------
    def refresh_cases(self) -> None:
        self.case_combo.blockSignals(True)
        self.case_combo.clear()
        self.case_combo.addItems(self.pm.list_cases())
        self.case_combo.blockSignals(False)
        if self.case_combo.count():
            self._on_case_changed(self.case_combo.currentText())

    def _on_case_changed(self, case_id: str) -> None:
        self._current_case = case_id or None
        self.stitch_combo.blockSignals(True)
        self.stitch_combo.clear()
        if case_id:
            for p in self.pm.list_stitches(case_id):
                if p.name.startswith("_full_standardized"):
                    continue
                self.stitch_combo.addItem(p.stem)
        self.stitch_combo.blockSignals(False)
        if self.stitch_combo.count():
            self._on_stitch_changed(self.stitch_combo.currentText())

    def _on_stitch_changed(self, stitch_id: str) -> None:
        if not stitch_id or not self._current_case:
            return
        path = self.pm.paths.pose / self._current_case / f"{stitch_id}.mp4"
        if not path.exists():
            return
        self.player.load(path)
        self._events = []
        self._refresh_event_table()
        self.comments_edit.clear()
        self.validation_view.clear()

    def _current_meta(self):
        if not self._current_case:
            return None
        return self.pm.get_stitch_meta(self._current_case, self.stitch_combo.currentText())

    def _xlsx_path(self) -> Path | None:
        rater = self.rater_edit.text().strip()
        if not self._current_case or not rater:
            return None
        return self.pm.paths.semantic / f"{self._current_case}_PJ_d2m_semantic_{rater}.xlsx"

    # -- event editing -------------------------------------------------------
    def _add_event(self, label: str) -> None:
        if self.player.nframes == 0:
            return
        ms = self.player.current_ms()
        self._events.append((ms, label))
        self._events.sort(key=lambda t: t[0])
        self._refresh_event_table()

    def _refresh_event_table(self) -> None:
        self.event_table.setRowCount(len(self._events))
        for r, (ms, label) in enumerate(self._events):
            self.event_table.setItem(r, 0, QTableWidgetItem(ms_to_timecode(ms)))
            self.event_table.setItem(r, 1, QTableWidgetItem(label))

    def _selected_row(self) -> int | None:
        rows = {idx.row() for idx in self.event_table.selectedIndexes()}
        return next(iter(rows), None)

    def _move_selected_to_playhead(self) -> None:
        r = self._selected_row()
        if r is None:
            return
        _, label = self._events[r]
        self._events[r] = (self.player.current_ms(), label)
        self._events.sort(key=lambda t: t[0])
        self._refresh_event_table()

    def _delete_selected(self) -> None:
        r = self._selected_row()
        if r is None:
            return
        del self._events[r]
        self._refresh_event_table()

    # -- validation -----------------------------------------------------------
    def _events_as_time_objs(self) -> list[tuple[dtime, str]]:
        return [(semantic_io.seconds_to_time(ms / 1000.0), label) for ms, label in self._events]

    def _validate(self) -> list[str]:
        problems = semantic_io.validate_events(self._events_as_time_objs())
        if problems:
            self.validation_view.setPlainText("\n".join(f"\u2717 {p}" for p in problems))
        else:
            self.validation_view.setPlainText("\u2713 No quality-check issues found.")
        return problems

    # -- load / save ------------------------------------------------------------
    def _load_existing(self) -> None:
        xlsx_path = self._xlsx_path()
        meta = self._current_meta()
        if xlsx_path is None:
            QMessageBox.warning(self, "Missing info", "Enter a rater name first.")
            return
        if not xlsx_path.exists():
            QMessageBox.information(self, "Not found", f"No existing file at {xlsx_path.name}.")
            return
        stitch_id = self.stitch_combo.currentText()
        rows = semantic_io.read_rows(xlsx_path)
        match = next((r for r in rows if r.pcaso_var == stitch_id), None)
        if match is None:
            QMessageBox.information(self, "Not found",
                                     f"No row for stitch '{stitch_id}' in {xlsx_path.name}.")
            return
        offset = meta.start_sec_in_source if meta else 0.0
        self._events = []
        skipped = 0
        for t, label in match.sorted_events():
            if not isinstance(t, dtime):
                skipped += 1
                continue
            abs_sec = t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1e6
            clip_ms = (abs_sec - offset) * 1000.0
            self._events.append((clip_ms, label))
        self._events.sort(key=lambda x: x[0])
        if skipped:
            QMessageBox.warning(
                self, "Malformed timestamp(s) skipped",
                f"{skipped} event(s) had a non-time cell (typo?) and were skipped. "
                f"Check the source xlsx directly to fix those rows."
            )
        self._refresh_event_table()
        self.comments_edit.setPlainText(match.comments or "")

    def _save(self) -> None:
        if not self._events:
            QMessageBox.warning(self, "Nothing to save", "Add at least one event first.")
            return
        problems = self._validate()
        if problems:
            resp = QMessageBox.question(
                self, "Quality-check issues found",
                f"{len(problems)} issue(s) found (see panel below). Save anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
                return
        meta = self._current_meta()
        xlsx_path = self._xlsx_path()
        if xlsx_path is None:
            QMessageBox.warning(self, "Missing info", "Enter a rater name first.")
            return
        offset = meta.start_sec_in_source if meta else 0.0
        stop_sec = meta.end_sec_in_source if meta else (offset + self.player.nframes / max(self.player.fps, 1))

        events_abs = [
            (semantic_io.seconds_to_time(ms / 1000.0 + offset), label)
            for ms, label in self._events
        ]
        row = semantic_io.StitchRow(
            file=self._current_case,
            pcaso_var=self.stitch_combo.currentText(),
            start=semantic_io.seconds_to_time(offset),
            stop=semantic_io.seconds_to_time(stop_sec),
            comments=self.comments_edit.toPlainText() or None,
            events=events_abs,
        )
        semantic_io.upsert_row(xlsx_path, row)
        QMessageBox.information(self, "Saved", f"Saved to {xlsx_path}")
