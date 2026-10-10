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

The clip list (in/out points + per-stitch scores) is saved to
pose/<case_id>/_clip_list_draft.json on every add/remove, so a crash, a
failed cut or closing the app doesn't lose it. It's restored when that
case is selected again and deleted once its clips are cut successfully.
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

        btn_standardize = QPushButton("Standardize \u2192 pose/<case_id>/_full_standardized.mp4")
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
        self.btn_add_clip = QPushButton("\u2795 Add clip (Enter)")
        self.btn_add_clip.clicked.connect(self._commit_clip)
        btn_row.addWidget(self.btn_add_clip)
        self.btn_update_clip = QPushButton("\u2714 Update")
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

        self.clip_table = QTableWidget(0, 6)
        self.clip_table.setHorizontalHeaderLabels(
            ["Stitch ID", "Type", "Start", "End", "Duration", "Scores"]
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
        score_l.addWidget(self.rater_edit)

        score_l.addWidget(self._section_label("Case-level"))
        self.case_form = QFormLayout()
        score_l.addLayout(self.case_form)
        btn_save_case = QPushButton("Save case-level scores")
        btn_save_case.clicked.connect(self._save_case_scores)
        score_l.addWidget(btn_save_case)
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

    def _sync_case_type_to_project(self) -> None:
        case_id = self.case_id_edit.text().strip()
        if case_id:
            self.pm.set_case_type(case_id, self._current_case_type())

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

    def _on_clip_type_changed(self, clip_type: str) -> None:
        enabled = clip_type == "stitch"
        for combo in self.stitch_combos.values():
            combo.setEnabled(enabled)
        self._maybe_prefill_stitch_id(clip_type)

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
            shutil.copy2(src, dst)
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
        self.btn_cut_all.setEnabled(bool(self._pending_clips) and not editing)
        self.btn_cut_all.setToolTip("Finish or cancel the current edit first." if editing else "")

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
            color = TIMELINE_EDITING if clip is self._editing_clip else CLIP_HIGHLIGHT
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
        }
        if editing is not None:
            editing.update(clip)  # in place: keeps identity for a running cut
            self._log(f"Updated '{stitch_id}'.")
            self._editing_clip = None
            next_form = self._stashed_new_form or self._blank_form_state()
            self._stashed_new_form = None
        else:
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
        self.clip_table.setItem(row, 0, QTableWidgetItem(clip["stitch_id"]))
        self.clip_table.setItem(row, 1, QTableWidgetItem(clip["clip_type"]))
        self.clip_table.setItem(row, 2, QTableWidgetItem(ms_to_timecode(clip["start_ms"])))
        self.clip_table.setItem(row, 3, QTableWidgetItem(ms_to_timecode(clip["end_ms"])))
        dur = (clip["end_ms"] - clip["start_ms"]) / 1000.0
        self.clip_table.setItem(row, 4, QTableWidgetItem(f"{dur:.2f}s"))
        scores_summary = ", ".join(f"{k}={v}" for k, v in clip["scores"].items())
        self.clip_table.setItem(row, 5, QTableWidgetItem(scores_summary))

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
                # keep text dark on the yellow highlight in dark themes too
                item.setForeground(QColor("#000") if row in active else QBrush())
        if newly_active:
            item = self.clip_table.item(min(newly_active), 0)
            if item is not None:
                self.clip_table.scrollToItem(item)

    def _remove_selected_clip(self) -> None:
        rows = self._selected_clip_rows()
        if any(self._pending_clips[r] is self._editing_clip for r in rows):
            self._cancel_edit()
        for r in reversed(rows):
            self.clip_table.removeRow(r)
            del self._pending_clips[r]
        if rows:
            self._save_draft()
            self._refresh_clip_highlight()
            self._update_timeline()
            self._update_buttons()

    # -- clip-list draft (survives crashes / failed cuts / restarts) ---------
    def _draft_path(self, case_id: str) -> Path:
        return self.pm.paths.pose / case_id / CLIP_LIST_DRAFT_FILENAME

    def _save_draft(self) -> None:
        case_id = self._draft_case_id
        if not case_id:
            return
        path = self._draft_path(case_id)
        try:
            if not self._pending_clips:
                path.unlink(missing_ok=True)
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "case_id": case_id,
                "rater": self.rater_edit.text().strip(),
                "clips": self._pending_clips,
            }
            # write-then-rename so a crash mid-write never corrupts the draft
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError as e:
            self._log(f"Warning: could not save clip list draft ({e})")

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
        if self._draft_case_id and self._pending_clips:
            self._log(f"Clip list for {self._draft_case_id} kept "
                      f"({len(self._pending_clips)} clip(s)); select that case to continue it.")
        self._draft_case_id = case_id
        clips, rater = self._load_draft(case_id) if case_id else ([], "")
        self._set_pending_clips(clips)
        if clips:
            if rater and not self.rater_edit.text().strip():
                self.rater_edit.setText(rater)
            self._log(f"Restored {len(clips)} unsaved clip(s) for {case_id}.")

    def _cut_all(self) -> None:
        case_id = self.case_id_edit.text().strip()
        if not case_id:
            QMessageBox.warning(self, "Missing case ID", "Enter a case ID first.")
            return
        if not self._pending_clips:
            QMessageBox.information(self, "Nothing to cut", "Add at least one clip to the list.")
            return
        if self._editing_clip is not None:
            QMessageBox.warning(self, "Edit in progress",
                                f"Update or cancel the edit of '{self._editing_clip['stitch_id']}' "
                                f"before cutting.")
            return
        unscored = [c["stitch_id"] for c in self._pending_clips
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

        clips = list(self._pending_clips)
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
        rater = self.rater_edit.text().strip()
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
            self.pm.register_stitch(meta)

            scores = clip.get("scores") or {}
            if clip["clip_type"] == "stitch" and scores:
                self._append_score_values(
                    case_id, clip["case_type"], rater, saved_stitch_id, scores,
                    start=clip["start_ms"] / 1000.0, stop=clip["end_ms"] / 1000.0,
                )
        self.pm.set_case_type(case_id, self._current_case_type())
        self._log(f"Done. {len(results)} clip(s) written to pose/{case_id}/ and registered "
                  f"(scores saved to clinical/score_entries_{self._current_case_type()}.csv "
                  f"for stitch clips).")
        # Drop only the clips that were cut -- any added while the cut was
        # running stay in the list and in the draft.
        if case_id == self._draft_case_id:
            cut = {id(clip) for clip, _, _ in results}
            self._set_pending_clips([c for c in self._pending_clips if id(c) not in cut])
            self._save_draft()

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

    # -- case-level scoring ---------------------------------------------------
    def _save_case_scores(self) -> None:
        case_id = self.case_id_edit.text().strip()
        if not case_id:
            QMessageBox.warning(self, "Missing case ID", "Enter a case ID first.")
            return
        case_type = self._current_case_type()
        rubric = config.RUBRICS[case_type]
        rater = self.rater_edit.text().strip()
        saved = []
        values: dict[str, int] = {}
        for scale_name, combo in self.case_combos.items():
            value = _combo_value(combo)
            if value is None:
                continue  # left blank -- nothing entered, nothing to save
            values[scale_name] = value
            saved.append(f"{scale_name}={value}")

        if not saved:
            self.case_score_status.setText("Nothing selected -- pick at least one subitem's score first.")
            return

        self._append_score_values(case_id, case_type, rater, "", values)
        self.pm.set_case_type(case_id, case_type)
        self.case_score_status.setText(
            f"Saved for {case_id} ({CASE_TYPE_LABELS.get(case_type, case_type)}): "
            f"{', '.join(saved)} \u2713 (appended to clinical/score_entries.csv)"
        )
        # Reset every dropdown back to blank so it's obvious nothing has
        # been entered yet for the next case/review pass.
        for combo in self.case_combos.values():
            combo.setCurrentIndex(0)

    def _log(self, msg: str) -> None:
        self.log_view.appendPlainText(msg)
