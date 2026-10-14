"""
widgets/preprocessing_tab.py

Three jobs, top to bottom:
  1. Import a full-length case video and standardize it to a consistent
     fps/resolution (matches the 640x360 / LANCZOS convention already used
     for DLC training frames elsewhere in the project).
  2. Scrub the (standardized) case video and cut out individual clips --
      either "stitch" (needle) or "knot_tying" passes -- by setting in/out
      points. Clips are written to pose/<case_id>/<case_id>_<stitch_id>.mp4
      and registered in project_meta.json with their offset into the source
      video's timebase (needed by the Semantic tab) *and* the ORIGINAL
      source video's native resolution/fps (needed by any downstream code
      doing coordinate-space rescaling back to native pixels).
  3. Score each stitch (and the case overall) right here while its video
     is on screen. Which scoring fields appear depends on the selected
     **Case type** (e.g. PJ/Whipple vs PEH) -- see core.config.RUBRICS.
     A case's chosen type is remembered (ProjectManager.set_case_type) so
     re-opening it later shows the same rubric automatically.

The clip list shows every clip of the case: ones already cut (read back
from project_meta.json, with their stitch scores from the score file) and
ones not cut yet. The not-yet-cut part (in/out points + per-stitch scores)
is saved to pose/<case_id>/_clip_list_draft.json on every add/edit/remove,
so a crash, a failed cut or closing the app doesn't lose it. Editing a cut
clip's scores writes them to the score file directly; changing its
start/end marks it for re-cutting.
"""
from __future__ import annotations
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QLineEdit,
    QPushButton, QSpinBox, QFileDialog, QListWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QPlainTextEdit, QComboBox,
    QFormLayout, QLayout, QScrollArea, QAbstractItemView,
)
from widgets.no_scroll_combo import NoScrollComboBox
from widgets.style import mark_primary
from widgets.save_guard import run_load, run_save
from io_utils.safe_io import atomic_write_text

from core import config
from core.config import (
    DEFAULT_TARGET_FPS, DEFAULT_TARGET_WIDTH, DEFAULT_TARGET_HEIGHT,
    CASE_TYPES, CASE_TYPE_LABELS, DEFAULT_CASE_TYPE, CLIP_TYPES,
    DEFAULT_CLIP_TYPE, ScaleDef,
)
from core.project import ProjectManager, StitchMeta
from io_utils import ffmpeg_utils, clinical_io
from widgets.video_widget import VideoPlayerWidget, ms_to_timecode
from widgets.worker import FnWorker

PREPROC_VIDEO_MIN_HEIGHT = 560

# Segment-name prefix prepopulated into the "Stitch ID" field when the
# matching clip type is selected, to save typing. Only applied when the
# field is empty or still holds an unedited prefix (from having switched
# clip type) -- never overwrites something the user actually typed.
CLIP_TYPE_PREFIXES = {"stitch": "stitch_", "knot_tying": "knot_"}

CLIP_LIST_DRAFT_FILENAME = "_clip_list_draft.json"

# Clip highlight shared by the table rows and the timeline bands, so the
# same clip reads the same color in both places.
CLIP_HIGHLIGHT = QColor("#ffe39f")
TIMELINE_EDITING = QColor("#ff8c00")
TIMELINE_UNCUT = QColor(255, 227, 159, 120)  # lighter: clip still to be cut
CUT_ROW_COLOR = QColor("#6b7280")  # grey text for clips that are already cut
# clip["status"]: "new" (never cut), "cut" (clip file matches the list),
# "recut" (cut before, but start/end changed or the file is missing)
CLIP_STATUS_LABELS = {"new": "\u2022 not cut", "cut": "\u2702 cut",
                      "recut": "\u21bb re-cut needed"}
TIMELINE_PENDING = QColor(40, 167, 69, 200)  # green, matches the "set" mark chips

# start/end mark chips: not set / set / invalid (end before start)
_CHIP_BASE = "QPushButton { border-radius: 10px; padding: 3px 12px; %s }"
CHIP_UNSET_STYLE = _CHIP_BASE % "background: transparent; color: #888; border: 1px dashed #aaa;"
CHIP_SET_STYLE = _CHIP_BASE % ("background: #d4edda; color: #155724; border: 1px solid #28a745;"
                               " font-weight: bold;")
CHIP_BAD_STYLE = _CHIP_BASE % ("background: #f8d7da; color: #721c24; border: 1px solid #dc3545;"
                               " font-weight: bold;")
STATUS_HINT_STYLE = "color: #888; padding: 3px;"
STATUS_READY_STYLE = "color: #155724; font-weight: bold; padding: 3px;"
STATUS_BAD_STYLE = "color: #721c24; font-weight: bold; padding: 3px;"
STATUS_EDITING_STYLE = ("background: #fff3cd; color: #000; padding: 3px 6px;"
                        " border-radius: 3px; font-weight: bold;")


def _pose_stitch_filename(case_id: str, stitch_id: str) -> str:
    return f"{case_id}_{stitch_id}.mp4"


def _make_scale_combo(scale_def: ScaleDef) -> QComboBox:
    combo = NoScrollComboBox()
    combo.addItem("", userData=None)  # blank by default -- no value entered yet
    for value, label in scale_def.options():
        combo.addItem(label, userData=value)
    return combo


def _combo_value(combo: QComboBox) -> Optional[int]:
    data = combo.currentData()
    return None if data is None else int(data)


