"""Color themes + stylesheet builder for the GUI.

Dark theme uses OpenWave's exact palette (navy background, cyan accent) so
the two apps look like siblings. Light theme is a matched high-contrast
variant using the same accent language.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str       # window background
    panel: str    # chat / status panel background
    border: str
    accent: str   # callsign, senders, prefix
    text: str
    yellow: str   # system messages
    green: str    # connected / RX
    red: str      # disconnected / TX


# OpenWave's colors verbatim.
DARK = Palette(
    name="dark",
    bg="#1a1a2e", panel="#0d0d1a", border="#2a2a4a",
    accent="#4fc3f7", text="#e0e0e0",
    yellow="#ffd54f", green="#69f0ae", red="#ef5350",
)

LIGHT = Palette(
    name="light",
    bg="#eef0f4", panel="#ffffff", border="#c6c9d6",
    accent="#0277bd", text="#1a1a2e",
    yellow="#a9791b", green="#2e7d32", red="#c62828",
)


def by_name(name: str) -> Palette:
    return LIGHT if name == "light" else DARK


def stylesheet(p: Palette) -> str:
    """Global application stylesheet for a palette."""
    return f"""
    QMainWindow, QWidget {{
        background: {p.bg};
        color: {p.text};
        font-family: 'Noto Sans Mono', 'DejaVu Sans Mono', monospace;
        font-size: 13px;
    }}
    QMenuBar {{ background: {p.panel}; color: {p.text}; }}
    QMenuBar::item:selected {{ background: {p.border}; }}
    QMenu {{ background: {p.panel}; color: {p.text}; border: 1px solid {p.border}; }}
    QMenu::item:selected {{ background: {p.accent}; color: {p.panel}; }}

    QTextEdit {{
        background: {p.panel};
        border: 1px solid {p.border};
        border-radius: 4px;
        selection-background-color: {p.accent};
    }}
    QLineEdit {{
        background: {p.panel};
        color: {p.text};
        border: 1px solid {p.border};
        border-radius: 4px;
        padding: 5px 8px;
    }}
    QLineEdit:focus {{ border: 1px solid {p.accent}; }}

    QPushButton {{
        background: {p.panel};
        color: {p.accent};
        border: 1px solid {p.border};
        border-radius: 4px;
        padding: 5px 14px;
        font-weight: bold;
    }}
    QPushButton:hover {{ border: 1px solid {p.accent}; }}
    QPushButton:pressed {{ background: {p.border}; }}

    QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: 4px; top: -1px; }}
    QTabBar::tab {{
        background: {p.panel};
        color: {p.text};
        padding: 6px 18px;
        border: 1px solid {p.border};
        border-bottom: none;
        border-top-left-radius: 4px;
        border-top-right-radius: 4px;
    }}
    QTabBar::tab:selected {{ background: {p.accent}; color: {p.panel}; font-weight: bold; }}
    QTabBar::tab:!selected {{ margin-top: 2px; }}

    QLabel {{ background: transparent; }}
    QDialog {{ background: {p.bg}; color: {p.text}; }}
    QComboBox, QSpinBox {{
        background: {p.panel}; color: {p.text};
        border: 1px solid {p.border}; border-radius: 4px; padding: 3px 6px;
    }}
    QCheckBox {{ color: {p.text}; }}
    """
