"""
widgets/keypoint_canvas.py

A still-frame canvas for placing/dragging the 5 (or more) DLC keypoints.
Click an empty spot to place the next un-placed bodypart in order;
click-drag an existing point to move it; right-click (or Delete while
hovering) removes the nearest point. Coordinates are always tracked and
emitted in *original image pixel space* regardless of how the widget is
scaled/letterboxed on screen -- this is what gets written to
CollectedData_<scorer>.csv, so it must exactly match native video
resolution, not the on-screen widget size.
"""
from __future__ import annotations
from typing import Optional

import numpy as np
from PySide6.QtCore import Qt, QPointF, Signal, QRectF
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QWidget, QSizePolicy

MIN_ZOOM = 0.25
MAX_ZOOM = 8.0

# Distinct colors per bodypart index (needle points get warm colors, tool
# points get cool colors so they're visually distinguishable at a glance).
PALETTE = [
    QColor(255, 60, 60), QColor(255, 160, 40), QColor(255, 230, 40),
    QColor(60, 220, 90), QColor(60, 170, 255), QColor(170, 90, 255),
    QColor(255, 90, 200), QColor(90, 255, 210),
]


def _is_placed(xy: Optional[tuple]) -> bool:
    return xy is not None and xy[0] is not None and xy[1] is not None


