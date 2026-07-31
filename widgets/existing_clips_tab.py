"""
widgets/existing_clips_tab.py

For clips that are already cut and sitting in pose/<case_id>/*.mp4 --
e.g. a PEH dataset where every stitch has already been isolated -- and
just need clinical scores assigned, without re-running the Preprocessing
tab's import/cut workflow. Laid out like the Semantic States tab: video
at the top, scoring controls below.

Workflow for a resident:
  1. Pick a Case and a clip (Stitch ID). Case type is auto-selected if
     already known for that case (ProjectManager.get_case_type), or set
     manually the first time.
  2. Watch the video (top).
  3. Fill in case-level scores (once per case) and/or this clip's
     stitch-level scores (below) -- which fields show up depends on the
     Case type, exactly like the Preprocessing tab's scoring panel.
  4. "Load existing scores" pulls back whatever's already saved for this
     case/clip/rater so it can be reviewed or changed; saving again
     replaces the old value in place (see io_utils.clinical_io's upsert
     semantics) rather than creating a duplicate row.

Switching case/clip automatically saves whatever was already filled in
first (same safety-net idea as the Semantic tab's autosave), so nothing
is lost from forgetting to click Save before moving to the next clip.

Each case's scores are saved to their own file --
clinical/score_entries_<case_id>.csv -- reviewable in the Clinical tab.
"""
from __future__ import annotations
from collections import Counter
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QLineEdit,
    QPushButton, QFormLayout, QScrollArea,
)
from widgets.no_scroll_combo import NoScrollComboBox

from core import config
from core.config import CASE_TYPES, CASE_TYPE_LABELS, DEFAULT_CASE_TYPE
from core.project import ProjectManager
from io_utils import clinical_io
from widgets.video_widget import VideoPlayerWidget
from widgets.preprocessing_tab import _make_scale_combo, _combo_value, _clear_layout


