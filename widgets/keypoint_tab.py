"""
widgets/keypoint_tab.py

Frame-by-frame keypoint labeling for DeepLabCut training data. Click to
place the 5 needle points (tip, b1, b2, b3, swage) in order, optionally
also 2 tool-tip points if "Track tools too" is enabled in the project
config. Saving a frame:
  1. writes the frame as a PNG under labeled-data/<video_folder>/imgNNN.png
     (standard DLC layout, video_folder name encodes case/stitch/time range
     the same way the existing CollectedData_master.csv rows do), and
  2. upserts that frame's coordinates into
     labeled-data/CollectedData_<scorer>.csv in DLC's native 3-row-header
     multi-scorer format.

The resulting labeled-data/ folder + CollectedData csv can be opened
directly by DeepLabCut's own labeling GUI / training pipeline, or merged
into master-shivank-2025-12-28 if the lab points DLC at this project's
config.yaml (bodyparts list is generated to match).
"""
from __future__ import annotations
import cv2
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QListWidget, QListWidgetItem, QMessageBox, QSplitter,
    QLineEdit, QDoubleSpinBox,
)
from widgets.no_scroll_combo import NoScrollComboBox
from PySide6.QtCore import Qt

from core.config import NEEDLE_BODYPARTS, TOOL_BODYPARTS, DEFAULT_SCORER
from core.project import ProjectManager
from io_utils import dlc_io
from widgets.video_widget import VideoPlayerWidget, ms_to_timecode
from widgets.keypoint_canvas import KeypointCanvas


def _tc_compact(sec: float) -> str:
    total = int(round(sec))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}-{m:02d}-{s:02d}"


