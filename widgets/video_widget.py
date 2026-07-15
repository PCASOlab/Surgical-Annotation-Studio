"""
widgets/video_widget.py

A frame-accurate video player built directly on OpenCV (cv2.VideoCapture)
rather than QMediaPlayer. QMediaPlayer's seek is backend-dependent and on
Linux (GStreamer) commonly snaps to the nearest keyframe, which is not
precise enough for millisecond-level semantic-state annotation or exact
frame keypoint labeling. Decoding frame-by-frame with OpenCV and painting
into a QLabel is slower for scrubbing through long video but gives exact,
reproducible frame indices -- which is the actual requirement here.

Zoom: the frame is displayed inside a QScrollArea. At zoom == 1.0 the
frame is scaled to fit the viewport (same behavior as before); zooming in
scales beyond the viewport and the QScrollArea provides native scrollbars
(plus Ctrl+wheel and a zoom spinbox) for panning.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal, QSize
from PySide6.QtGui import QImage, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton,
    QSpinBox, QSizePolicy, QDoubleSpinBox, QScrollArea,
)

MIN_ZOOM = 0.25
MAX_ZOOM = 8.0
DEFAULT_VIDEO_MIN_HEIGHT = 480


def ms_to_timecode(ms: float) -> str:
    total_ms = int(round(ms))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms_ = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms_:03d}"


class VideoPlayerWidget(QWidget):
    frameChanged = Signal(int)         # current frame index
    positionMsChanged = Signal(float)  # current position in ms

    def __init__(self, parent=None, min_height: int = DEFAULT_VIDEO_MIN_HEIGHT,
                 show_zoom_controls: bool = True):
        super().__init__(parent)
        self._cap: Optional[cv2.VideoCapture] = None
        self._path: Optional[Path] = None
        self._fps: float = 30.0
        self._nframes: int = 0
        self._cur_frame: int = 0
        self._playing = False
        self._last_frame_rgb: Optional[np.ndarray] = None
        self._zoom: float = 1.0
        self._min_height = min_height
        self._show_zoom_controls = show_zoom_controls

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)

        self._build_ui()

    # -- UI ---------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.video_label = QLabel("No video loaded")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background-color: #111; color: #888;")

        self.scroll = QScrollArea()
        self.scroll.setWidget(self.video_label)
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(self._min_height)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.scroll.viewport().installEventFilter(self)
        layout.addWidget(self.scroll, stretch=1)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self.seek_frame)
        layout.addWidget(self.slider)

        controls = QHBoxLayout()
        self.btn_play = QPushButton("\u25b6 Play")
        self.btn_play.clicked.connect(self.toggle_play)
        controls.addWidget(self.btn_play)

        for label, delta in [("-10f", -10), ("-1f", -1), ("+1f", 1), ("+10f", 10)]:
            b = QPushButton(label)
            b.clicked.connect(lambda _, d=delta: self.step(d))
            controls.addWidget(b)

        controls.addWidget(QLabel("Frame:"))
        self.frame_spin = QSpinBox()
        self.frame_spin.setRange(0, 0)
        self.frame_spin.valueChanged.connect(self._on_spin_changed)
        controls.addWidget(self.frame_spin)

        controls.addWidget(QLabel("Rate:"))
        self.rate_spin = QDoubleSpinBox()
        self.rate_spin.setRange(0.1, 4.0)
        self.rate_spin.setSingleStep(0.1)
        self.rate_spin.setValue(1.0)
        controls.addWidget(self.rate_spin)

        if self._show_zoom_controls:
            zoom_label = QLabel("Zoom:")
            controls.addWidget(zoom_label)
            btn_zoom_out = QPushButton("\u2212")
            btn_zoom_out.setMaximumWidth(28)
            btn_zoom_out.clicked.connect(lambda: self.set_zoom(self._zoom / 1.25))
            controls.addWidget(btn_zoom_out)
            self.zoom_spin = QDoubleSpinBox()
            self.zoom_spin.setRange(MIN_ZOOM, MAX_ZOOM)
            self.zoom_spin.setSingleStep(0.25)
            self.zoom_spin.setValue(1.0)
            self.zoom_spin.setSuffix("x")
            self.zoom_spin.valueChanged.connect(self.set_zoom)
            controls.addWidget(self.zoom_spin)
            btn_zoom_in = QPushButton("+")
            btn_zoom_in.setMaximumWidth(28)
            btn_zoom_in.clicked.connect(lambda: self.set_zoom(self._zoom * 1.25))
            controls.addWidget(btn_zoom_in)
            btn_fit = QPushButton("Fit")
            btn_fit.clicked.connect(lambda: self.set_zoom(1.0))
            controls.addWidget(btn_fit)
            # Grouped so callers can also hide them at runtime if needed.
            self._zoom_control_widgets = [zoom_label, btn_zoom_out, self.zoom_spin, btn_zoom_in, btn_fit]
        else:
            self.zoom_spin = None
            self._zoom_control_widgets = []

        self.time_label = QLabel("00:00:00.000")
        self.time_label.setStyleSheet("font-family: monospace; font-size: 13px;")
        controls.addWidget(self.time_label)
        controls.addStretch(1)
        layout.addLayout(controls)

    # -- loading ------------------------------------------------------
    def load(self, path: Path) -> bool:
        if self._cap is not None:
            self._cap.release()
        self._cap = cv2.VideoCapture(str(path))
        if not self._cap.isOpened():
            self.video_label.setText(f"Failed to open:\n{path}")
            self._cap = None
            return False
        self._path = path
        self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._nframes = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.slider.setRange(0, max(0, self._nframes - 1))
        self.frame_spin.setRange(0, max(0, self._nframes - 1))
        self.seek_frame(0)
        return True

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def nframes(self) -> int:
        return self._nframes

    @property
    def current_frame(self) -> int:
        return self._cur_frame

    def current_ms(self) -> float:
        return (self._cur_frame / self._fps) * 1000.0 if self._fps else 0.0

    def current_frame_bgr(self) -> Optional[np.ndarray]:
        """Grabs the currently displayed frame as a raw BGR ndarray (for
        DLC keypoint frame export)."""
        if self._cap is None:
            return None
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, self._cur_frame)
        ok, frame = self._cap.read()
        return frame if ok else None

    def set_display_visible(self, visible: bool) -> None:
        """Hide/show the built-in video display area (controls stay
        visible). Used by tabs that render the frame themselves elsewhere
        (e.g. the Keypoint tab's KeypointCanvas) and only want this
        widget's scrub/play controls. Also hides this widget's own zoom
        controls when the display is hidden, since they'd otherwise zoom a
        display nobody can see."""
        self.scroll.setVisible(visible)
        if not visible:
            self.set_zoom_controls_visible(False)

    def set_zoom_controls_visible(self, visible: bool) -> None:
        """Show/hide this widget's own Zoom +/-/spinbox/Fit controls.
        Ctrl+scroll-wheel zoom on the display still works regardless --
        this only affects the explicit control row, for contexts where
        another widget (e.g. KeypointCanvas) provides its own zoom UI
        instead."""
        for w in self._zoom_control_widgets:
            w.setVisible(visible)

    # -- zoom -----------------------------------------------------------
    def set_zoom(self, zoom: float) -> None:
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        if abs(zoom - self._zoom) < 1e-6:
            return
        self._zoom = zoom
        if self.zoom_spin is not None:
            self.zoom_spin.blockSignals(True)
            self.zoom_spin.setValue(zoom)
            self.zoom_spin.blockSignals(False)
        # zoom==1.0 ("Fit") lets the scroll area auto-resize the label to
        # the viewport, matching pre-zoom behavior exactly; any other zoom
        # switches to a fixed label size (in image pixels * zoom) so the
        # scroll area's native scrollbars can pan around it.
        self.scroll.setWidgetResizable(zoom == 1.0)
        if self._last_frame_rgb is not None:
            self._display_rgb(self._last_frame_rgb)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if obj is self.scroll.viewport() and event.type() == event.Type.Wheel:
            wheel: QWheelEvent = event
            if wheel.modifiers() & Qt.KeyboardModifier.ControlModifier:
                factor = 1.25 if wheel.angleDelta().y() > 0 else 1 / 1.25
                self.set_zoom(self._zoom * factor)
                return True
        return super().eventFilter(obj, event)

    # -- playback control ----------------------------------------------
    def seek_frame(self, idx: int) -> None:
        if self._cap is None:
            return
        idx = max(0, min(idx, self._nframes - 1)) if self._nframes else max(0, idx)
        self._cur_frame = idx
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = self._cap.read()
        if ok:
            self._display(frame)
        self.slider.blockSignals(True)
        self.slider.setValue(idx)
        self.slider.blockSignals(False)
        self.frame_spin.blockSignals(True)
        self.frame_spin.setValue(idx)
        self.frame_spin.blockSignals(False)
        self.time_label.setText(ms_to_timecode(self.current_ms()))
        self.frameChanged.emit(idx)
        self.positionMsChanged.emit(self.current_ms())

    def seek_ms(self, ms: float) -> None:
        frame_idx = int(round((ms / 1000.0) * self._fps))
        self.seek_frame(frame_idx)

    def step(self, delta_frames: int) -> None:
        self.seek_frame(self._cur_frame + delta_frames)

    def toggle_play(self) -> None:
        if self._playing:
            self.pause()
        else:
            self.play()

    def play(self) -> None:
        if self._cap is None:
            return
        self._playing = True
        self.btn_play.setText("\u23f8 Pause")
        interval_ms = max(1, int(1000.0 / (self._fps * self.rate_spin.value())))
        self._timer.start(interval_ms)

    def pause(self) -> None:
        self._playing = False
        self.btn_play.setText("\u25b6 Play")
        self._timer.stop()

    def _on_tick(self) -> None:
        if self._cur_frame >= self._nframes - 1:
            self.pause()
            return
        self.step(1)

    def _on_spin_changed(self, val: int) -> None:
        if val != self._cur_frame:
            self.seek_frame(val)

    # -- rendering ------------------------------------------------------
    def _display(self, frame_bgr: np.ndarray) -> None:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        self._last_frame_rgb = rgb
        self._display_rgb(rgb)

    def _display_rgb(self, rgb: np.ndarray) -> None:
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(qimg)
        if self._zoom == 1.0:
            # fit-to-viewport, same as the original behavior
            target = self.scroll.viewport().size()
            pix = pix.scaled(target, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
            self.video_label.setPixmap(pix)
        else:
            viewport = self.scroll.viewport().size()
            base_scale = min(viewport.width() / w, viewport.height() / h) if w and h else 1.0
            scale = max(base_scale * self._zoom, 0.01)
            target = QSize(max(1, int(w * scale)), max(1, int(h * scale)))
            pix = pix.scaled(target, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
            self.video_label.setFixedSize(pix.size())
            self.video_label.setPixmap(pix)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        if self._last_frame_rgb is not None and self._zoom == 1.0:
            self._display_rgb(self._last_frame_rgb)