class ExistingClipsTab(QWidget):
    def __init__(self, pm: ProjectManager, parent=None):
        super().__init__(parent)
        self.pm = pm
        self._loaded_case: Optional[str] = None
        self._loaded_stitch: Optional[str] = None
        self.case_combos: dict = {}
        self.stitch_combos: dict = {}
        self._build_ui()
        self.refresh_cases()

    # -- UI -----------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Case:"))
        self.case_combo = NoScrollComboBox()
        self.case_combo.currentTextChanged.connect(self._on_case_changed)
        top.addWidget(self.case_combo)
        top.addWidget(QLabel("Stitch/clip:"))
        self.stitch_combo = NoScrollComboBox()
        self.stitch_combo.currentTextChanged.connect(self._on_stitch_changed)
        top.addWidget(self.stitch_combo)
        top.addWidget(QLabel("Case type:"))
        self.case_type_combo = NoScrollComboBox()
        for ct in CASE_TYPES:
            self.case_type_combo.addItem(CASE_TYPE_LABELS.get(ct, ct), userData=ct)
        self.case_type_combo.currentIndexChanged.connect(self._on_case_type_changed)
        top.addWidget(self.case_type_combo)
        top.addWidget(QLabel("Rater:"))
        self.rater_edit = QLineEdit()
        self.rater_edit.setMaximumWidth(120)
        top.addWidget(self.rater_edit)
        top.addStretch(1)
        layout.addLayout(top)

        self.player = VideoPlayerWidget()
        layout.addWidget(self.player, stretch=2)

        panels = QHBoxLayout()

        case_box = QGroupBox("Case-level scores (once per case)")
        case_l = QVBoxLayout(case_box)
        case_scroll = QScrollArea()
        case_scroll.setWidgetResizable(True)
        case_inner = QWidget()
        self.case_form = QFormLayout(case_inner)
        case_scroll.setWidget(case_inner)
        case_l.addWidget(case_scroll)
        case_btn_row = QHBoxLayout()
        btn_load_case = QPushButton("Load existing case-level scores")
        btn_load_case.clicked.connect(self._load_case_scores)
        case_btn_row.addWidget(btn_load_case)
        btn_save_case = QPushButton("Save case-level scores")
        btn_save_case.clicked.connect(self._save_case_scores)
        case_btn_row.addWidget(btn_save_case)
        case_l.addLayout(case_btn_row)
        panels.addWidget(case_box, stretch=1)

        stitch_box = QGroupBox("Scores for this clip")
        stitch_l = QVBoxLayout(stitch_box)
        stitch_scroll = QScrollArea()
        stitch_scroll.setWidgetResizable(True)
        stitch_inner = QWidget()
        self.stitch_form = QFormLayout(stitch_inner)
        stitch_scroll.setWidget(stitch_inner)
        stitch_l.addWidget(stitch_scroll)
        stitch_btn_row = QHBoxLayout()
        btn_load_stitch = QPushButton("Load existing scores for this clip")
        btn_load_stitch.clicked.connect(self._load_stitch_scores)
        stitch_btn_row.addWidget(btn_load_stitch)
        btn_save_stitch = QPushButton("Save scores for this clip")
        btn_save_stitch.clicked.connect(self._save_stitch_scores)
        stitch_btn_row.addWidget(btn_save_stitch)
        stitch_l.addLayout(stitch_btn_row)
        panels.addWidget(stitch_box, stretch=1)

        layout.addLayout(panels, stretch=1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self._rebuild_score_panels()

    # -- case type (rubric) ------------------------------------------------
    def _current_case_type(self) -> str:
        data = self.case_type_combo.currentData()
        return data if data else DEFAULT_CASE_TYPE

    def _on_case_type_changed(self, _index: int) -> None:
        self._rebuild_score_panels()
        if self._loaded_case:
            self.pm.set_case_type(self._loaded_case, self._current_case_type())

    def _rebuild_score_panels(self) -> None:
        case_type = self._current_case_type()
        rubric = config.RUBRICS[case_type]
        case_keys = config.case_level_scales(case_type)
        stitch_keys = config.stitch_level_scales(case_type)
        group_counts = Counter(rubric[k].group for k in case_keys)

        _clear_layout(self.case_form)
        self.case_combos = {}
        last_group = None
        for scale_name in case_keys:
            d = rubric[scale_name]
            if d.group != last_group:
                if group_counts[d.group] > 1:
                    self.case_form.addRow(self._section_label(f"{d.group} (1-{d.hi})"))
                last_group = d.group
            combo = _make_scale_combo(d)
            self.case_combos[scale_name] = combo
            self.case_form.addRow(f"{d.display_label or scale_name}:", combo)

        _clear_layout(self.stitch_form)
        self.stitch_combos = {}
        for scale_name in stitch_keys:
            d = rubric[scale_name]
            combo = _make_scale_combo(d)
            self.stitch_combos[scale_name] = combo
            self.stitch_form.addRow(f"{d.display_label or scale_name}:", combo)

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: bold; margin-top: 6px;")
        return lbl

    # -- case/clip selection --------------------------------------------------
    def refresh_cases(self) -> None:
        current = self.case_combo.currentText()
        ids = self.pm.list_cases()

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
            self.stitch_combo.clear()
            self.status_label.setText("No cases found under pose/ yet.")

    def _on_case_changed(self, case_id: str) -> None:
        self._autosave_current()
        if not case_id:
            self._loaded_case = None
            self.stitch_combo.clear()
            return

        known_type = self.pm.get_case_type(case_id)
        if known_type:
            idx = self.case_type_combo.findData(known_type)
            if idx >= 0:
                self.case_type_combo.blockSignals(True)
                self.case_type_combo.setCurrentIndex(idx)
                self.case_type_combo.blockSignals(False)
                self._rebuild_score_panels()

        self._loaded_case = case_id
        for combo in self.case_combos.values():
            combo.setCurrentIndex(0)

        self.stitch_combo.blockSignals(True)
        self.stitch_combo.clear()
        self.stitch_combo.addItems([p.stem for p in self.pm.list_stitches(case_id)])
        self.stitch_combo.blockSignals(False)
        self._loaded_stitch = None
        if self.stitch_combo.count():
            self._on_stitch_changed(self.stitch_combo.currentText())
        else:
            self.status_label.setText(f"No clips found under pose/{case_id}/.")

    def _on_stitch_changed(self, stitch_id: str) -> None:
        if not stitch_id or not self._loaded_case:
            return
        if self._loaded_stitch != stitch_id:
            self._autosave_current(keep_case_level=True)
        path = self.pm.paths.pose / self._loaded_case / f"{stitch_id}.mp4"
        if not path.exists():
            return
        self.player.load(path)
        self._loaded_stitch = stitch_id
        for combo in self.stitch_combos.values():
            combo.setCurrentIndex(0)
        self.status_label.setText(f"Loaded '{stitch_id}'.")

    # -- autosave -------------------------------------------------------------
    def _autosave_current(self, keep_case_level: bool = False) -> None:
        """Saves whatever's currently filled in (non-blank) for the case
        we're about to leave, before switching -- upsert semantics make
        this safe to call on every switch without creating duplicates.
        `keep_case_level=True` when only the stitch is changing (case-level
        values shouldn't be re-saved against a case we haven't left)."""
        if self._loaded_case is None:
            return
        case_type = self._current_case_type()
        rater = self.rater_edit.text().strip()
        if not keep_case_level:
            values = {name: v for name, combo in self.case_combos.items()
                      if (v := _combo_value(combo)) is not None}
            if values:
                self._write_scores(self._loaded_case, case_type, rater, "", values)
        if self._loaded_stitch:
            values = {name: v for name, combo in self.stitch_combos.items()
                      if (v := _combo_value(combo)) is not None}
            if values:
                self._write_scores(self._loaded_case, case_type, rater,
                                    self._loaded_stitch, values)

    # -- score read/write -------------------------------------------------------
    def _write_scores(self, case_id: str, case_type: str, rater: str,
                       video_id_annot: str, values: dict[str, int]) -> None:
        rubric = config.RUBRICS.get(case_type, {})
        meta = self.pm.get_stitch_meta(case_id, video_id_annot) if video_id_annot else None
        for scale_name, value in values.items():
            d = rubric.get(scale_name)
            if d is None:
                continue
            clinical_io.append_score_entry(self.pm.paths.clinical, {
                "case_id": case_id,
                "case_type": case_type,
                "video_id_annot": video_id_annot,
                "rater": rater,
                "scale": d.group,
                "item": d.item,
                "hogg_var": scale_name,
                "score": value,
                "start": "" if meta is None else f"{meta.start_sec_in_source:.3f}",
                "stop": "" if meta is None else f"{meta.end_sec_in_source:.3f}",
            })

    def _load_case_scores(self) -> None:
        if not self._loaded_case:
            return
        rater = self.rater_edit.text().strip()
        found = 0
        for scale_name, combo in self.case_combos.items():
            val = clinical_io.get_score_value(
                self.pm.paths.clinical, self._loaded_case, "", scale_name, rater
            )
            if val is None:
                continue
            idx = combo.findData(int(float(val)))
            if idx >= 0:
                combo.setCurrentIndex(idx)
                found += 1
        self.status_label.setText(
            f"Loaded {found} existing case-level score(s) for '{self._loaded_case}' (rater '{rater}')."
        )

    def _save_case_scores(self) -> None:
        if not self._loaded_case:
            return
        case_type = self._current_case_type()
        rater = self.rater_edit.text().strip()
        values = {name: v for name, combo in self.case_combos.items()
                  if (v := _combo_value(combo)) is not None}
        if not values:
            self.status_label.setText("Nothing selected -- pick at least one case-level score first.")
            return
        self._write_scores(self._loaded_case, case_type, rater, "", values)
        self.pm.set_case_type(self._loaded_case, case_type)
        self.status_label.setText(
            f"Saved case-level scores for '{self._loaded_case}': "
            f"{', '.join(f'{k}={v}' for k, v in values.items())}"
        )

    def _load_stitch_scores(self) -> None:
        if not self._loaded_case or not self._loaded_stitch:
            return
        rater = self.rater_edit.text().strip()
        found = 0
        for scale_name, combo in self.stitch_combos.items():
            val = clinical_io.get_score_value(
                self.pm.paths.clinical, self._loaded_case, self._loaded_stitch, scale_name, rater
            )
            if val is None:
                continue
            idx = combo.findData(int(float(val)))
            if idx >= 0:
                combo.setCurrentIndex(idx)
                found += 1
        self.status_label.setText(
            f"Loaded {found} existing score(s) for '{self._loaded_stitch}' (rater '{rater}')."
        )

    def _save_stitch_scores(self) -> None:
        if not self._loaded_case or not self._loaded_stitch:
            return
        case_type = self._current_case_type()
        rater = self.rater_edit.text().strip()
        values = {name: v for name, combo in self.stitch_combos.items()
                  if (v := _combo_value(combo)) is not None}
        if not values:
            self.status_label.setText("Nothing selected -- pick at least one score first.")
            return
        self._write_scores(self._loaded_case, case_type, rater, self._loaded_stitch, values)
        self.status_label.setText(
            f"Saved scores for '{self._loaded_stitch}': "
            f"{', '.join(f'{k}={v}' for k, v in values.items())}"
        )
