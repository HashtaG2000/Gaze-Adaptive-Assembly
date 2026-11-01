"""Shared look (after the HTML design prototype). Colours and fonts come from data/theme.json.

Participant screens: light slate surfaces, one blue accent, rounded cards, coloured chips.
Experimenter cockpit / author tool: dark zinc theme with explicit colours for every widget
(nothing inherits the OS palette, so text is always readable).

Labels are PLAIN TEXT by default: text from instruction files or the LLM is never interpreted
as HTML.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from app.config import PROJECT_ROOT
from app.core.files import read_json
from app.ui.icons import Chip, IconTile, icon, icon_file, scale_button_icons, set_button_icon

_DEFAULT_COLORS = {
    "slate_50": "#f8fafc", "slate_100": "#f1f5f9", "slate_200": "#e2e8f0", "slate_300": "#cbd5e1",
    "slate_400": "#94a3b8", "slate_500": "#64748b", "slate_600": "#475569", "slate_700": "#334155",
    "slate_800": "#1e293b", "slate_900": "#0f172a", "blue_50": "#eff6ff", "blue_100": "#dbeafe",
    "blue_200": "#bfdbfe", "blue_300": "#93c5fd", "blue_400": "#60a5fa", "blue_600": "#2563eb",
    "blue_700": "#1d4ed8", "blue_900": "#1e3a8a", "blue_950": "#172554", "amber_100": "#fef3c7",
    "amber_200": "#fde68a", "amber_300": "#fcd34d", "amber_400": "#fbbf24", "amber_500": "#f59e0b",
    "amber_600": "#d97706", "amber_700": "#b45309", "amber_800": "#92400e", "amber_900": "#78350f",
    "emerald_100": "#d1fae5", "emerald_300": "#6ee7b7", "emerald_400": "#34d399", "emerald_500": "#10b981",
    "emerald_600": "#059669", "emerald_700": "#047857", "emerald_800": "#065f46", "emerald_950": "#022c22",
    "rose_50": "#fff1f2", "rose_100": "#ffe4e6", "rose_300": "#fda4af", "rose_400": "#fb7185",
    "rose_600": "#e11d48", "rose_800": "#9f1239", "rose_950": "#4c0519", "purple_300": "#d8b4fe",
    "purple_400": "#c084fc", "purple_500": "#a855f7", "purple_600": "#9333ea", "purple_950": "#3b0764",
    "zinc_100": "#f4f4f5", "zinc_200": "#e4e4e7", "zinc_300": "#d4d4d8", "zinc_400": "#a1a1aa",
    "zinc_500": "#71717a", "zinc_600": "#52525b", "zinc_700": "#3f3f46", "zinc_800": "#27272a",
    "zinc_900": "#18181b", "cockpit_bg": "#121214", "cockpit_card": "#1b1b1f", "cockpit_input": "#232329",
    "cockpit_border": "#2e2e35", "white": "#ffffff", "gaze": "#ef4444",
}
_DEFAULT_CHART = {"surface": "#1a1a19", "text": "#c3c2b7", "grid": "#33332f", "reference": "#e4e4e7",
                  "categorical": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"]}


def _load_theme() -> dict:
    try:
        data = read_json(PROJECT_ROOT / "data" / "theme.json")
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


THEME = _load_theme()
C: dict[str, str] = {**_DEFAULT_COLORS, **{k: v for k, v in THEME.get("colors", {}).items() if isinstance(v, str)}}
CHART: dict = {**_DEFAULT_CHART, **THEME.get("chart", {})}
FONT_NAME = str(THEME.get("fonts", {}).get("family", "Segoe UI"))
MONO_NAME = str(THEME.get("fonts", {}).get("mono", "Consolas"))
FONT_FAMILY = f'"{FONT_NAME}", "Arial", sans-serif'
MONO_FAMILY = f'"{MONO_NAME}", "Cascadia Mono", monospace'

# named constants (used throughout the UI)
SLATE_50, SLATE_100, SLATE_200, SLATE_300 = C["slate_50"], C["slate_100"], C["slate_200"], C["slate_300"]
SLATE_400, SLATE_500, SLATE_600, SLATE_700 = C["slate_400"], C["slate_500"], C["slate_600"], C["slate_700"]
SLATE_800, SLATE_900 = C["slate_800"], C["slate_900"]
BLUE_50, BLUE_100, BLUE_200, BLUE_300 = C["blue_50"], C["blue_100"], C["blue_200"], C["blue_300"]
BLUE_600, BLUE_700, BLUE_900 = C["blue_600"], C["blue_700"], C["blue_900"]
AMBER_100, AMBER_300, AMBER_400, AMBER_500 = C["amber_100"], C["amber_300"], C["amber_400"], C["amber_500"]
AMBER_600, AMBER_700, AMBER_800, AMBER_900 = C["amber_600"], C["amber_700"], C["amber_800"], C["amber_900"]
EMERALD_100, EMERALD_300, EMERALD_400 = C["emerald_100"], C["emerald_300"], C["emerald_400"]
EMERALD_500, EMERALD_600, EMERALD_700, EMERALD_800 = C["emerald_500"], C["emerald_600"], C["emerald_700"], C["emerald_800"]
ROSE_50, ROSE_100, ROSE_300, ROSE_800 = C["rose_50"], C["rose_100"], C["rose_300"], C["rose_800"]

BG = SLATE_50
PANEL = C["white"]
BORDER = SLATE_200
TEXT = SLATE_900
MUTED = SLATE_500
ACCENT = BLUE_600
ACCENT_DARK = BLUE_700

# Base sizes in px on the 1920x1080 canvas
TITLE_PX, BODY_PX, BUTTON_PX = 52, 34, 32


def combo_css(bg: str, fg: str, border: str, hover: str, selected: str, arrow: str, radius: int) -> str:
    """Dropdown as one control: chevron inside the field, the list opens directly below it
    (combobox-popup: 0) in the same colours - no native arrow button, no white popup frame."""
    arrow_file = icon_file("chevron_down", arrow, 24)
    return (
        "QComboBox { combobox-popup: 0; padding-right: 34px; }"
        f"QComboBox:hover {{ border-color: {arrow}; }}"
        "QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: 30px;"
        " border: none; background: transparent; }"
        f'QComboBox::down-arrow {{ image: url("{arrow_file}"); width: 12px; height: 12px; }}'
        "QComboBox::down-arrow:on { top: 1px; }"
        f"QComboBox QAbstractItemView {{ background: {bg}; color: {fg}; border: 1px solid {border};"
        f" border-radius: {radius}px; padding: 4px; outline: 0;"
        f" selection-background-color: {selected}; selection-color: {fg}; }}"
        "QComboBox QAbstractItemView::item { min-height: 28px; padding: 2px 10px; border-radius: 4px; }"
        f"QComboBox QAbstractItemView::item:hover {{ background: {hover}; }}"
        f"QComboBox QFrame {{ background: {bg}; border: none; }}"
    )


def check_css(bg: str, border: str, accent: str, fg: str, size: int, radius: int) -> str:
    """Real check boxes: an empty outlined box, or the accent box with a white tick - for
    QCheckBox and for checkable table / list items (e.g. rule "On")."""
    tick = icon_file("check", "#ffffff", 32)
    box = (f"width: {size}px; height: {size}px; border: 2px solid {border}; border-radius: {radius}px;"
           f" background: {bg};")
    on = f'background: {accent}; border-color: {accent}; image: url("{tick}");'
    return (
        f"QCheckBox {{ spacing: 10px; color: {fg}; }}"
        f"QCheckBox::indicator, QTableView::indicator, QListView::indicator {{ {box} }}"
        f"QCheckBox::indicator:hover, QTableView::indicator:hover {{ border-color: {accent}; }}"
        f"QCheckBox::indicator:checked, QTableView::indicator:checked, QListView::indicator:checked {{ {on} }}"
        f"QCheckBox::indicator:disabled {{ border-color: {border}; background: transparent; }}"
    )


def screen_stylesheet(scale: float) -> str:
    s = lambda v: max(8, round(v * scale))  # noqa: E731
    return f"""
        QWidget#screen {{ background: {BG}; }}
        QWidget {{ font-family: {FONT_FAMILY}; color: {TEXT}; }}
        QLabel {{ background: transparent; }}
        QLabel#title {{ font-size: {s(TITLE_PX)}px; font-weight: 700; color: {SLATE_900}; }}
        QLabel#headline {{ font-size: {s(30)}px; font-weight: 700; color: {SLATE_900}; }}
        QLabel#subtitle {{ font-size: {s(22)}px; color: {SLATE_500}; }}
        QLabel#body {{ font-size: {s(BODY_PX)}px; color: {SLATE_800}; }}
        QLabel#muted {{ font-size: {s(BODY_PX * 0.8)}px; color: {SLATE_500}; }}
        QLabel#small {{ font-size: {s(22)}px; color: {SLATE_500}; }}
        QLabel#section {{ font-size: {s(20)}px; font-weight: 700; color: {SLATE_400}; letter-spacing: 1px; }}
        QLabel#question {{ font-size: {s(28)}px; font-weight: 700; color: {SLATE_800}; }}
        QLabel#field {{ font-size: {s(26)}px; font-weight: 600; color: {SLATE_700}; }}
        QLabel#error {{ font-size: {s(24)}px; color: #b91c1c; }}
        QLabel#msgcard {{ background: {BLUE_50}; border: 2px solid {BLUE_200}; border-radius: {s(16)}px;
                          padding: {s(28)}px {s(36)}px; font-size: {s(BODY_PX)}px; font-weight: 500; color: #172554; }}
        QWidget#panel {{ background: {PANEL}; border: 2px solid {BORDER}; border-radius: {s(16)}px; }}
        QWidget#qcard {{ background: {SLATE_50}; border: 2px solid {BORDER}; border-radius: {s(14)}px; }}
        QWidget#divider {{ background: {BORDER}; min-height: 2px; max-height: 2px; }}

        QWidget#chip_amber {{ background: {AMBER_100}; border: 2px solid {AMBER_300}; border-radius: {s(18)}px; }}
        QWidget#chip_blue {{ background: {BLUE_100}; border: 2px solid {BLUE_300}; border-radius: {s(18)}px; }}
        QWidget#chip_emerald {{ background: {EMERALD_100}; border: 2px solid {EMERALD_300}; border-radius: {s(18)}px; }}
        QWidget#chip_neutral {{ background: {SLATE_100}; border: 2px solid {SLATE_300}; border-radius: {s(18)}px; }}
        QWidget#chip_amber QLabel, QWidget#chip_blue QLabel, QWidget#chip_emerald QLabel,
        QWidget#chip_neutral QLabel, QWidget#chip_check QLabel {{ border: none; background: transparent; }}
        QWidget#chip_amber QLabel#chiptext {{ color: {AMBER_900}; font-size: {s(22)}px; font-weight: 700; padding: {s(4)}px 0px; }}
        QWidget#chip_blue QLabel#chiptext {{ color: {BLUE_900}; font-size: {s(22)}px; font-weight: 700; padding: {s(4)}px 0px; }}
        QWidget#chip_emerald QLabel#chiptext {{ color: {EMERALD_800}; font-size: {s(22)}px; font-weight: 700; padding: {s(4)}px 0px; }}
        QWidget#chip_neutral QLabel#chiptext {{ color: {SLATE_800}; font-size: {s(22)}px; font-weight: 700; padding: {s(4)}px 0px; }}
        QWidget#chip_check QLabel#chiptext {{ color: {SLATE_700}; font-size: {s(28)}px; padding: {s(2)}px 0px; }}
        QLabel#icon_blue, QLabel#icon_emerald, QLabel#icon_amber, QLabel#icon_emerald_round {{ border-radius: {s(14)}px;
                       min-width: {s(68)}px; max-width: {s(68)}px; min-height: {s(68)}px; max-height: {s(68)}px; }}
        QLabel#icon_blue {{ background: {BLUE_600}; }}
        QLabel#icon_emerald {{ background: {EMERALD_100}; }}
        QLabel#icon_amber {{ background: {AMBER_100}; }}
        QLabel#icon_emerald_round {{ background: {EMERALD_100}; border-radius: {s(56)}px;
                       min-width: {s(112)}px; max-width: {s(112)}px; min-height: {s(112)}px; max-height: {s(112)}px; }}

        QProgressBar {{ background: {SLATE_200}; border: none; border-radius: {s(7)}px;
                        min-height: {s(14)}px; max-height: {s(14)}px; }}
        QProgressBar::chunk {{ background: {ACCENT}; border-radius: {s(7)}px; }}

        QPushButton {{ font-size: {s(BUTTON_PX)}px; font-weight: 700; border-radius: {s(14)}px;
                       padding: {s(22)}px {s(44)}px; min-width: {s(360)}px; }}
        QPushButton#primary {{ background: {ACCENT}; color: white; border: none; }}
        QPushButton#primary:pressed {{ background: {ACCENT_DARK}; }}
        QPushButton#secondary {{ background: {PANEL}; color: {SLATE_700}; border: 2px solid {SLATE_300}; }}
        QPushButton#secondary:pressed {{ background: {SLATE_100}; }}
        QPushButton#success {{ background: {EMERALD_600}; color: white; border: none; }}
        QPushButton#success:pressed {{ background: {EMERALD_700}; }}
        QPushButton#danger {{ background: {ROSE_50}; color: {ROSE_800}; border: 2px solid {ROSE_300}; }}
        QPushButton#danger:pressed {{ background: {ROSE_100}; }}
        QPushButton#option {{ background: {PANEL}; color: {SLATE_800}; border: 2px solid {SLATE_300};
                              border-radius: {s(14)}px; padding: {s(14)}px {s(20)}px; min-width: 0px; text-align: left; }}
        QPushButton#option:checked {{ background: {BLUE_50}; border: 3px solid {ACCENT}; }}
        QPushButton#option:disabled {{ background: {SLATE_100}; border: 2px dashed {SLATE_300}; }}
        QPushButton#option QLabel#opt_title {{ font-size: {s(26)}px; font-weight: 700; color: {SLATE_800}; }}
        QPushButton#option QLabel#opt_hint {{ font-size: {s(20)}px; color: {SLATE_500}; }}
        QPushButton#link {{ background: transparent; color: {SLATE_500}; border: none; min-width: 0px;
                            padding: {s(22)}px {s(16)}px; text-decoration: underline; }}
        QPushButton:disabled {{ background: {SLATE_200}; color: {SLATE_400}; border: 2px solid {SLATE_200}; }}
        QPushButton#link:disabled {{ background: transparent; color: {SLATE_300}; border: none; }}
        QLineEdit, QSpinBox, QComboBox {{ background: {PANEL}; border: 2px solid {SLATE_300}; border-radius: {s(10)}px;
                            padding: {s(10)}px {s(14)}px; font-size: {s(28)}px; color: {SLATE_900}; }}
        QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 2px solid {ACCENT}; }}
        {combo_css(PANEL, SLATE_900, SLATE_300, SLATE_100, BLUE_50, SLATE_500, s(10))}
        {check_css(PANEL, SLATE_400, ACCENT, SLATE_700, s(26), s(6))}
        QScrollArea {{ background: transparent; border: none; }}
        QScrollBar:vertical {{ background: {SLATE_100}; width: {s(14)}px; margin: 0; border-radius: {s(7)}px; }}
        QScrollBar::handle:vertical {{ background: {SLATE_300}; border-radius: {s(7)}px; min-height: {s(60)}px; }}
        QScrollBar::handle:vertical:hover {{ background: {SLATE_400}; }}
        QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
                     width: 0; height: 0; background: transparent; }}
    """


def cockpit_stylesheet() -> str:
    """Dark experimenter theme (HTML 'cockpit'): every widget type gets explicit colours."""
    bg, card, inp, brd = C["cockpit_bg"], C["cockpit_card"], C["cockpit_input"], C["cockpit_border"]
    z1, z2, z3, z4, z5 = C["zinc_100"], C["zinc_200"], C["zinc_300"], C["zinc_400"], C["zinc_500"]
    return f"""
        QWidget {{ font-family: {FONT_FAMILY}; font-size: 13px; color: {z2}; }}
        QMainWindow, QDialog, QWidget#root {{ background: {bg}; }}
        QLabel {{ background: transparent; color: {z2}; }}
        QWidget#card {{ background: {card}; border: 1px solid {brd}; border-radius: 10px; }}
        QWidget#tile {{ background: {inp}; border: 1px solid {brd}; border-radius: 8px; }}
        QLabel#section {{ color: {z4}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
        QLabel#app_title {{ font-size: 18px; font-weight: 700; color: {z1}; }}
        QLabel#app_sub {{ font-size: 12px; color: {z4}; }}
        QLabel#mono {{ font-family: {MONO_FAMILY}; color: {z3}; }}
        QLabel#tile_caption {{ color: {z4}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
        QLabel#tile_value {{ font-size: 14px; font-weight: 700; color: {z1}; }}
        QLabel#muted {{ color: {z4}; }}
        QLabel#banner {{ background: {C['purple_950']}; color: {C['purple_300']}; border: 1px solid {C['purple_600']};
                         border-radius: 6px; padding: 6px 10px; font-weight: 700; }}
        QLabel#icon_tile {{ background: {C['purple_600']}; border-radius: 10px;
                            min-width: 40px; max-width: 40px; min-height: 40px; max-height: 40px; }}

        QWidget#chip_ok {{ background: {C['emerald_950']}; border: 1px solid {C['emerald_700']}; border-radius: 11px; }}
        QWidget#chip_bad {{ background: {C['rose_950']}; border: 1px solid {C['rose_600']}; border-radius: 11px; }}
        QWidget#chip_blue {{ background: {C['blue_950']}; border: 1px solid {C['blue_700']}; border-radius: 11px; }}
        QWidget#chip_amber {{ background: #3b2306; border: 1px solid {C['amber_700']}; border-radius: 11px; }}
        QWidget#chip_purple {{ background: {C['purple_950']}; border: 1px solid {C['purple_600']}; border-radius: 11px; }}
        QWidget#chip_neutral {{ background: {inp}; border: 1px solid {brd}; border-radius: 11px; }}
        QWidget#chip_ok QLabel, QWidget#chip_bad QLabel, QWidget#chip_blue QLabel, QWidget#chip_amber QLabel,
        QWidget#chip_purple QLabel, QWidget#chip_neutral QLabel {{ border: none; background: transparent; }}
        QLabel#chiptext {{ font-weight: 700; font-size: 12px; padding: 2px 0px; color: {z2}; }}
        QWidget#chip_ok QLabel#chiptext {{ color: {C['emerald_300']}; }}
        QWidget#chip_bad QLabel#chiptext {{ color: {C['rose_300']}; }}
        QWidget#chip_blue QLabel#chiptext {{ color: {C['blue_300']}; }}
        QWidget#chip_amber QLabel#chiptext {{ color: {C['amber_300']}; }}
        QWidget#chip_purple QLabel#chiptext {{ color: {C['purple_300']}; }}

        QPushButton {{ background: {inp}; color: {z2}; border: 1px solid {brd}; border-radius: 7px;
                       padding: 7px 14px; font-weight: 600; }}
        QPushButton:hover {{ background: {C['zinc_800']}; border-color: {C['zinc_600']}; }}
        QPushButton:disabled {{ color: {z5}; background: {card}; border-color: {brd}; }}
        QPushButton:checked {{ background: {C['purple_950']}; border-color: {C['purple_500']}; color: {C['purple_300']}; }}
        QPushButton#start {{ background: {C['blue_600']}; color: white; border: none; border-radius: 9px;
                             padding: 12px 24px; font-weight: 700; font-size: 14px; }}
        QPushButton#start:hover {{ background: {C['blue_700']}; }}
        QPushButton#start:disabled {{ background: {C['zinc_700']}; color: {z4}; }}
        QPushButton#danger {{ background: {C['rose_950']}; color: {C['rose_300']}; border: 1px solid {C['rose_600']}; }}
        QPushButton#danger:hover {{ background: #6b0f2a; }}
        QPushButton#ok {{ background: {C['emerald_950']}; color: {C['emerald_300']}; border: 1px solid {C['emerald_600']}; }}
        QPushButton#ok:hover {{ background: #064e3b; }}
        QPushButton#warn {{ background: #3b2306; color: {C['amber_300']}; border: 1px solid {C['amber_700']}; }}
        QPushButton#warn:hover {{ background: #4a2c08; }}
        QPushButton#danger:disabled, QPushButton#ok:disabled, QPushButton#warn:disabled {{
                             color: {z5}; background: {card}; border-color: {brd}; }}

        QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QTextEdit {{ background: {inp}; color: {z1};
                       border: 1px solid {brd}; border-radius: 6px; padding: 5px 8px; selection-background-color: {C['blue_700']}; }}
        QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus {{ border-color: {C['blue_600']}; }}
        {combo_css(inp, z1, brd, C['zinc_800'], C['blue_950'], C['zinc_300'], 6)}
        {check_css(inp, C['zinc_500'], C['blue_600'], z3, 16, 4)}

        QTabWidget::pane {{ background: {card}; border: 1px solid {brd}; border-radius: 10px; top: -1px; }}
        QTabBar::tab {{ background: transparent; color: {z4}; padding: 8px 18px; margin-right: 4px; font-weight: 600;
                        border: none; border-bottom: 2px solid transparent; }}
        QTabBar::tab:selected {{ color: {C['purple_300']}; border-bottom: 2px solid {C['purple_500']}; }}
        QTabBar::tab:hover {{ color: {z1}; }}

        QTableWidget, QTableView {{ background: {card}; color: {z2}; border: 1px solid {brd}; border-radius: 8px;
                       gridline-color: {brd}; selection-background-color: {C['blue_950']}; selection-color: {z1};
                       alternate-background-color: {inp}; }}
        QHeaderView::section {{ background: {inp}; color: {z4}; border: none; border-bottom: 1px solid {brd};
                               padding: 6px 8px; font-size: 11px; font-weight: 700; }}
        QTableCornerButton::section {{ background: {inp}; border: none; }}
        QListWidget {{ background: {card}; color: {z2}; border: 1px solid {brd}; border-radius: 8px; padding: 4px; }}
        QListWidget::item {{ padding: 6px 8px; border-radius: 6px; }}
        QListWidget::item:selected {{ background: {C['blue_950']}; color: {z1}; }}
        QListWidget#nav {{ background: {card}; border: 1px solid {brd}; border-radius: 10px; padding: 8px 6px; }}
        QListWidget#nav::item {{ padding: 11px 12px; margin: 2px 0px; border-radius: 7px; color: {z3};
                                font-weight: 600; }}
        QListWidget#nav::item:hover {{ background: {inp}; color: {z1}; }}
        QListWidget#nav::item:selected {{ background: {C['purple_950']}; color: {C['purple_300']}; }}
        QTreeView, QTreeWidget {{ background: {card}; color: {z2}; border: 1px solid {brd}; border-radius: 8px;
                       alternate-background-color: {inp}; selection-background-color: {C['blue_950']};
                       selection-color: {z1}; outline: 0; }}
        QTreeView::item {{ padding: 4px 2px; }}
        QTreeView::item:hover {{ background: {inp}; }}
        QTreeView::item:selected {{ background: {C['blue_950']}; color: {z1}; }}
        QTreeView::branch {{ background: transparent; }}
        QTreeView::branch:has-children:closed {{ image: url("{icon_file('chevron_right', C['zinc_400'], 24)}"); }}
        QTreeView::branch:has-children:open {{ image: url("{icon_file('chevron_down', C['zinc_400'], 24)}"); }}
        QPlainTextEdit {{ font-family: {MONO_FAMILY}; font-size: 12px; background: #0b0b0d; color: {z2};
                          border: 1px solid {brd}; border-radius: 8px; padding: 6px; selection-background-color: {C['zinc_700']}; }}
        QPlainTextEdit#raw {{ color: {C['emerald_300']}; }}
        QScrollBar:vertical {{ background: {bg}; width: 10px; margin: 0; }}
        QScrollBar::handle:vertical {{ background: {C['zinc_700']}; border-radius: 4px; min-height: 24px; }}
        QScrollBar:horizontal {{ background: {bg}; height: 10px; margin: 0; }}
        QScrollBar::handle:horizontal {{ background: {C['zinc_700']}; border-radius: 4px; min-width: 24px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QSplitter::handle {{ background: transparent; }}
        QToolTip {{ background: {inp}; color: {z1}; border: 1px solid {brd}; padding: 4px; }}
        QMessageBox {{ background: {card}; }}
        QMessageBox QLabel {{ color: {z1}; }}
    """


def label(text: str, name: str, wrap: bool = True, align=Qt.AlignmentFlag.AlignCenter, rich: bool = False) -> QLabel:
    lab = QLabel()
    lab.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
    lab.setText(text)
    lab.setObjectName(name)
    lab.setWordWrap(wrap)
    lab.setAlignment(align)
    return lab


TILE_ICON_COLOR = {"blue": "#ffffff", "emerald": EMERALD_700, "emerald_round": EMERALD_600, "amber": AMBER_700}
BUTTON_ICON_COLOR = {"primary": "#ffffff", "success": "#ffffff", "secondary": SLATE_700, "danger": ROSE_800,
                     "option": SLATE_700, "link": SLATE_500}


def button(text: str, name: str, icon_key: Optional[str] = None, trailing: bool = False) -> QPushButton:
    b = QPushButton(text.replace("&", "&&"))   # "&" is a Qt mnemonic marker
    b.setObjectName(name)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_key:
        set_button_icon(b, icon_key, BUTTON_ICON_COLOR.get(name, SLATE_700), trailing=trailing)
    return b


def icon_tile(key: str, colour: str = "blue") -> IconTile:
    """Rounded tile with a QtAwesome icon (see app.ui.icons.ICONS)."""
    return IconTile(key, TILE_ICON_COLOR[colour], f"icon_{colour}")


def divider() -> QWidget:
    w = QWidget()
    w.setObjectName("divider")
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    return w


class OptionCard(QPushButton):
    """Large checkable card (title + hint) used for radio / checkbox choices (HTML option cards)."""

    def __init__(self, title: str, hint: str = "", icon_key: Optional[str] = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("option")
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.icon_key = icon_key
        self.mark = QLabel()
        self.title = label(title, "opt_title", wrap=True, align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.hint = label(hint, "opt_hint", wrap=True, align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.hint.setVisible(bool(hint))
        for w in (self.mark, self.title, self.hint):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(self.title)
        texts.addWidget(self.hint)
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 12, 18, 12)
        row.setSpacing(14)
        row.addWidget(self.mark)
        row.addLayout(texts, 1)
        self.toggled.connect(lambda _: self._refresh_mark())
        self._refresh_mark()

    def set_texts(self, title: str, hint: str = "") -> None:
        self.title.setText(title)
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))

    def _refresh_mark(self) -> None:
        px = max(14, self.title.fontMetrics().height())
        key = "check_circle" if self.isChecked() else (self.icon_key or "circle_empty")
        color = ACCENT if self.isChecked() else SLATE_400
        self.mark.setPixmap(icon(key, color).pixmap(px, px))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._refresh_mark()

    def sizeHint(self):
        sh = super().sizeHint()
        lay = self.layout()
        return lay.sizeHint().expandedTo(sh) if lay else sh


class ScreenHeader(QWidget):
    """Icon tile + headline + subtitle on the left, an optional chip on the right."""

    def __init__(self, icon_key: str, colour: str = "blue", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.icon = icon_tile(icon_key, colour)
        self.headline = label("", "headline", align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.subtitle = label("", "subtitle", align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.chip = Chip("", "amber")
        self.chip.hide()
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(self.headline)
        texts.addWidget(self.subtitle)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.icon)
        row.addSpacing(18)
        row.addLayout(texts, 1)
        row.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignVCenter)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(row)
        lay.addSpacing(16)
        lay.addWidget(divider())

    def set(self, headline: str, subtitle: str = "") -> None:
        self.headline.setText(headline)
        self.subtitle.setText(subtitle)
        self.subtitle.setVisible(bool(subtitle))

    def set_chip(self, text: str, kind: str = "amber", visible: bool = True, icons: tuple[str, ...] = (),
                 icon_color: Optional[str] = None) -> None:
        self.chip.set_kind(kind)
        self.chip.set_icons(list(icons), icon_color)
        self.chip.setText(text)
        self.chip.setVisible(visible and bool(text))


class ScaledScreen(QWidget):
    """Base class: restyles itself relative to a 1920x1080 canvas on resize."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("screen")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(screen_stylesheet(1.0))
        self.scale = 1.0

    def resizeEvent(self, event) -> None:
        self.scale = min(self.width() / 1920, self.height() / 1080) or 1.0
        self.setStyleSheet(screen_stylesheet(self.scale))
        scale_button_icons(self, max(12, round(30 * self.scale)))
        super().resizeEvent(event)


def page_layout(widget: QWidget) -> QVBoxLayout:
    """Standard page margins of the participant screens."""
    lay = QVBoxLayout(widget)
    lay.setContentsMargins(90, 60, 90, 50)
    return lay
