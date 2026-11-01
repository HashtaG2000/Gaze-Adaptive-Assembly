"""Force a light theme regardless of the Windows dark-mode setting.

Qt follows the OS colour scheme. All screens are designed light (white / light grey, dark text),
so with Windows dark mode some widgets (tables, lists, tabs, combo boxes) got dark backgrounds
while the stylesheets kept the text black. This pins the whole app to a light palette.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

WINDOW = "#f4f5f7"
BASE = "#ffffff"
ALT_BASE = "#f7f7f5"
TEXT = "#111111"
MUTED = "#5f6670"
ACCENT = "#1565c0"


def apply_light_theme(app: QApplication) -> None:
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)  # Qt >= 6.8
    except AttributeError:
        pass
    app.setStyle("Fusion")  # the native Windows 11 style ignores parts of the palette
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: WINDOW, QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: BASE, QPalette.ColorRole.AlternateBase: ALT_BASE,
        QPalette.ColorRole.Text: TEXT, QPalette.ColorRole.Button: BASE,
        QPalette.ColorRole.ButtonText: TEXT, QPalette.ColorRole.ToolTipBase: BASE,
        QPalette.ColorRole.ToolTipText: TEXT, QPalette.ColorRole.PlaceholderText: MUTED,
        QPalette.ColorRole.Highlight: ACCENT, QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.Link: ACCENT, QPalette.ColorRole.BrightText: "#ffffff",
        QPalette.ColorRole.Light: BASE, QPalette.ColorRole.Midlight: "#e4e6ea",
        QPalette.ColorRole.Mid: "#c9ced6", QPalette.ColorRole.Dark: "#a0a6ae",
        QPalette.ColorRole.Shadow: "#7a7f86",
    }
    for role, colour in roles.items():
        p.setColor(role, QColor(colour))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor("#a0a6ae"))
    app.setPalette(p)
