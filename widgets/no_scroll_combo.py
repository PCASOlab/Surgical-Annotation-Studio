"""
widgets/no_scroll_combo.py

Qt's default QComboBox changes its selected value on mouse-wheel scroll
whenever the cursor happens to be over it -- even without clicking to open
it first. That's an easy way to silently change a scoring value by
accident while scrolling past it (e.g. scrolling a panel that contains
dropdowns). This subclass ignores wheel events entirely so scrolling
passes through to whatever's underneath (e.g. the panel's QScrollArea)
instead of changing the combo's value. Click the dropdown open as usual
to change it -- only incidental wheel-scroll-while-hovering is blocked.

Use this in place of QComboBox everywhere in the app.
"""
from __future__ import annotations
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QComboBox


class NoScrollComboBox(QComboBox):
    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        event.ignore()
