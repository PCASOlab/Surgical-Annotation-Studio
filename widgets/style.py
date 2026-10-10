"""
widgets/style.py

App-wide look: a light palette + a small stylesheet on top of the OS's
native widget style (several widgets use hard-coded greys that assume a
light background). Per-widget setStyleSheet() calls still take precedence
over this.

Buttons for a screen's main action can be marked with mark_primary() to
get the accent color.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QAbstractButton, QApplication

ACCENT = "#2f6fd1"
ACCENT_HOVER = "#3b7be0"
ACCENT_BORDER = "#2a62b8"
WINDOW_BG = "#f4f5f7"
BORDER = "#c9ced6"
SOFT_BORDER = "#d9dde3"

STYLESHEET = f"""
QGroupBox {{
    background: #fbfbfc;
    border: 1px solid {SOFT_BORDER};
    border-radius: 6px;
    margin-top: 14px;
    padding: 10px 6px 6px 6px;
    font-weight: bold;  /* title only: stylesheet fonts don't reach children */
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #2b3440;
}}

QPushButton {{
    background: #ffffff;
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 8px;
}}
QPushButton:hover {{ background: #eef3fb; border-color: #9fb6dc; }}
QPushButton:pressed {{ background: #dde7f7; }}
QPushButton:disabled {{ color: #a0a6ae; background: #f3f4f6; border-color: #e1e4e8; }}
QPushButton[primary="true"] {{
    background: {ACCENT};
    color: white;
    border-color: {ACCENT_BORDER};
    font-weight: bold;
}}
QPushButton[primary="true"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[primary="true"]:pressed {{ background: {ACCENT_BORDER}; }}
QPushButton[primary="true"]:disabled {{
    background: #b3c8ea;
    color: #f2f6fc;
    border-color: #b3c8ea;
}}

QLineEdit, QPlainTextEdit, QTextEdit, QListWidget {{
    background: white;
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 3px 5px;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QListWidget:focus {{
    border-color: {ACCENT};
}}

QTabWidget::pane {{
    border: 1px solid {SOFT_BORDER};
    border-radius: 4px;
    top: -1px;
    background: {WINDOW_BG};
}}
QTabBar::tab {{
    background: #e6e9ee;
    color: #4a5462;
    border: 1px solid {SOFT_BORDER};
    border-bottom: none;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    padding: 7px 16px;
    margin-right: 2px;
}}
/* no bold here: a wider selected tab makes QTabBar show scroll arrows */
QTabBar::tab:selected {{ background: {WINDOW_BG}; color: #1f2933; border-top: 2px solid {ACCENT}; }}
QTabBar::tab:hover:!selected {{ background: #eef3fb; }}

QTableView {{
    background: white;
    gridline-color: #e6e8ec;
    border: 1px solid {SOFT_BORDER};
    border-radius: 4px;
}}
QHeaderView::section {{
    background: #eef1f5;
    color: #2b3440;
    font-weight: bold;
    padding: 4px 6px;
    border: none;
    border-right: 1px solid {SOFT_BORDER};
    border-bottom: 1px solid {SOFT_BORDER};
}}

QToolTip {{
    background: #2b3440;
    color: white;
    border: none;
    padding: 4px 6px;
}}
"""


def _light_palette() -> QPalette:
    pal = QPalette()
    colors = {
        QPalette.ColorRole.Window: WINDOW_BG,
        QPalette.ColorRole.WindowText: "#1f2933",
        QPalette.ColorRole.Base: "#ffffff",
        QPalette.ColorRole.AlternateBase: "#f6f8fb",
        QPalette.ColorRole.Text: "#1f2933",
        QPalette.ColorRole.Button: "#ffffff",
        QPalette.ColorRole.ButtonText: "#1f2933",
        QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.ToolTipBase: "#2b3440",
        QPalette.ColorRole.ToolTipText: "#ffffff",
        QPalette.ColorRole.PlaceholderText: "#9aa1ab",
        QPalette.ColorRole.Link: ACCENT,
    }
    for role, color in colors.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor("#a0a6ae"))
    return pal


def apply_app_style(app: QApplication) -> None:
    app.setPalette(_light_palette())
    app.setStyleSheet(STYLESHEET)


def mark_primary(button: QAbstractButton) -> QAbstractButton:
    """Give a screen's main action the accent color."""
    button.setProperty("primary", True)
    return button