class KeypointCanvas(QWidget):
    keypointsChanged = Signal()
    zoomChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

        self._frame_rgb: Optional[np.ndarray] = None
        self._pixmap: Optional[QPixmap] = None
        self._img_w = 0
        self._img_h = 0
        self._bodyparts: list[str] = []
        # bodypart -> (x, y) in *image pixel* coords, or None if unplaced
        self._points: dict[str, Optional[tuple[float, float]]] = {}
        self._drag_bp: Optional[str] = None
        self._point_radius_px = 6

        # zoom (multiplier on top of fit-to-widget scale) + pan (widget-space offset)
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._panning = False
        self._pan_last_pos = QPointF(0.0, 0.0)

    # -- data plumbing ---------------------------------------------------
    def set_bodyparts(self, bodyparts: list[str]) -> None:
        self._bodyparts = list(bodyparts)
        self._points = {bp: self._points.get(bp) for bp in self._bodyparts}
        self.update()

    def set_frame(self, frame_bgr: np.ndarray) -> None:
        import cv2
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB).copy()
        self._frame_rgb = rgb
        h, w, ch = rgb.shape
        self._img_w, self._img_h = w, h
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        self._pixmap = QPixmap.fromImage(qimg)
        self.update()

    def clear_frame(self) -> None:
        """Reset to the no-frame-loaded state (no pixmap, zero dimensions)
        and clear any points, so nothing stale lingers against a frame
        that failed to load (e.g. a transient video-decode hiccup)."""
        self._frame_rgb = None
        self._pixmap = None
        self._img_w = 0
        self._img_h = 0
        self._points = {bp: None for bp in self._bodyparts}
        self.update()

    def set_points(self, points: dict[str, Optional[tuple[float, float]]]) -> None:
        """Load a bodypart->(x, y) mapping. dlc_io returns `(None, None)`
        for an unlabeled bodypart (rather than a bare `None`) when reading
        back from CollectedData_<scorer>.csv -- normalize that here so the
        rest of this class only ever has to handle one "unplaced" sentinel
        (bare None), instead of every consumer (paintEvent, next_unplaced,
        _nearest_bp) needing to separately guard against both forms."""
        def _normalize(xy: Optional[tuple[float, float]]) -> Optional[tuple[float, float]]:
            if xy is None:
                return None
            x, y = xy
            if x is None or y is None:
                return None
            return (x, y)

        self._points = {bp: _normalize(points.get(bp)) for bp in self._bodyparts}
        self.update()

    def points(self) -> dict[str, Optional[tuple[float, float]]]:
        return dict(self._points)

    def clear_points(self) -> None:
        self._points = {bp: None for bp in self._bodyparts}
        self.keypointsChanged.emit()
        self.update()

    def next_unplaced(self) -> Optional[str]:
        for bp in self._bodyparts:
            if not _is_placed(self._points.get(bp)):
                return bp
        return None

    # -- zoom / pan -------------------------------------------------------
    def set_zoom(self, zoom: float, anchor: Optional[QPointF] = None) -> None:
        """Zoom in/out, keeping the image point under `anchor` (widget
        coords) fixed on screen if given -- otherwise zooms on-center."""
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        if abs(zoom - self._zoom) < 1e-6:
            return
        anchor = anchor or QPointF(self.width() / 2, self.height() / 2)
        img_xy = self._widget_to_image(anchor, clamp=True)
        self._zoom = zoom
        if img_xy is not None:
            # after changing zoom, re-derive pan so `img_xy` still maps to `anchor`
            base_scale = self._base_scale()
            scale = base_scale * self._zoom
            target_x = anchor.x() - (img_xy[0] - self._img_w / 2) * scale
            target_y = anchor.y() - (img_xy[1] - self._img_h / 2) * scale
            self._pan = QPointF(target_x - self.width() / 2, target_y - self.height() / 2)
        self.zoomChanged.emit(self._zoom)
        self.update()

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * 1.25)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / 1.25)

    def reset_zoom(self) -> None:
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self.zoomChanged.emit(self._zoom)
        self.update()

    def zoom(self) -> float:
        return self._zoom

    def _base_scale(self) -> float:
        if not self._pixmap or self._img_w == 0 or self._img_h == 0:
            return 1.0
        return min(self.width() / self._img_w, self.height() / self._img_h)

    # -- coordinate mapping (widget <-> image pixel space) ---------------
    def _fit_rect(self) -> QRectF:
        if not self._pixmap or self._img_w == 0 or self._img_h == 0:
            return QRectF(0, 0, self.width(), self.height())
        scale = self._base_scale() * self._zoom
        w = self._img_w * scale
        h = self._img_h * scale
        x = (self.width() - w) / 2 + self._pan.x()
        y = (self.height() - h) / 2 + self._pan.y()
        return QRectF(x, y, w, h)

    def _widget_to_image(self, pos: QPointF, clamp: bool = False) -> Optional[tuple[float, float]]:
        rect = self._fit_rect()
        if rect.width() == 0 or rect.height() == 0:
            return None
        if not clamp and not rect.contains(pos):
            return None
        rel_x = (pos.x() - rect.x()) / rect.width()
        rel_y = (pos.y() - rect.y()) / rect.height()
        if clamp:
            rel_x = min(max(rel_x, 0.0), 1.0)
            rel_y = min(max(rel_y, 0.0), 1.0)
        return rel_x * self._img_w, rel_y * self._img_h

    def _image_to_widget(self, xy: tuple[float, float]) -> QPointF:
        rect = self._fit_rect()
        if not self._img_w or not self._img_h:
            # No frame loaded yet (or a degenerate 0x0 frame) -- there's no
            # meaningful image-space position to map to, so just anchor at
            # the rect origin rather than dividing by zero.
            return QPointF(rect.x(), rect.y())
        x, y = xy
        return QPointF(rect.x() + (x / self._img_w) * rect.width(),
                        rect.y() + (y / self._img_h) * rect.height())

    # -- painting ---------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor(17, 17, 17))
            if self._pixmap is None or not self._img_w or not self._img_h:
                # Nothing loaded yet -- background only. Also guards against
                # ever computing point positions against a 0-width/height
                # image (see _image_to_widget), which previously caused a
                # ZeroDivisionError here; an uncaught exception inside
                # paintEvent leaves Qt's painter in a broken state and can
                # crash the whole app, not just this widget, hence the
                # try/finally below.
                return
            painter.drawPixmap(self._fit_rect(), self._pixmap, QRectF(self._pixmap.rect()))

            for i, bp in enumerate(self._bodyparts):
                xy = self._points.get(bp)
                color = PALETTE[i % len(PALETTE)]
                if not _is_placed(xy):
                    continue
                wp = self._image_to_widget(xy)
                pen = QPen(color, 2)
                painter.setPen(pen)
                painter.setBrush(color)
                painter.drawEllipse(wp, self._point_radius_px, self._point_radius_px)
                painter.setPen(QPen(QColor(255, 255, 255)))
                painter.drawText(wp + QPointF(8, -8), bp)
        finally:
            painter.end()

    # -- mouse interaction --------------------------------------------------
    def _nearest_bp(self, wpos: QPointF, max_dist_px: float = 14.0) -> Optional[str]:
        best_bp, best_d = None, max_dist_px
        for bp, xy in self._points.items():
            if not _is_placed(xy):
                continue
            wp = self._image_to_widget(xy)
            d = (wp - wpos).manhattanLength()
            if d < best_d:
                best_d, best_bp = d, bp
        return best_bp

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_last_pos = pos
            return
        if event.button() == Qt.MouseButton.RightButton:
            bp = self._nearest_bp(pos)
            if bp:
                self._points[bp] = None
                self.keypointsChanged.emit()
                self.update()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        existing = self._nearest_bp(pos)
        if existing:
            self._drag_bp = existing
            return
        target_bp = self.next_unplaced()
        if target_bp is None:
            return
        img_xy = self._widget_to_image(pos)
        if img_xy is None:
            return
        self._points[target_bp] = img_xy
        self._drag_bp = target_bp
        self.keypointsChanged.emit()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._panning:
            delta = event.position() - self._pan_last_pos
            self._pan += delta
            self._pan_last_pos = event.position()
            self.update()
            return
        if self._drag_bp is None:
            return
        img_xy = self._widget_to_image(event.position(), clamp=True)
        if img_xy is None:
            return
        self._points[self._drag_bp] = img_xy
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            return
        if self._drag_bp is not None:
            self._drag_bp = None
            self.keypointsChanged.emit()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.25 if event.angleDelta().y() > 0 else 1 / 1.25
            self.set_zoom(self._zoom * factor, anchor=event.position())
            event.accept()
        else:
            super().wheelEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.update()