def _clear_layout(layout: QLayout) -> None:
    """Remove and delete every item/widget from `layout` without deleting
    the layout object itself, so it can be reused across score-panel
    rebuilds (e.g. when the Case type selection changes)."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        else:
            sub_layout = item.layout()
            if sub_layout is not None:
                _clear_layout(sub_layout)


class PreprocessingTab(QWidget):
    def __init__(self, pm: ProjectManager, parent=None):
        super().__init__(parent)
        self.pm = pm
        self._pending_clips: list[dict] = []
        self._worker: FnWorker | None = None
        self._source_info_cache: dict[str, ffmpeg_utils.VideoInfo] = {}
        self._last_autofilled_case_id: Optional[str] = None
        # case whose clip list is currently in _pending_clips / the table
        self._draft_case_id: Optional[str] = None
        self.case_combos: dict[str, QComboBox] = {}
        self.stitch_combos: dict[str, QComboBox] = {}
        self._build_ui()
        self._refresh_source_list()
        self._rebuild_score_panels()

    # -- UI ---------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        if not ffmpeg_utils.ffmpeg_available():
            warn = QLabel(
                f"\u26a0 ffmpeg/ffprobe were not found on PATH. {ffmpeg_utils.install_hint()}"
            )
            warn.setStyleSheet("color: #d33; font-weight: bold;")
            warn.setWordWrap(True)
            layout.addWidget(warn)

        # -- import / standardize ------------------------------------
        box1 = QGroupBox("1. Import + standardize case video")
        b1 = QHBoxLayout(box1)
        self.source_list = QListWidget()
        self.source_list.setMaximumWidth(260)
        self.source_list.currentTextChanged.connect(self._on_source_selected)
        b1.addWidget(self.source_list)

        col = QVBoxLayout()
        row = QHBoxLayout()
        btn_import = QPushButton("Import video file\u2026")
        btn_import.clicked.connect(self._import_video)
        row.addWidget(btn_import)
        col.addLayout(row)

        form = QHBoxLayout()
        form.addWidget(QLabel("Case ID:"))
        self.case_id_edit = QLineEdit()
        self.case_id_edit.editingFinished.connect(self._on_case_id_edited)
        form.addWidget(self.case_id_edit)
        form.addWidget(QLabel("Case type:"))
        self.case_type_combo = NoScrollComboBox()
        for ct in CASE_TYPES:
            self.case_type_combo.addItem(CASE_TYPE_LABELS.get(ct, ct), userData=ct)
        self.case_type_combo.currentIndexChanged.connect(self._on_case_type_changed)
        form.addWidget(self.case_type_combo)
        form.addWidget(QLabel("Target FPS:"))
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 120)
        self.fps_spin.setValue(DEFAULT_TARGET_FPS)
        form.addWidget(self.fps_spin)
        form.addWidget(QLabel("W:"))
        self.w_spin = QSpinBox()
        self.w_spin.setRange(64, 4096)
        self.w_spin.setValue(DEFAULT_TARGET_WIDTH)
        form.addWidget(self.w_spin)
        form.addWidget(QLabel("H:"))
        self.h_spin = QSpinBox()
        self.h_spin.setRange(64, 4096)
        self.h_spin.setValue(DEFAULT_TARGET_HEIGHT)
        form.addWidget(self.h_spin)
        col.addLayout(form)

        self.probe_label = QLabel("Select a video to see its properties (native resolution is "
                                   "recorded automatically and kept even after standardizing).")
        self.probe_label.setWordWrap(True)
        self.probe_label.setStyleSheet("color: #888;")
        col.addWidget(self.probe_label)

        btn_standardize = mark_primary(
            QPushButton("Standardize \u2192 pose/<case_id>/_full_standardized.mp4"))
        btn_standardize.clicked.connect(self._standardize)
        col.addWidget(btn_standardize)
        b1.addLayout(col, stretch=1)
        layout.addWidget(box1)

        # -- cut clips + score ---------------------------------------------
        box2 = QGroupBox("2. Scrub, cut into clips, and score")
        b2 = QHBoxLayout(box2)

        left = QVBoxLayout()
        self.player = VideoPlayerWidget(min_height=PREPROC_VIDEO_MIN_HEIGHT)
        self.player.markStartRequested.connect(self._set_start)
        self.player.markEndRequested.connect(self._set_end)
        left.addWidget(self.player, stretch=1)

        cut_row1 = QHBoxLayout()
        cut_row1.addWidget(QLabel("Clip type:"))
        self.clip_type_combo = NoScrollComboBox()
        self.clip_type_combo.addItems(CLIP_TYPES)
        self.clip_type_combo.setCurrentText(DEFAULT_CLIP_TYPE)
        self.clip_type_combo.currentTextChanged.connect(self._on_clip_type_changed)
        cut_row1.addWidget(self.clip_type_combo)
        cut_row1.addWidget(QLabel("Stitch ID:"))
        self.stitch_id_edit = QLineEdit()
        self.stitch_id_edit.setPlaceholderText("e.g. stitch_01 / knot_01")
        self.stitch_id_edit.returnPressed.connect(self._commit_clip)
        cut_row1.addWidget(self.stitch_id_edit)
        self.btn_set_start = QPushButton("Set start (I)")
        self.btn_set_start.clicked.connect(self._set_start)
        cut_row1.addWidget(self.btn_set_start)
        self.start_chip = QPushButton()
        self.start_chip.clicked.connect(lambda: self._seek_to_mark(self._pending_start_ms))
        cut_row1.addWidget(self.start_chip)
        self.btn_set_end = QPushButton("Set end (O)")
        self.btn_set_end.clicked.connect(self._set_end)
        cut_row1.addWidget(self.btn_set_end)
        self.end_chip = QPushButton()
        self.end_chip.clicked.connect(lambda: self._seek_to_mark(self._pending_end_ms))
        cut_row1.addWidget(self.end_chip)
        left.addLayout(cut_row1)

        # always-present status line (next step / ready / editing) -- shown
        # in place rather than as an extra row, so the layout never jumps
        self.mark_status = QLabel("")
        self.mark_status.setWordWrap(True)
        left.addWidget(self.mark_status)

        btn_row = QHBoxLayout()
        self.btn_add_clip = mark_primary(QPushButton("\u2795 Add clip (Enter)"))
        self.btn_add_clip.clicked.connect(self._commit_clip)
        btn_row.addWidget(self.btn_add_clip)
        self.btn_update_clip = mark_primary(QPushButton("\u2714 Update"))
        self.btn_update_clip.clicked.connect(self._commit_clip)
        btn_row.addWidget(self.btn_update_clip)
        self.btn_cancel_edit = QPushButton("Cancel edit")
        self.btn_cancel_edit.clicked.connect(self._cancel_edit)
        btn_row.addWidget(self.btn_cancel_edit)
        btn_row.addSpacing(16)
        self.btn_edit_clip = QPushButton("\u270e Edit selected")
        self.btn_edit_clip.clicked.connect(self._edit_selected_clip)
        btn_row.addWidget(self.btn_edit_clip)
        self.btn_remove_clip = QPushButton("Remove selected")
        self.btn_remove_clip.clicked.connect(self._remove_selected_clip)
        btn_row.addWidget(self.btn_remove_clip)
        btn_row.addStretch(1)
        self.btn_cut_all = QPushButton("\u2702 Cut all clips \u2192 pose/<case_id>/")
        self.btn_cut_all.clicked.connect(self._cut_all)
        btn_row.addWidget(self.btn_cut_all)
        left.addLayout(btn_row)
        QShortcut(QKeySequence("Ctrl+Return"), self, self._commit_clip)

        self.clip_table = QTableWidget(0, 7)
        self.clip_table.setHorizontalHeaderLabels(
            ["Stitch ID", "Status", "Type", "Start", "End", "Duration", "Scores"]
        )
        self.clip_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        # Cells were editable before, but edits never reached _pending_clips
        # (what actually gets cut) -- make that explicit. Double-click seeks.
        self.clip_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.clip_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.clip_table.setToolTip("Double-click a row to jump to its start. "
                                   "Use \"Edit selected\" to change a clip.")
        self.clip_table.cellDoubleClicked.connect(lambda row, _col: self._seek_to_clip_row(row))
        self.clip_table.verticalHeader().sectionDoubleClicked.connect(self._seek_to_clip_row)
        self.player.positionMsChanged.connect(self._sync_clip_highlight_to_playhead)
        self._active_clip_rows: Optional[set[int]] = None  # None = restyle needed
        self.clip_table.itemSelectionChanged.connect(self._update_buttons)
        left.addWidget(self.clip_table)
        b2.addLayout(left, stretch=3)

        # -- scoring panel (visible while scrubbing/cutting) --------------
        score_box = QGroupBox("Scoring (visible while reviewing this video)")
        score_box_outer = QVBoxLayout(score_box)
        score_scroll = QScrollArea()
        score_scroll.setWidgetResizable(True)
        score_inner = QWidget()
        score_l = QVBoxLayout(score_inner)
        score_scroll.setWidget(score_inner)
        score_box_outer.addWidget(score_scroll)

        score_l.addWidget(QLabel("Rater:"))
        self.rater_edit = QLineEdit()
        self.rater_edit.setPlaceholderText("your name or initials -- required for scoring")
        # enable/disable live while typing; (re)load saved scores once the
        # name is complete (focus leaves the field or Enter is pressed)
        self.rater_edit.textChanged.connect(lambda _: self._update_scoring_enabled())
        self.rater_edit.editingFinished.connect(self._refresh_scoring_state)
        score_l.addWidget(self.rater_edit)
        self.scoring_hint = QLabel("")
        self.scoring_hint.setWordWrap(True)
        score_l.addWidget(self.scoring_hint)

        score_l.addWidget(self._section_label("Case-level (saved as you pick)"))
        self.case_form = QFormLayout()
        score_l.addLayout(self.case_form)
        self.case_score_status = QLabel("")
        self.case_score_status.setStyleSheet("color: #888;")
        self.case_score_status.setWordWrap(True)
        score_l.addWidget(self.case_score_status)

        self.stitch_section_label = self._section_label("")
        score_l.addWidget(self.stitch_section_label)
        self.stitch_score_note = QLabel(
            "Captured when the clip is added/updated and only saved "
            "for Clip type = stitch."
        )
        self.stitch_score_note.setWordWrap(True)
        self.stitch_score_note.setStyleSheet("color: #888;")
        score_l.addWidget(self.stitch_score_note)
        self.stitch_form = QFormLayout()
        score_l.addLayout(self.stitch_form)
        score_l.addStretch(1)
        b2.addWidget(score_box, stretch=2)

        layout.addWidget(box2, stretch=1)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(110)
        layout.addWidget(self.log_view)

        self._pending_start_ms: float | None = None
        self._pending_end_ms: float | None = None
        # Edit mode: the clip dict being edited (by identity, so it survives
        # re-sorting) and the half-entered NEW clip it displaced, restored
        # when the edit is finished or cancelled.
        self._editing_clip: Optional[dict] = None
        self._stashed_new_form: Optional[dict] = None
        self._on_clip_type_changed(self.clip_type_combo.currentText())
        self._set_pending_range(None, None)
        self._update_edit_mode_ui()

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: bold; margin-top: 6px;")
        return lbl

    # -- case type (rubric) selection -------------------------------------
    def _current_case_type(self) -> str:
        data = self.case_type_combo.currentData()
        return data if data else DEFAULT_CASE_TYPE

    def _on_case_type_changed(self, _index: int) -> None:
        self._rebuild_score_panels()
        self._sync_case_type_to_project()

    def _rebuild_score_panels(self) -> None:
        """(Re)build the case-level and stitch-level scoring dropdowns for
        whichever Case type is currently selected. Called at startup and
        every time the Case type selection changes."""
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
            combo.currentIndexChanged.connect(
                lambda _, name=scale_name: self._on_case_score_changed(name))
            self.case_combos[scale_name] = combo
            self.case_form.addRow(f"{d.display_label or scale_name}:", combo)

        # keep whatever per-stitch scores were picked (e.g. mid-edit) for
        # scales that also exist in the new rubric
        previous_scores = self._form_scores()
        _clear_layout(self.stitch_form)
        self.stitch_combos = {}
        for scale_name in stitch_keys:
            d = rubric[scale_name]
            combo = _make_scale_combo(d)
            self.stitch_combos[scale_name] = combo
            self.stitch_form.addRow(f"{d.display_label or scale_name}:", combo)
        self._set_form_scores(previous_scores)

        # re-apply the stitch-combo enabled/disabled state for the current clip type
        self._on_clip_type_changed(self.clip_type_combo.currentText())
        self._refresh_scoring_state()

    def _sync_case_type_to_project(self) -> None:
        case_id = self.case_id_edit.text().strip()
        if case_id:
            case_type = self._current_case_type()
            run_save(self, f"The case type of '{case_id}'",
                     lambda: self.pm.set_case_type(case_id, case_type))

    def _apply_known_case_type_for(self, case_id: str) -> None:
        """If `case_id` already has a case type on record (from a previous
        session, or from having just picked a video that matches a known
        case), select it automatically instead of leaving whatever was
        previously selected -- most useful when switching between cases
        that use different rubrics."""
        if not case_id:
            return
        known = self.pm.get_case_type(case_id)
        if not known:
            return
        idx = self.case_type_combo.findData(known)
        if idx >= 0 and idx != self.case_type_combo.currentIndex():
            self.case_type_combo.blockSignals(True)
            self.case_type_combo.setCurrentIndex(idx)
            self.case_type_combo.blockSignals(False)
            self._rebuild_score_panels()

    def _on_case_id_edited(self) -> None:
        case_id = self.case_id_edit.text().strip()
        self._apply_known_case_type_for(case_id)
        self._switch_draft_case(case_id)
        self._refresh_scoring_state()

    def _on_clip_type_changed(self, clip_type: str) -> None:
        self._update_scoring_enabled()
        self._maybe_prefill_stitch_id(clip_type)

    # -- scoring: rater gate + case-level autosave ------------------------------
    def _scoring_ready(self) -> bool:
        return bool(self.rater_edit.text().strip() and self.case_id_edit.text().strip())

    def _update_scoring_enabled(self) -> None:
        """Scoring dropdowns only work once a rater and a case ID are set,
        so no score is ever saved without a rater."""
        ready = self._scoring_ready()
        for combo in self.case_combos.values():
            combo.setEnabled(ready)
        stitch_ok = ready and self.clip_type_combo.currentText() == "stitch"
        for combo in self.stitch_combos.values():
            combo.setEnabled(stitch_ok)
        if not self.rater_edit.text().strip():
            self.scoring_hint.setText("\u26a0 Enter your rater name to enable scoring.")
            self.scoring_hint.setStyleSheet("color: #b45309; font-weight: bold;")
        elif not self.case_id_edit.text().strip():
            self.scoring_hint.setText("\u26a0 Enter a case ID to enable scoring.")
            self.scoring_hint.setStyleSheet("color: #b45309; font-weight: bold;")
        else:
            self.scoring_hint.setText("Case-level scores save automatically as you pick them.")
            self.scoring_hint.setStyleSheet("color: #888;")

    def _refresh_scoring_state(self) -> None:
        """Enable/disable the dropdowns and show the case-level scores already
        saved for this case + rater (blank where nothing is saved)."""
        self._update_scoring_enabled()
        case_id = self.case_id_edit.text().strip()
        rater = self.rater_edit.text().strip()

        def read_saved() -> dict[str, int]:
            saved = {}
            for scale_name in self.case_combos:
                value = clinical_io.get_score_value(
                    self.pm.paths.clinical, case_id, "", scale_name, rater)
                if value is not None:
                    saved[scale_name] = int(float(value))
            return saved
        saved = (run_load(self, f"Saved scores for '{case_id}'", read_saved, default={})
                 if case_id and rater else {})
        for scale_name, combo in self.case_combos.items():
            value = saved.get(scale_name)
            idx = combo.findData(value) if value is not None else 0
            combo.blockSignals(True)  # loading, not editing -- don't write back
            combo.setCurrentIndex(max(idx, 0))
            combo.blockSignals(False)
        self.case_score_status.setText("")
        if self._editing_clip is None:
            self._reload_cut_scores()

    def _on_case_score_changed(self, scale_name: str) -> None:
        if not self._scoring_ready():
            return
        case_id = self.case_id_edit.text().strip()
        rater = self.rater_edit.text().strip()
        case_type = self._current_case_type()
        value = _combo_value(self.case_combos[scale_name])

        def save() -> None:
            if value is None:
                clinical_io.remove_score_entry(self.pm.paths.clinical, case_id, "",
                                               scale_name, rater)
            else:
                self._append_score_values(case_id, case_type, rater, "", {scale_name: value})
                self.pm.set_case_type(case_id, case_type)
        if not run_save(self, f"Score '{scale_name}'", save):
            self.case_score_status.setText(
                f"\u2717 {scale_name} was NOT saved -- pick the value again to retry.")
            self.case_score_status.setStyleSheet("color: #b91c1c; font-weight: bold;")
            return
        msg = f"{scale_name} cleared" if value is None else f"{scale_name}={value} saved"
        self.case_score_status.setText(
            f"\u2713 {msg} for {case_id} / {rater} "
            f"(clinical/score_entries_{case_id}.csv)")
        self.case_score_status.setStyleSheet("color: #888;")

    def _maybe_prefill_stitch_id(self, clip_type: str) -> None:
        """Prepopulate the Stitch ID field with a prefix matching the
        selected clip type (stitch_/knot_), saving some typing. Only
        touches the field if it's empty or still holds an unedited prefix
        (e.g. left over from switching clip type) -- never overwrites
        something the user actually typed."""
        prefix = CLIP_TYPE_PREFIXES.get(clip_type, "")
        if not prefix:
            return
        current = self.stitch_id_edit.text()
        known_prefixes = set(CLIP_TYPE_PREFIXES.values())
        if not current or current in known_prefixes:
            self.stitch_id_edit.setText(prefix)
            self.stitch_id_edit.setCursorPosition(len(prefix))

    # -- source video management -------------------------------------------
    def _refresh_source_list(self) -> None:
        self.source_list.clear()
        for p in self.pm.list_source_videos():
            self.source_list.addItem(p.name)

    def _import_video(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Import case video", str(Path.home()),
            "Video files (*.mp4 *.mov *.avi *.mkv *.m4v)"
        )
        if not path_str:
            return
        src = Path(path_str)
        self.pm.paths.videos.mkdir(parents=True, exist_ok=True)
        dst = self.pm.paths.videos / src.name
        if dst.resolve() != src.resolve():
            if not run_save(self, f"Importing {src.name}", lambda: shutil.copy2(src, dst)):
                return
        self._refresh_source_list()
        self._log(f"Imported {src.name}")
        # select the newly-imported video so Case ID/preview update to it
        # right away, rather than leaving whatever was previously selected
        items = self.source_list.findItems(src.name, Qt.MatchFlag.MatchExactly)
        if items:
            self.source_list.setCurrentItem(items[0])

    def _on_source_selected(self, name: str) -> None:
        if not name:
            return
        path = self.pm.paths.videos / name
        if not path.exists():
            return
        if ffmpeg_utils.ffmpeg_available():
            try:
                info = ffmpeg_utils.probe_video(path)
                self._source_info_cache[name] = info
                self.probe_label.setText(
                    f"Native/original resolution: {info.width}x{info.height} @ {info.fps:.2f} fps, "
                    f"{info.duration_sec:.1f}s, {info.nframes} frames, codec={info.codec}. "
                    f"This original resolution is recorded for every clip cut from it, "
                    f"even after standardizing."
                )
            except Exception as e:  # noqa: BLE001
                self.probe_label.setText(f"probe failed: {e}")

        # Prefill Case ID from the video's filename -- but only overwrite
        # if the field is empty or still holds whatever we last
        # auto-filled it with, never something the user typed themselves.
        # (Previously this only fired once ever, the first time the field
        # was empty -- so importing/selecting a second video silently kept
        # showing the first video's case ID.)
        current = self.case_id_edit.text()
        if not current or current == self._last_autofilled_case_id:
            self.case_id_edit.setText(path.stem)
            self._last_autofilled_case_id = path.stem
        self._apply_known_case_type_for(self.case_id_edit.text().strip())
        self._switch_draft_case(self.case_id_edit.text().strip())
        self._refresh_scoring_state()

        # also load it into the player for cutting (uses the *original*
        # if not yet standardized -- see _standardize's on-finish reload)
        self.player.load(path)

    def _standardize(self) -> None:
        name = self.source_list.currentItem().text() if self.source_list.currentItem() else None
        if not name:
            QMessageBox.warning(self, "No video selected", "Select an imported video first.")
            return
        case_id = self.case_id_edit.text().strip()
        if not case_id:
            QMessageBox.warning(self, "Missing case ID", "Enter a case ID before standardizing.")
            return
        src = self.pm.paths.videos / name
        dst_dir = self.pm.paths.pose / case_id
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / "_full_standardized.mp4"

        self._log(f"Standardizing {name} \u2192 {dst} ...")
        self._worker = FnWorker(
            ffmpeg_utils.standardize_video, src, dst,
            target_fps=self.fps_spin.value(), target_width=self.w_spin.value(),
            target_height=self.h_spin.value(),
        )
        self._worker.log.connect(self._log)
        self._worker.finished_ok.connect(lambda _: self._on_standardize_done(dst))
        self._worker.failed.connect(lambda msg: self._log(f"FAILED: {msg}"))
        self._worker.start()

    def _on_standardize_done(self, dst: Path) -> None:
        self._log(f"Standardize complete: {dst}")
        self.player.load(dst)

    # -- clip cutting -------------------------------------------------------
    def _set_start(self) -> None:
        self._set_pending_range(self.player.current_ms(), self._pending_end_ms)

    def _set_end(self) -> None:
        self._set_pending_range(self._pending_start_ms, self.player.current_ms())

    def _set_pending_range(self, start_ms: Optional[float], end_ms: Optional[float]) -> None:
        self._pending_start_ms = start_ms
        self._pending_end_ms = end_ms
        bad = start_ms is not None and end_ms is not None and end_ms <= start_ms
        for chip, label, ms in ((self.start_chip, "Start", start_ms),
                                (self.end_chip, "End", end_ms)):
            if ms is None:
                chip.setText(f"{label}: not set")
                chip.setStyleSheet(CHIP_UNSET_STYLE)
                chip.setToolTip("")
            else:
                mark = "\u2717" if bad else "\u2713"
                chip.setText(f"{label} {mark} {ms_to_timecode(ms)}")
                chip.setStyleSheet(CHIP_BAD_STYLE if bad else CHIP_SET_STYLE)
                chip.setToolTip("Click to jump here")
        self._update_mark_status()
        self._update_timeline()
        self._update_buttons()

    def _seek_to_mark(self, ms: Optional[float]) -> None:
        if ms is not None:
            self.player.seek_ms(ms)

    def _update_mark_status(self) -> None:
        """One line telling the user what's set and what to do next."""
        a, b = self._pending_start_ms, self._pending_end_ms
        if a is None and b is None:
            text, style = "\u2460 Scrub to where the clip starts and press I (Set start).", STATUS_HINT_STYLE
        elif a is None:
            text, style = "\u2460 Now mark the start: press I (Set start).", STATUS_HINT_STYLE
        elif b is None:
            text, style = "\u2461 Now scrub to the end of the clip and press O (Set end).", STATUS_HINT_STYLE
        elif b <= a:
            text, style = ("\u2717 End is before start -- move the playhead and set start or "
                           "end again."), STATUS_BAD_STYLE
        else:
            text = (f"\u2714 {(b - a) / 1000.0:.2f} s clip marked -- enter the stitch ID and "
                    f"scores, then press Enter (or Add clip).")
            style = STATUS_READY_STYLE
        if self._editing_clip is not None:
            sid = self._editing_clip["stitch_id"]
            dur = f" ({(b - a) / 1000.0:.2f} s)" if a is not None and b is not None and b > a else ""
            if self._was_cut(self._editing_clip):
                text = (f"\u270e Editing cut clip '{sid}'{dur}: changed scores are saved "
                        f"to the score file on Update; changing start/end marks it to be "
                        f"cut again with Cut all. Cancel edit discards.")
            else:
                text = (f"\u270e Editing '{sid}'{dur}: changes apply to this clip only -- "
                        f"Update to save or Cancel edit to discard. "
                        f"The new clip you were entering comes back afterwards.")
            if b is not None and a is not None and b <= a:
                text += "  \u2717 End is before start."
            style = STATUS_EDITING_STYLE
        self.mark_status.setText(text)
        self.mark_status.setStyleSheet(style)

    def _update_buttons(self) -> None:
        editing = self._editing_clip is not None
        a, b = self._pending_start_ms, self._pending_end_ms
        range_ok = a is not None and b is not None and b > a
        n_selected = len(self._selected_clip_rows())
        self.btn_add_clip.setEnabled(not editing and range_ok)
        self.btn_add_clip.setToolTip(
            "Finish or cancel the current edit first." if editing else
            "" if range_ok else "Set a start (I) and an end (O) first.")
        self.btn_update_clip.setEnabled(editing and range_ok)
        self.btn_cancel_edit.setEnabled(editing)
        self.btn_edit_clip.setEnabled(n_selected == 1)
        self.btn_remove_clip.setEnabled(n_selected > 0)
        to_cut = any(c.get("status") != "cut" for c in self._pending_clips)
        self.btn_cut_all.setEnabled(to_cut and not editing)
        self.btn_cut_all.setToolTip(
            "Finish or cancel the current edit first." if editing else
            "" if to_cut else "Every clip in the list is already cut.")

    # -- clip form state (shared by "add new" and "edit existing") ----------
    def _form_scores(self) -> dict[str, int]:
        scores = {}
        for name, combo in self.stitch_combos.items():
            value = _combo_value(combo)
            if value is not None:
                scores[name] = value
        return scores

    def _set_form_scores(self, scores: dict[str, int]) -> None:
        for name, combo in self.stitch_combos.items():
            idx = combo.findData(scores.get(name)) if name in scores else 0
            combo.setCurrentIndex(max(idx, 0))

    def _form_state(self) -> dict:
        return {
            "stitch_id": self.stitch_id_edit.text(),
            "clip_type": self.clip_type_combo.currentText(),
            "start_ms": self._pending_start_ms,
            "end_ms": self._pending_end_ms,
            "scores": self._form_scores(),
        }

    def _apply_form_state(self, state: dict) -> None:
        self.clip_type_combo.setCurrentText(state["clip_type"])
        self.stitch_id_edit.setText(state["stitch_id"])
        self._set_form_scores(state["scores"])
        self._set_pending_range(state["start_ms"], state["end_ms"])

    def _blank_form_state(self) -> dict:
        clip_type = self.clip_type_combo.currentText()
        return {"stitch_id": CLIP_TYPE_PREFIXES.get(clip_type, ""), "clip_type": clip_type,
                "start_ms": None, "end_ms": None, "scores": {}}

    # -- edit mode ----------------------------------------------------------
    def _selected_clip_rows(self) -> list[int]:
        return sorted({idx.row() for idx in self.clip_table.selectedIndexes()})

    def _edit_selected_clip(self) -> None:
        rows = self._selected_clip_rows()
        if len(rows) != 1:
            QMessageBox.information(self, "Select one clip", "Select a single clip row to edit.")
            return
        clip = self._pending_clips[rows[0]]
        if self._editing_clip is None:
            self._stashed_new_form = self._form_state()
        # switching straight to another clip drops unsaved changes to the
        # previous one -- the stashed new clip is kept either way
        self._editing_clip = clip
        self._apply_form_state(clip)
        self.player.seek_ms(clip["start_ms"])
        self._update_edit_mode_ui()

    def _cancel_edit(self) -> None:
        if self._editing_clip is None:
            return
        self._editing_clip = None
        self._apply_form_state(self._stashed_new_form or self._blank_form_state())
        self._stashed_new_form = None
        self._update_edit_mode_ui()

    def _update_edit_mode_ui(self) -> None:
        editing = self._editing_clip
        # a clip that exists on disk keeps its ID and type: renaming would
        # mean moving files and score rows
        locked = editing is not None and self._was_cut(editing)
        self.stitch_id_edit.setEnabled(not locked)
        self.clip_type_combo.setEnabled(not locked)
        if editing is None:
            self.btn_update_clip.setText("\u2714 Update")
            self.stitch_section_label.setText("Per-stitch (applies to the NEXT clip you add)")
        else:
            sid = editing["stitch_id"]
            self.btn_update_clip.setText(f"\u2714 Update '{sid}' (Enter)")
            self.stitch_section_label.setText(f"Per-stitch (EDITING '{sid}')")
        self._update_mark_status()
        self._update_timeline()
        self._update_buttons()

    def _update_timeline(self) -> None:
        ranges = []
        for clip in self._pending_clips:
            if clip is self._editing_clip:
                color = TIMELINE_EDITING
            elif clip.get("status") == "cut":
                color = CLIP_HIGHLIGHT
            else:
                color = TIMELINE_UNCUT
            ranges.append((clip["start_ms"], clip["end_ms"], color))
        a, b = self._pending_start_ms, self._pending_end_ms
        if a is not None and b is not None:
            ranges.append((min(a, b), max(a, b), TIMELINE_PENDING))
        elif a is not None or b is not None:
            mark = a if a is not None else b
            ranges.append((mark, mark, TIMELINE_PENDING))
        self.player.set_timeline_ranges(ranges)

    def _commit_clip(self) -> None:
        """Add a new clip, or -- in edit mode -- update the one being edited."""
        case_id = self.case_id_edit.text().strip()
        if not case_id:
            QMessageBox.warning(self, "Missing case ID", "Enter a case ID first.")
            return
        self._switch_draft_case(case_id)
        editing = self._editing_clip
        others = [c for c in self._pending_clips if c is not editing]
        stitch_id = self.stitch_id_edit.text().strip()
        # the case ID is prepended when the clip is saved -- don't double it
        if stitch_id.startswith(f"{case_id}_"):
            stitch_id = stitch_id[len(case_id) + 1:]
        if not stitch_id or stitch_id in CLIP_TYPE_PREFIXES.values():
            QMessageBox.warning(self, "Missing stitch ID",
                                "Enter a full stitch ID (e.g. stitch_01), not just the prefix.")
            return
        if any(c["stitch_id"] == stitch_id for c in others):
            QMessageBox.warning(self, "Duplicate stitch ID",
                                f"'{stitch_id}' is already in the clip list.")
            return
        existing = self.pm.paths.pose / case_id / _pose_stitch_filename(case_id, stitch_id)
        id_unchanged = editing is not None and editing["stitch_id"] == stitch_id
        if existing.exists() and not id_unchanged:
            answer = QMessageBox.question(
                self, "Clip already exists",
                f"{existing.name} was already cut. Cutting again will overwrite it. Continue?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if self._pending_start_ms is None or self._pending_end_ms is None:
            QMessageBox.warning(self, "Missing in/out points", "Set both start and end first.")
            return
        if self._pending_end_ms <= self._pending_start_ms:
            QMessageBox.warning(self, "Invalid range", "End must be after start.")
            return

        clip_type = self.clip_type_combo.currentText()
        start_ms, end_ms = self._pending_start_ms, self._pending_end_ms
        overlapping = [c for c in others if c["clip_type"] == clip_type
                       and c["start_ms"] < end_ms and start_ms < c["end_ms"]]
        if overlapping:
            names = ", ".join(
                f"{c['stitch_id']} ({ms_to_timecode(c['start_ms'])}\u2013{ms_to_timecode(c['end_ms'])})"
                for c in overlapping)
            answer = QMessageBox.question(
                self, "Overlapping clip",
                f"This {clip_type} clip overlaps {names}. Keep it anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        clip = {
            "stitch_id": stitch_id,
            "clip_type": clip_type,
            "case_type": self._current_case_type(),
            "start_ms": start_ms,
            "end_ms": end_ms,
            "scores": self._form_scores() if clip_type == "stitch" else {},
            "rater": self.rater_edit.text().strip(),
        }
        if editing is not None:
            if "cut_start_ms" in editing:
                # cut before: same times -> just a score change, saved now;
                # new times -> needs cutting again
                same_times = (abs(start_ms - editing["cut_start_ms"]) < 0.5
                              and abs(end_ms - editing["cut_end_ms"]) < 0.5)
                clip["status"] = "cut" if same_times else "recut"
            if clip.get("status") == "cut":
                # save before applying, so a failed save leaves the edit open
                updated = {**editing, **clip}
                if not run_save(self, f"Scores for '{stitch_id}'", lambda: self._write_stitch_scores(
                        case_id, updated, editing["cut_start_ms"] / 1000.0,
                        editing["cut_end_ms"] / 1000.0)):
                    return
            editing.update(clip)  # in place: keeps identity for a running cut
            if editing.get("status") == "cut":
                self._log(f"Updated '{stitch_id}' -- scores saved to "
                          f"clinical/score_entries_{case_id}.csv.")
            elif editing.get("status") == "recut":
                self._log(f"Updated '{stitch_id}' -- new start/end, it will be cut again "
                          f"with Cut all.")
            else:
                self._log(f"Updated '{stitch_id}'.")
            self._editing_clip = None
            next_form = self._stashed_new_form or self._blank_form_state()
            self._stashed_new_form = None
        else:
            clip["status"] = "new"
            self._pending_clips.append(clip)
            next_form = self._blank_form_state()
        self._pending_clips.sort(key=lambda c: c["start_ms"])
        self._set_pending_clips(self._pending_clips)
        self._save_draft()
        self._apply_form_state(next_form)
        self._update_edit_mode_ui()

    def _append_table_row(self, clip: dict) -> None:
        row = self.clip_table.rowCount()
        self.clip_table.insertRow(row)
        dur = (clip["end_ms"] - clip["start_ms"]) / 1000.0
        cells = [
            clip["stitch_id"],
            CLIP_STATUS_LABELS.get(clip.get("status", "new"), ""),
            clip["clip_type"],
            ms_to_timecode(clip["start_ms"]),
            ms_to_timecode(clip["end_ms"]),
            f"{dur:.2f}s",
            ", ".join(f"{k}={v}" for k, v in clip["scores"].items()),
        ]
        for col, text in enumerate(cells):
            self.clip_table.setItem(row, col, QTableWidgetItem(text))

    def _set_pending_clips(self, clips: list[dict]) -> None:
        self._pending_clips = list(clips)
        self.clip_table.setRowCount(0)
        for clip in self._pending_clips:
            self._append_table_row(clip)
        self._refresh_clip_highlight()
        self._update_timeline()
        self._update_buttons()

    def _seek_to_clip_row(self, row: int) -> None:
        if 0 <= row < len(self._pending_clips):
            self.player.seek_ms(self._pending_clips[row]["start_ms"])

    def _refresh_clip_highlight(self) -> None:
        self._active_clip_rows = None  # force a restyle
        self._sync_clip_highlight_to_playhead(self.player.current_ms())

    def _sync_clip_highlight_to_playhead(self, ms: float) -> None:
        """Highlight every clip whose [start, end) contains the playhead
        (clips may overlap or be out of order, so this is a range check
        rather than the Semantic tab's "last event before playhead")."""
        active = {r for r, c in enumerate(self._pending_clips)
                  if c["start_ms"] <= ms < c["end_ms"]}
        if active == self._active_clip_rows:
            return  # unchanged -- skip restyling on every frame
        newly_active = active - (self._active_clip_rows or set())
        self._active_clip_rows = active
        active_background = CLIP_HIGHLIGHT
        for row in range(self.clip_table.rowCount()):
            for column in range(self.clip_table.columnCount()):
                item = self.clip_table.item(row, column)
                if item is None:
                    continue
                font = item.font()
                font.setBold(row in active)
                item.setFont(font)
                item.setBackground(active_background if row in active else QBrush())
                # keep text dark on the yellow highlight in dark themes too;
                # otherwise grey for clips that are already cut
                if row in active:
                    item.setForeground(QColor("#000"))
                elif self._pending_clips[row].get("status") == "cut":
                    item.setForeground(CUT_ROW_COLOR)
                else:
                    item.setForeground(QBrush())
        if newly_active:
            item = self.clip_table.item(min(newly_active), 0)
            if item is not None:
                self.clip_table.scrollToItem(item)

    def _remove_selected_clip(self) -> None:
        rows = self._selected_clip_rows()
        cut = [r for r in rows if self._pending_clips[r].get("status") == "cut"]
        if cut:
            names = ", ".join(self._pending_clips[r]["stitch_id"] for r in cut)
            QMessageBox.information(
                self, "Already cut",
                f"{names} {'is' if len(cut) == 1 else 'are'} already cut, so "
                f"{'it stays' if len(cut) == 1 else 'they stay'} in the list. To drop a cut "
                f"clip, delete its file in pose/{self._draft_case_id}/ -- this never "
                f"deletes clip files.")
            rows = [r for r in rows if r not in cut]
        if not rows:
            return
        if any(self._pending_clips[r] is self._editing_clip for r in rows):
            self._cancel_edit()
        for r in reversed(rows):
            del self._pending_clips[r]
        self._save_draft()
        # a removed "re-cut needed" clip falls back to its cut version
        self._reload_clip_list()

    def _was_cut(self, clip: dict) -> bool:
        """True if the clip has a file on disk from an earlier cut."""
        return "cut_start_ms" in clip

    def _saved_id(self, case_id: str, clip: dict) -> str:
        return clip.get("saved_id") or f"{case_id}_{clip['stitch_id']}"

    def _write_stitch_scores(self, case_id: str, clip: dict,
                             start_s: float, stop_s: float) -> None:
        """Write a stitch clip's scores to the score file, and remove rows for
        subitems that were set back to blank."""
        rater = clip.get("rater") or self.rater_edit.text().strip()
        if clip["clip_type"] != "stitch" or not rater:
            return
        saved_id = self._saved_id(case_id, clip)
        self._append_score_values(case_id, clip["case_type"], rater, saved_id,
                                  clip["scores"], start=start_s, stop=stop_s)
        for scale_name in config.stitch_level_scales(clip["case_type"]):
            if scale_name not in clip["scores"]:
                clinical_io.remove_score_entry(self.pm.paths.clinical, case_id,
                                               saved_id, scale_name, rater)

    # -- clips already cut (project_meta.json + score file) --------------------
    def _load_case_clips(self, case_id: str) -> tuple[list[dict], str]:
        """All clips of a case: the saved not-yet-cut list, plus every clip
        that was already cut. A draft entry wins over the cut version of the
        same stitch (it holds pending edits)."""
        draft, rater = self._load_draft(case_id)
        for clip in draft:
            clip.setdefault("status", "new")
        clips = list(draft)
        in_draft = {c["stitch_id"] for c in draft}
        prefix = f"{case_id}_"
        for meta in self.pm.all_stitch_meta():
            if meta.case_id != case_id:
                continue
            sid = meta.stitch_id[len(prefix):] if meta.stitch_id.startswith(prefix) \
                else meta.stitch_id
            if sid in in_draft:
                continue
            start_ms = meta.start_sec_in_source * 1000.0
            end_ms = meta.end_sec_in_source * 1000.0
            clip = {
                "stitch_id": sid,
                "clip_type": meta.clip_type,
                "case_type": self.pm.get_case_type(case_id) or self._current_case_type(),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "scores": {},
                "rater": "",
                "saved_id": meta.stitch_id,
            }
            if (self.pm.paths.root / meta.clip_path).exists():
                clip.update(status="cut", cut_start_ms=start_ms, cut_end_ms=end_ms)
            else:
                clip["status"] = "recut"  # registered, but the file is gone
            clips.append(clip)
        clips.sort(key=lambda c: c["start_ms"])
        return clips, rater

    def _reload_cut_scores(self) -> None:
        """Show the current rater's saved stitch scores for clips that are
        already cut (their scores live in the score file, not the draft)."""
        case_id = self._draft_case_id
        rater = self.rater_edit.text().strip()
        cut = [c for c in self._pending_clips if c.get("status") == "cut"]

        def read_scores() -> list[dict[str, int]]:
            out = []
            for clip in cut:
                scores: dict[str, int] = {}
                if case_id and rater and clip["clip_type"] == "stitch":
                    for scale_name in config.stitch_level_scales(clip["case_type"]):
                        value = clinical_io.get_score_value(
                            self.pm.paths.clinical, case_id,
                            self._saved_id(case_id, clip), scale_name, rater)
                        if value is not None:
                            scores[scale_name] = int(float(value))
                out.append(scores)
            return out
        all_scores = run_load(self, f"Saved stitch scores for '{case_id}'",
                              read_scores, default=[{} for _ in cut]) if cut else []
        for clip, scores in zip(cut, all_scores):
            clip["rater"] = rater
            clip["scores"] = scores
        self._set_pending_clips(self._pending_clips)

    def _reload_clip_list(self) -> None:
        """Rebuild the list from disk, keeping an edit in progress."""
        if not self._draft_case_id:
            return
        editing_id = self._editing_clip["stitch_id"] if self._editing_clip else None
        clips, _ = self._load_case_clips(self._draft_case_id)
        self._set_pending_clips(clips)
        if editing_id is not None:
            self._editing_clip = next(
                (c for c in clips if c["stitch_id"] == editing_id), None)
        self._reload_cut_scores()
        self._update_edit_mode_ui()

    # -- clip-list draft (survives crashes / failed cuts / restarts) ---------
    def _draft_path(self, case_id: str) -> Path:
        return self.pm.paths.pose / case_id / CLIP_LIST_DRAFT_FILENAME

    def _save_draft(self) -> None:
        if self._draft_case_id:
            self._write_draft(self._draft_case_id, self._pending_clips,
                              self.rater_edit.text().strip())

    def _write_draft(self, case_id: str, clips: list[dict], rater: str) -> None:
        """Save the clips that still need cutting (cut ones live on disk)."""
        to_cut = [c for c in clips if c.get("status") != "cut"]
        path = self._draft_path(case_id)

        def save() -> None:
            if not to_cut:
                path.unlink(missing_ok=True)
                return
            data = {"case_id": case_id, "rater": rater, "clips": to_cut}
            atomic_write_text(path, json.dumps(data, indent=2))
        if not run_save(self, f"The clip list of '{case_id}'", save):
            self._log(f"Warning: the clip list of {case_id} was not saved -- it is still "
                      f"on screen; any change to it will try to save again.")

    def _load_draft(self, case_id: str) -> tuple[list[dict], str]:
        path = self._draft_path(case_id)
        if not path.exists():
            return [], ""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return list(data.get("clips", [])), data.get("rater", "")
        except (OSError, ValueError) as e:
            self._log(f"Warning: could not read clip list draft {path} ({e})")
            return [], ""

    def _switch_draft_case(self, case_id: str) -> None:
        """Show the saved clip list for `case_id` (empty if none). The
        previous case's list stays saved under its own ID."""
        case_id = case_id or None
        if case_id == self._draft_case_id:
            return
        self._cancel_edit()
        n_uncut = sum(1 for c in self._pending_clips if c.get("status") != "cut")
        if self._draft_case_id and n_uncut:
            self._log(f"Clip list for {self._draft_case_id} kept "
                      f"({n_uncut} clip(s) not cut yet); select that case to continue it.")
        self._draft_case_id = case_id
        clips, rater = self._load_case_clips(case_id) if case_id else ([], "")
        self._set_pending_clips(clips)
        if rater and not self.rater_edit.text().strip():
            self.rater_edit.setText(rater)
        self._reload_cut_scores()
        n_cut = sum(1 for c in clips if c.get("status") == "cut")
        n_uncut = len(clips) - n_cut
        if clips:
            self._log(f"{case_id}: {n_cut} clip(s) already cut"
                      + (f", {n_uncut} not cut yet (restored)." if n_uncut else "."))

    def _cut_all(self) -> None:
        case_id = self.case_id_edit.text().strip()
        if not case_id:
            QMessageBox.warning(self, "Missing case ID", "Enter a case ID first.")
            return
        to_cut = [c for c in self._pending_clips if c.get("status") != "cut"]
        if not to_cut:
            QMessageBox.information(self, "Nothing to cut",
                                    "Every clip in the list is already cut -- add a new one first.")
            return
        if self._editing_clip is not None:
            QMessageBox.warning(self, "Edit in progress",
                                f"Update or cancel the edit of '{self._editing_clip['stitch_id']}' "
                                f"before cutting.")
            return
        unscored = [c["stitch_id"] for c in to_cut
                    if c["clip_type"] == "stitch" and not c["scores"]]
        if unscored and self.stitch_combos:
            answer = QMessageBox.question(
                self, "Unscored stitches",
                f"No scores entered for: {', '.join(unscored)}. Cut anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        name = self.source_list.currentItem().text() if self.source_list.currentItem() else None
        if not name:
            QMessageBox.warning(self, "No source video", "Select the source video in the list.")
            return
        source_video_path = self.pm.paths.videos / name

        source_info: Optional[ffmpeg_utils.VideoInfo] = self._source_info_cache.get(name)
        if source_info is None and ffmpeg_utils.ffmpeg_available():
            try:
                source_info = ffmpeg_utils.probe_video(source_video_path)
                self._source_info_cache[name] = source_info
            except Exception as e:  # noqa: BLE001
                self._log(f"Warning: could not probe original source resolution ({e})")

        # If already standardized, cut from the standardized copy so every
        # clip inherits the consistent fps/resolution; otherwise cut+resize
        # in one pass per clip.
        std_path = self.pm.paths.pose / case_id / "_full_standardized.mp4"
        cut_source = std_path if std_path.exists() else source_video_path
        already_standardized = std_path.exists()

        clips = to_cut
        dst_dir = self.pm.paths.pose / case_id
        target_fps = None if already_standardized else self.fps_spin.value()
        target_w = None if already_standardized else self.w_spin.value()
        target_h = None if already_standardized else self.h_spin.value()

        def job(on_log=None, on_progress=None):
            results = []
            for i, clip in enumerate(clips):
                dst = dst_dir / _pose_stitch_filename(case_id, clip["stitch_id"])
                ffmpeg_utils.cut_clip(
                    cut_source, dst,
                    start_sec=clip["start_ms"] / 1000.0, end_sec=clip["end_ms"] / 1000.0,
                    target_fps=target_fps, target_width=target_w, target_height=target_h,
                    on_log=on_log,
                )
                info = ffmpeg_utils.probe_video(dst)
                results.append((clip, dst, info))
                if on_progress:
                    on_progress(i + 1, len(clips))
            return results

        self._log(f"Cutting {len(clips)} clip(s) for case {case_id} from {cut_source.name} ...")
        self._worker = FnWorker(job)
        self._worker.log.connect(self._log)
        self._worker.progress.connect(lambda c, t: self._log(f"  progress: {c}/{t}"))
        self._worker.finished_ok.connect(
            lambda results: self._on_cut_done(case_id, source_video_path, source_info, results)
        )
        self._worker.failed.connect(lambda msg: self._log(
            f"FAILED: {msg}\nThe clip list is still saved -- fix the problem and cut again."))
        self._worker.start()

    def _on_cut_done(self, case_id: str, source_video_path: Path,
                      source_info: Optional[ffmpeg_utils.VideoInfo], results: list) -> None:
        current_rater = self.rater_edit.text().strip()
        registered = []
        for clip, dst, info in results:
            saved_stitch_id = f"{case_id}_{clip['stitch_id']}"
            meta = StitchMeta(
                case_id=case_id, stitch_id=saved_stitch_id,
                clip_path=str(dst.relative_to(self.pm.paths.root)),
                source_video=str(source_video_path.relative_to(self.pm.paths.root)),
                start_sec_in_source=clip["start_ms"] / 1000.0,
                end_sec_in_source=clip["end_ms"] / 1000.0,
                fps=info.fps, width=info.width, height=info.height,
                clip_type=clip["clip_type"],
                source_width=source_info.width if source_info else 0,
                source_height=source_info.height if source_info else 0,
                source_fps=source_info.fps if source_info else 0.0,
            )
            if not run_save(self, f"Registering clip '{clip['stitch_id']}'",
                            lambda m=meta: self.pm.register_stitch(m)):
                # stays in the list as not cut; the next Cut all redoes it
                self._log(f"  {clip['stitch_id']}: clip file written but NOT registered "
                          f"-- it stays in the list and will be cut again.")
                continue
            registered.append(clip)

            # the rater who scored the clip (older drafts lack it)
            clip.setdefault("rater", "")
            clip["rater"] = clip["rater"] or current_rater
            clip["saved_id"] = saved_stitch_id
            if not run_save(self, f"Scores for '{clip['stitch_id']}'",
                            lambda c=clip: self._write_stitch_scores(
                                case_id, c, c["start_ms"] / 1000.0, c["end_ms"] / 1000.0)):
                self._log(f"  {clip['stitch_id']}: scores NOT saved -- edit the clip and "
                          f"click Update to save them.")
        case_type = self._current_case_type()
        run_save(self, f"The case type of '{case_id}'",
                 lambda: self.pm.set_case_type(case_id, case_type))
        self._log(f"Done. {len(registered)} of {len(results)} clip(s) written to "
                  f"pose/{case_id}/ and registered "
                  f"(stitch scores saved to clinical/score_entries_{case_id}.csv).")
        # Mark what was cut (matched by ID + times, so a clip edited or added
        # while the cut was running is left alone) and drop it from the draft.
        done = {(c["stitch_id"], c["start_ms"], c["end_ms"]) for c in registered}
        if case_id == self._draft_case_id:
            for clip in self._pending_clips:
                if (clip["stitch_id"], clip["start_ms"], clip["end_ms"]) in done:
                    clip.update(status="cut", cut_start_ms=clip["start_ms"],
                                cut_end_ms=clip["end_ms"],
                                saved_id=f"{case_id}_{clip['stitch_id']}")
            self._save_draft()
            self._set_pending_clips(self._pending_clips)
        else:
            # switched to another case while cutting -- fix up that case's draft
            draft, rater = self._load_draft(case_id)
            self._write_draft(case_id, [c for c in draft if (
                c["stitch_id"], c["start_ms"], c["end_ms"]) not in done], rater)

    # -- score writing (generic: one row per subitem, any case type) -------------
    def _append_score_values(self, case_id: str, case_type: str, rater: str,
                              video_id_annot: str, values: dict[str, int],
                              start: Optional[float] = None, stop: Optional[float] = None) -> None:
        rubric = config.RUBRICS.get(case_type, {})
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
                "start": "" if start is None else f"{start:.3f}",
                "stop": "" if stop is None else f"{stop:.3f}",
            })

    def _log(self, msg: str) -> None:
        self.log_view.appendPlainText(msg)
