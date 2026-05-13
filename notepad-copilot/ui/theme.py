"""Global look-and-feel: Fluent-inspired light theme (clean & modern)."""
from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication


# ---- palette ---------------------------------------------------------------
BG_WINDOW   = "#f4f6f9"   # main window background
BG_SURFACE  = "#ffffff"   # pane / card surface
BG_SUBTLE   = "#fafbfc"   # subtle surface (toolbars, headers)
BORDER      = "#e1e5ea"   # soft borders
BORDER_HARD = "#d0d7de"   # divider / splitter handles

TEXT_PRIMARY   = "#1f2328"
TEXT_SECONDARY = "#656d76"
TEXT_MUTED     = "#8b949e"

ACCENT         = "#0078d4"   # Fluent blue
ACCENT_HOVER   = "#106ebe"
ACCENT_PRESSED = "#005a9e"
ACCENT_SOFT    = "#e6f1fb"

WARN   = "#bf6900"
WARN_BG = "#fff4e5"

OK_BG  = "#e9f7ee"
OK_FG  = "#1a7f37"


def _qss() -> str:
    return f"""
/* ================== Base ================== */
QWidget {{
    background: {BG_WINDOW};
    color: {TEXT_PRIMARY};
    font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 9pt;
}}

QMainWindow, QDialog {{
    background: {BG_WINDOW};
}}

/* ================== Menu / Status bar ================== */
QMenuBar {{
    background: {BG_SURFACE};
    border-bottom: 1px solid {BORDER};
    padding: 2px 4px;
}}
QMenuBar::item {{
    padding: 4px 10px;
    background: transparent;
    border-radius: 4px;
}}
QMenuBar::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}

QMenu {{
    background: {BG_SURFACE};
    border: 1px solid {BORDER};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 22px 6px 22px;
    border-radius: 4px;
}}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}

QStatusBar {{
    background: {BG_SURFACE};
    border-top: 1px solid {BORDER};
    color: {TEXT_SECONDARY};
}}
QStatusBar::item {{ border: none; }}

/* ================== Splitter ================== */
QSplitter::handle {{
    background: {BG_WINDOW};
}}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical   {{ height: 6px; }}
QSplitter::handle:hover {{ background: {ACCENT_SOFT}; }}

/* ================== Inputs ================== */
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser {{
    background: {BG_SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: {ACCENT};
    selection-color: white;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QTextBrowser:focus {{
    border-color: {ACCENT};
}}
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {{
    background: {BG_SUBTLE};
    color: {TEXT_MUTED};
}}

/* ================== Buttons ================== */
QPushButton {{
    background: {BG_SURFACE};
    border: 1px solid {BORDER_HARD};
    border-radius: 6px;
    padding: 5px 14px;
    color: {TEXT_PRIMARY};
    min-height: 18px;
}}
QPushButton:hover  {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton:pressed {{ background: #d6e8f8; }}
QPushButton:disabled {{
    background: {BG_SUBTLE};
    color: {TEXT_MUTED};
    border-color: {BORDER};
}}

QPushButton[accent="true"] {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: white;
    font-weight: 600;
}}
QPushButton[accent="true"]:hover  {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[accent="true"]:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton[accent="true"]:disabled {{
    background: #b9d6ee; border-color: #b9d6ee; color: #f0f4f8;
}}

QPushButton[danger="true"]:hover {{
    background: #fdecea; border-color: #d1242f; color: #d1242f;
}}

/* ================== ComboBox ================== */
QComboBox {{
    background: {BG_SURFACE};
    border: 1px solid {BORDER_HARD};
    border-radius: 6px;
    padding: 4px 8px;
    min-height: 20px;
}}
QComboBox:hover {{ border-color: {ACCENT}; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_SECONDARY};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background: {BG_SURFACE};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT_SOFT};
    selection-color: {ACCENT};
    outline: 0;
    padding: 2px;
}}

/* ================== Progress bar ================== */
QProgressBar {{
    background: {BG_SUBTLE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 5px;
}}

/* ================== Scrollbar (slim) ================== */
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #c8ced6; border-radius: 4px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: #9aa4b1; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

QScrollBar:horizontal {{
    background: transparent; height: 10px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: #c8ced6; border-radius: 4px; min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: #9aa4b1; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ height: 0; }}

/* ================== Tooltip ================== */
QToolTip {{
    background: #24292e;
    color: white;
    border: none;
    padding: 5px 8px;
    border-radius: 4px;
}}

/* ================== Pane / card wrapper (objectName-based) ================== */
QWidget#Card {{
    background: {BG_SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}

QWidget#PaneHeader {{
    background: {BG_SUBTLE};
    border-bottom: 1px solid {BORDER};
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}}

QLabel#PaneTitle {{
    color: {TEXT_PRIMARY};
    font-weight: 600;
    font-size: 9.5pt;
    padding: 2px 0;
}}

QLabel#StatusPill {{
    color: {TEXT_SECONDARY};
    background: {BG_SUBTLE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 2px 10px;
    font-size: 8.5pt;
}}
QLabel#StatusPill[state="busy"] {{
    color: {WARN}; background: {WARN_BG}; border-color: #f3d9b1;
}}
QLabel#StatusPill[state="ok"] {{
    color: {OK_FG}; background: {OK_BG}; border-color: #b8e0c2;
}}

QLabel#BusyLabel {{
    color: {WARN}; font-weight: 600; padding: 0 6px;
}}

QLabel#FieldLabel {{
    color: {TEXT_SECONDARY}; padding-right: 2px;
}}

/* Editors inside cards: drop their own border (the card already has one) */
QWidget#Card > QTextEdit,
QWidget#Card > QPlainTextEdit,
QWidget#Card > QTextBrowser {{
    border: none;
    border-radius: 0;
}}

/* Top toolbar (az bar) */
QWidget#AzBar {{
    background: {BG_SURFACE};
    border-bottom: 1px solid {BORDER};
}}
"""


def apply_theme(app: QApplication) -> None:
    """Install global font + stylesheet on the QApplication."""
    font = QFont("Segoe UI", 9)
    app.setFont(font)
    app.setStyleSheet(_qss())