class KeypointTab(QWidget):
    def __init__(self, pm: ProjectManager, parent=None):
        super().__init__(parent)
        self.pm = pm
        self._current_case: str | None = None
        self._current_stitch_path: Path | None = None
        self._build_ui()
        self.refresh_cases()

    # -- UI ------------------------------------------------------------
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

        top.addWidget(QLabel("Scorer:"))
        self.scorer_edit = QLineEdit(self.pm.config.scorer or DEFAULT_SCORER)
        self.scorer_edit.setMaximumWidth(100)
        top.addWidget(self.scorer_edit)

        self.track_tools_check = QCheckBox("Track tools too")
        self.track_tools_check.setChecked(self.pm.config.track_tools)
        self.track_tools_check.toggled.connect(self._on_track_tools_toggled)
        top.addWidget(self.track_tools_check)
        top.addStretch(1)
        layout.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_l = QVBoxLayout(left)
        self.canvas = KeypointCanvas()
        self._apply_bodyparts()
        self.canvas.keypointsChanged.connect(self._autosave_current_frame)
        left_l.addWidget(self.canvas, stretch=1)

        self.player = VideoPlayerWidget(min_height=120, show_zoom_controls=False)
        self.player.set_display_visible(False)
        self.player.frameChanged.connect(self._on_frame_changed)
        left_l.addWidget(self.player)

        btn_row = QHBoxLayout()
        btn_save = QPushButton("\U0001f4be Force re-save")
        btn_save.clicked.connect(self._save_current_frame)
        btn_row.addWidget(btn_save)
        btn_clear = QPushButton("Clear points")
        btn_clear.clicked.connect(self.canvas.clear_points)
        btn_row.addWidget(btn_clear)

        btn_row.addWidget(QLabel("Zoom:"))
        btn_zoom_out = QPushButton("\u2212")
        btn_zoom_out.setMaximumWidth(28)
        btn_zoom_out.clicked.connect(self.canvas.zoom_out)
        btn_row.addWidget(btn_zoom_out)
        self.zoom_spin = QDoubleSpinBox()
        self.zoom_spin.setRange(0.25, 8.0)
        self.zoom_spin.setSingleStep(0.25)
        self.zoom_spin.setValue(1.0)
        self.zoom_spin.setSuffix("x")
        self.zoom_spin.valueChanged.connect(lambda v: self.canvas.set_zoom(v))
        self.canvas.zoomChanged.connect(self._on_canvas_zoom_changed)
        btn_row.addWidget(self.zoom_spin)
        btn_zoom_in = QPushButton("+")
        btn_zoom_in.setMaximumWidth(28)
        btn_zoom_in.clicked.connect(self.canvas.zoom_in)
        btn_row.addWidget(btn_zoom_in)
        btn_fit = QPushButton("Fit")
        btn_fit.clicked.connect(self.canvas.reset_zoom)
        btn_row.addWidget(btn_fit)

        self.status_label = QLabel("")
        btn_row.addWidget(self.status_label)
        btn_row.addStretch(1)
        left_l.addLayout(btn_row)
        splitter.addWidget(left)

        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.addWidget(QLabel("Labeled frames (this stitch):"))
        self.labeled_list = QListWidget()
        self.labeled_list.itemDoubleClicked.connect(self._jump_to_labeled_frame)
        right_l.addWidget(self.labeled_list)
        help_label = QLabel(
            "Click to place the next point (order shown below the video). "
            "Drag a point to adjust it. Right-click a point to delete it. "
            "Middle-click drag to pan; Ctrl+scroll or the Zoom controls to zoom. "
            "Points save automatically as you place/adjust/delete them -- "
            "\"Force re-save\" is only there for peace of mind.\n\n"
            "Bodypart order:\n" + "\n".join(f"  {i+1}. {bp}" for i, bp in
                                             enumerate(self._active_bodyparts()))
        )
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: #888;")
        right_l.addWidget(help_label)
        right_l.addStretch(1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter, stretch=1)

    def _active_bodyparts(self) -> list[str]:
        bps = list(self.pm.config.needle_bodyparts or NEEDLE_BODYPARTS)
        if self.track_tools_check.isChecked():
            bps += list(self.pm.config.tool_bodyparts or TOOL_BODYPARTS)
        return bps

    def _apply_bodyparts(self) -> None:
        self.canvas.set_bodyparts(self._active_bodyparts())

    def _on_canvas_zoom_changed(self, zoom: float) -> None:
        self.zoom_spin.blockSignals(True)
        self.zoom_spin.setValue(zoom)
        self.zoom_spin.blockSignals(False)

    def _on_track_tools_toggled(self, checked: bool) -> None:
        self.pm.config.track_tools = checked
        self._apply_bodyparts()

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
        self._current_stitch_path = path
        self.player.load(path)
        self._refresh_labeled_list()

    # -- naming helpers -----------------------------------------------------
    def _video_folder_name(self) -> str:
        assert self._current_case and self.stitch_combo.currentText()
        stitch_id = self.stitch_combo.currentText()
        meta = self.pm.get_stitch_meta(self._current_case, stitch_id)
        if meta is None:
            return f"{self._current_case}_{stitch_id}"
        return (f"{self._current_case}_{stitch_id}_"
                f"{_tc_compact(meta.start_sec_in_source)}_to_{_tc_compact(meta.end_sec_in_source)}")

    def _image_name(self, frame_idx: int) -> str:
        nframes = max(self.player.nframes, 1)
        width = max(3, len(str(nframes - 1)))
        return f"img{frame_idx:0{width}d}.png"

    def _csv_path(self) -> Path:
        scorer = self.scorer_edit.text().strip() or DEFAULT_SCORER
        return dlc_io.collected_data_path(self.pm.paths.labeled_data, scorer)

    # -- frame sync -----------------------------------------------------------
    def _on_frame_changed(self, idx: int) -> None:
        frame = self.player.current_frame_bgr()
        if frame is None:
            # Transient decode hiccups happen occasionally right after a
            # seek (seen on macOS's AVFoundation backend) -- retry once
            # before giving up.
            frame = self.player.current_frame_bgr()
        if frame is None:
            self.canvas.clear_frame()
            self.status_label.setText(f"Frame {idx}: could not read this frame from the video.")
            return
        self.canvas.set_frame(frame)
        # load existing keypoints for this frame if already labeled
        scorer = self.scorer_edit.text().strip() or DEFAULT_SCORER
        csv_path = self._csv_path()
        _, data = dlc_io.read_collected_data(csv_path)
        key = (self._video_folder_name(), self._image_name(idx))
        existing = data.get(key)
        if existing:
            self.canvas.set_points(existing)
            self.status_label.setText(f"Frame {idx}: previously labeled ({scorer})")
        else:
            self.canvas.clear_points()
            self.status_label.setText(f"Frame {idx}: not yet labeled")

    def _write_current_frame(self) -> tuple[bool, int, int]:
        """Writes the current canvas points for the current frame to disk.
        Returns (wrote_something, n_placed, n_total). Writes nothing (and
        returns False) if there's nothing placed and no pre-existing saved
        entry for this frame -- so scrubbing through unlabeled frames never
        creates empty rows."""
        if self._current_stitch_path is None:
            return False, 0, 0
        points = self.canvas.points()
        n_total = len(points)
        n_placed = sum(1 for v in points.values() if v is not None)
        folder = self._video_folder_name()
        image_name = self._image_name(self.player.current_frame)
        csv_path = self._csv_path()
        _, existing_data = dlc_io.read_collected_data(csv_path)
        already_has_entry = (folder, image_name) in existing_data
        if n_placed == 0 and not already_has_entry:
            return False, n_placed, n_total

        img_dir = self.pm.paths.labeled_data / folder
        img_dir.mkdir(parents=True, exist_ok=True)
        img_path = img_dir / image_name
        if not img_path.exists():
            frame = self.player.current_frame_bgr()
            if frame is not None:
                cv2.imwrite(str(img_path), frame)

        scorer = self.scorer_edit.text().strip() or DEFAULT_SCORER
        dlc_io.upsert_frame(
            csv_path, scorer, self._active_bodyparts(), folder, image_name, points
        )
        self._refresh_labeled_list()
        return True, n_placed, n_total

    def _autosave_current_frame(self) -> None:
        """Called automatically on every keypoint edit (place/drag/delete/
        clear) so work is never lost from forgetting to click Save."""
        wrote, n_placed, n_total = self._write_current_frame()
        if wrote:
            self.status_label.setText(
                f"Frame {self.player.current_frame}: auto-saved ({n_placed}/{n_total} pts)"
            )

    def _save_current_frame(self) -> None:
        """Manual "Save frame keypoints" button. Saving happens
        automatically as you edit (see _autosave_current_frame) -- this is
        just an explicit confirmation/force-save for peace of mind."""
        if self._current_stitch_path is None:
            QMessageBox.warning(self, "No clip loaded", "Select a case and stitch clip first.")
            return
        wrote, n_placed, n_total = self._write_current_frame()
        if not wrote:
            QMessageBox.warning(self, "Nothing to save", "Place at least one keypoint first.")
            return
        self.status_label.setText(
            f"Saved frame {self.player.current_frame} ({n_placed}/{n_total} points) \u2713"
        )

    def _refresh_labeled_list(self) -> None:
        self.labeled_list.clear()
        csv_path = self._csv_path()
        _, data = dlc_io.read_collected_data(csv_path)
        folder = self._video_folder_name()
        for (f, image), kp in sorted(data.items()):
            if f != folder:
                continue
            n = sum(1 for v in kp.values() if v is not None)
            item = QListWidgetItem(f"{image}  ({n}/{len(kp)} pts)")
            item.setData(Qt.ItemDataRole.UserRole, image)
            self.labeled_list.addItem(item)

    def _jump_to_labeled_frame(self, item: QListWidgetItem) -> None:
        image_name = item.data(Qt.ItemDataRole.UserRole)
        # image name format img<digits>.png -> frame index
        digits = "".join(ch for ch in image_name if ch.isdigit())
        if digits:
            self.player.seek_frame(int(digits))
