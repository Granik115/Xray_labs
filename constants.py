"""
Constants and theme colors taken 1:1 from MolPlayer (C:/projects/MolPlayer/constants.py).
Industrial / molecular transformer dark blue + cyan glowing theme.
Used for X-Ray-lab to match the requested color palette exactly.
Semi-transparent window, Comic Sans preferred, etc.
"""

from PyQt5.QtGui import QColor

# === Theme colors (exact from MolPlayer / molecular transformer reference) ===
ACCENT_FRAME = "#3e80a3"
ACCENT_FRAME_LIGHT = "#4181a7"
ACCENT_GLOW = "#00bfff"
ACCENT_GLOW_TEAL = "#40e0d0"
DEPTH_BLUE = "#215175"

# Very dark blue-tinted backgrounds
BG_DARK = "#0f141b"
BG_SIDEBAR = "#0a111c"
BG_PANEL = "#0a1a2e"
BG_OVERLAY = "#001a33"

# Track / slot / list backgrounds
BG_TRACK = "#12233a"
BG_TRACK_HOVER = "#18304f"
BG_TRACK_SELECTED = "#1e3a5f"

# Text
TEXT_PRIMARY = "#e8f4ff"
TEXT_SECONDARY = "#a8d4f0"
TEXT_MUTED = "#5c7a9a"

# Progress / accents
PROGRESS_BG = "#1a2a47"
PROGRESS_FILL = "#00bfff"

# Buttons (same blue family)
BTN_BG = "#215175"
BTN_HOVER = "#3e80a3"
BTN_PRIMARY = "#3e80a3"
BTN_PRIMARY_HOVER = "#00bfff"

BORDER = "#215175"

COLOR_ERROR = "#ff6b6b"
COLOR_SUCCESS = "#40e0d0"

# Aliases (for convenience)
ACCENT_BLUE = BTN_PRIMARY
ACCENT_CYAN = ACCENT_GLOW

# App info
APP_NAME = "Xray_labs"
APP_DISPLAY_NAME = "X-Ray-lab"
APP_VERSION = "0.0.5"
VERSION = APP_VERSION

# For self-updater (must match GitHub repo)
GITHUB_REPO = "Granik115/Xray_labs"

# Preferred font (user loves Comic Sans MS)
PREFERRED_FONT = "Comic Sans MS"
FALLBACK_FONT = "Segoe UI"

def get_qcolor(hex_str: str) -> QColor:
    return QColor(hex_str)

# Full QSS stylesheet string (applied after loading .ui)
def get_app_stylesheet() -> str:
    return f"""
QMainWindow, QDialog, QWidget {{
    background-color: {BG_DARK};
    color: {TEXT_PRIMARY};
    font-family: "{PREFERRED_FONT}", "{FALLBACK_FONT}", Arial, sans-serif;
    font-size: 11pt;
}}

QFrame#headerFrame {{
    background-color: #0a0e14;
    border: none;
}}

QFrame#contentFrame, QFrame#leftPanel {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 4px;
}}

QLabel#titleLabel, QLabel#labTitleLabel {{
    color: {TEXT_PRIMARY};
    font-family: "{PREFERRED_FONT}", "{FALLBACK_FONT}", Arial;
    font-size: 14pt;
    font-weight: bold;
}}

QLabel#instructionLabel {{
    color: {TEXT_SECONDARY};
    font-size: 10pt;
    padding: 4px;
}}

QPushButton {{
    background-color: {BTN_BG};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 6px 12px;
    font-size: 11pt;
    font-weight: bold;
    min-height: 26px;
}}

QPushButton:hover {{
    background-color: {BTN_HOVER};
}}

QPushButton:pressed {{
    background-color: {DEPTH_BLUE};
}}

QPushButton#smallUpdateBtn {{
    font-size: 10pt;
    min-width: 22px;
    max-width: 28px;
    min-height: 20px;
    max-height: 22px;
    padding: 1px;
    background-color: {BG_TRACK};
}}

QPushButton#smallUpdateBtn:hover {{
    background-color: {ACCENT_FRAME};
}}

QRadioButton {{
    color: {TEXT_PRIMARY};
    spacing: 6px;
}}

QRadioButton::indicator {{
    width: 14px;
    height: 14px;
}}

QLineEdit {{
    background-color: {BG_TRACK_SELECTED};
    color: {TEXT_PRIMARY};
    border: 1px solid {ACCENT_FRAME};
    border-radius: 2px;
    padding: 4px 6px;
    selection-background-color: {ACCENT_GLOW};
}}

QLineEdit:focus {{
    border: 1px solid {ACCENT_GLOW};
}}

QLabel#resultLabel {{
    background-color: {DEPTH_BLUE};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    padding: 4px 8px;
    font-weight: bold;
}}

QGraphicsView {{
    background-color: #2a2f38;
    border: 1px solid {BORDER};
}}

QTableWidget, QTextBrowser {{
    background-color: {BG_DARK};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    gridline-color: {BORDER};
    selection-background-color: {ACCENT_FRAME};
    selection-color: {TEXT_PRIMARY};
}}

QHeaderView::section {{
    background-color: {BG_TRACK_SELECTED};
    color: {TEXT_PRIMARY};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 6px 8px;
    font-weight: bold;
}}

QScrollBar:vertical {{
    background: {BG_PANEL};
    width: 10px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {ACCENT_FRAME};
    min-height: 20px;
    border-radius: 4px;
}}

QScrollBar::handle:vertical:hover {{
    background: {ACCENT_GLOW};
}}

QStatusBar {{
    background: {BG_OVERLAY};
    color: {TEXT_MUTED};
    font-size: 9pt;
}}
"""
