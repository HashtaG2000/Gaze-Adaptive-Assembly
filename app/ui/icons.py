"""Icons for the whole app, from the QtAwesome icon pack (Font Awesome 6 Solid; fa5 for outlines).

Use the names in ICONS (semantic keys) rather than raw Font Awesome names, so the icon set can be
changed in one place. Widgets here rescale their icons with the (window-scaled) text size.
"""
from __future__ import annotations

import functools
from typing import Iterable, Optional

import qtawesome as qta
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

ICONS = {
    # navigation / actions
    "back": "fa6s.arrow-left", "next": "fa6s.arrow-right", "play": "fa6s.circle-play",
    "adjust": "fa6s.pen-to-square", "check": "fa6s.check", "check_double": "fa6s.check-double",
    "close": "fa6s.xmark", "revert": "fa6s.rotate-left", "export": "fa6s.file-export", "stop": "fa6s.stop",
    # screen headers
    "app": "fa6s.eye", "welcome": "fa6s.shapes", "direction": "fa6s.sliders", "elements": "fa6s.list-check",
    "proposal": "fa6s.wand-magic-sparkles", "feedback": "fa6s.clipboard-question", "done": "fa6s.circle-check",
    "thinking": "fa6s.brain", "warning": "fa6s.circle-exclamation", "item": "fa6s.cubes",
    # gamification
    "streak": "fa6s.bolt", "level": "fa6s.medal", "level_up": "fa6s.circle-arrow-up", "trophy": "fa6s.trophy",
    "star": "fa6s.star", "star_half": "fa6s.star-half-stroke", "star_empty": "fa5.star",
    # elements
    "text": "fa6s.font", "image": "fa6s.image", "video": "fa6s.video", "gamification": "fa6s.trophy",
    "keyboard": "fa6s.keyboard",
    # researcher panel
    "dot": "fa6s.circle", "user": "fa6s.user", "condition": "fa6s.flask", "gaze": "fa6s.eye", "llm": "fa6s.robot",
    "repetition": "fa6s.repeat", "version": "fa6s.code-branch", "valid": "fa6s.circle-check",
    "invalid": "fa6s.circle-xmark",
    # new design
    "check_circle": "fa6s.circle-check", "circle_empty": "fa5.circle", "square_empty": "fa5.square",
    "square_check": "fa6s.square-check", "guide": "fa6s.helmet-safety", "flag": "fa6s.flag-checkered",
    "bullseye": "fa6s.bullseye", "power": "fa6s.power-off", "cockpit": "fa6s.sliders", "lab": "fa6s.flask-vial",
    "sessions": "fa6s.folder-open", "author": "fa6s.pen-ruler", "chart": "fa6s.chart-line", "save": "fa6s.floppy-disk",
    "plus": "fa6s.plus", "trash": "fa6s.trash", "up": "fa6s.arrow-up", "down": "fa6s.arrow-down",
    "import": "fa6s.file-import", "copy": "fa6s.copy", "refresh": "fa6s.rotate", "video_off": "fa6s.video-slash",
    "hidden": "fa6s.eye-slash", "trial": "fa6s.flask", "difficulty": "fa6s.gauge", "compact": "fa6s.layer-group",
    "rules": "fa6s.scale-balanced", "media": "fa6s.photo-film", "steps": "fa6s.list-ol", "live": "fa6s.tower-broadcast",
    "merge": "fa6s.code-merge", "chevron_down": "fa6s.chevron-down", "chevron_right": "fa6s.chevron-right",
    "camera": "fa6s.camera",
}


def _load_overrides() -> None:
    """Optional icon overrides from data/theme.json -> {"icons": {"key": "fa6s.name"}}."""
    try:
        import json
        from app.config import PROJECT_ROOT
        path = PROJECT_ROOT / "data" / "theme.json"
        if path.is_file() and path.stat().st_size < 1_000_000:
            data = json.loads(path.read_text(encoding="utf-8"))
            for k, v in (data.get("icons") or {}).items():
                if isinstance(k, str) and isinstance(v, str) and v.count(".") == 1:
                    ICONS[k] = v
    except (OSError, ValueError, ImportError):
        pass


_load_overrides()


def icon(key: str, color: str, disabled: Optional[str] = None) -> QIcon:
    opts = {"color": color}
    if disabled:
        opts["color_disabled"] = disabled
    try:
        return qta.icon(ICONS.get(key, key), **opts)
    except Exception:  # unknown / mistyped icon name: never crash the UI
        return qta.icon("fa6s.circle-question", **opts)


def pixmap(key: str, color: str, px: int) -> QPixmap:
    return icon(key, color).pixmap(QSize(px, px))


@functools.lru_cache(maxsize=64)
def icon_file(key: str, color: str, px: int = 32) -> str:
    """The icon as a cached PNG file (stylesheets can only use image files, e.g. combo arrows,
    check marks). Returns a forward-slash path for url(...); "" if it cannot be written."""
    import tempfile
    from pathlib import Path
    safe = "".join(ch for ch in f"{key}_{color}_{px}" if ch.isalnum() or ch in "_-")
    folder = Path(tempfile.gettempdir()) / "gaze_app_icons"
    path = folder / f"{safe}.png"
    if not path.is_file():
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if not pixmap(key, color, px).save(str(path), "PNG"):
                return ""
        except OSError:
            return ""
    return path.as_posix()


def set_button_icon(btn: QPushButton, key: str, color: str, disabled: str = "#94a3b8",
                    trailing: bool = False) -> None:
    """Icon on a push button; trailing=True puts it after the text (e.g. "Next →")."""
    btn.setIcon(icon(key, color, disabled))
    btn.setProperty("icon_key", key)
    if trailing:
        btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)


def scale_button_icons(root: QWidget, px: int) -> None:
    for b in root.findChildren(QPushButton):
        if b.property("icon_key"):
            b.setIconSize(QSize(px, px))


class IconTile(QLabel):
    """Rounded square with one icon (size follows the tile size set by the stylesheet)."""

    def __init__(self, key: str, color: str, object_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key, self.color = key, color
        self.setObjectName(object_name)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._px = 0

    def set_icon(self, key: str, color: Optional[str] = None) -> None:
        self.key, self.color = key, color or self.color
        self._px = 0
        self._refresh()

    def _refresh(self) -> None:
        px = max(10, int(min(self.width(), self.height()) * 0.5))
        if px != self._px:
            self._px = px
            self.setPixmap(pixmap(self.key, self.color, px))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._refresh()


class Chip(QWidget):
    """Pill with one or more icons and a text. Styled via its object name: chip_<kind>.

    The stylesheet sets background / border on `QWidget#chip_<kind>` and the text style on
    `QWidget#chip_<kind> QLabel#chiptext`. Icon colour is passed explicitly per kind.
    """

    COLORS = {"amber": "#b45309", "blue": "#1d4ed8", "emerald": "#047857", "ok": "#047857",
              "bad": "#be123c", "neutral": "#475569", "stars": "#f59e0b", "purple": "#7e22ce"}
    COLORS_DARK = {"amber": "#fcd34d", "blue": "#93c5fd", "emerald": "#6ee7b7", "ok": "#6ee7b7",
                   "bad": "#fda4af", "neutral": "#d4d4d8", "stars": "#fbbf24", "purple": "#d8b4fe"}

    def __init__(self, text: str = "", kind: str = "neutral", icons: Iterable[str] = (),
                 parent: QWidget | None = None, dark: bool = False) -> None:
        super().__init__(parent)
        self.dark = dark
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(10, 3, 12, 3)
        self._lay.setSpacing(6)
        self._icon_labels: list[QLabel] = []
        self._icons: list[str] = []
        self._icon_color: Optional[str] = None
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.PlainText)   # never interpret chip text as HTML
        self.label.setText(text)
        self.label.setObjectName("chiptext")
        self._lay.addWidget(self.label)
        self._px = 0
        self.set_kind(kind)
        self.set_icons(list(icons))

    # QLabel-like API
    def text(self) -> str:
        return self.label.text()

    def setText(self, text: str) -> None:  # noqa: N802
        self.label.setText(text)

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        if self.objectName() != f"chip_{kind}":
            self.setObjectName(f"chip_{kind}")
            self.style().unpolish(self)
            self.style().polish(self)
            for lab in self.findChildren(QLabel):
                lab.style().unpolish(lab)
                lab.style().polish(lab)
        self._px = 0
        self._refresh_icons()

    def set_icons(self, keys: list[str], color: Optional[str] = None) -> None:
        if list(keys) == self._icons and color == self._icon_color and self._icon_labels:
            return                                   # unchanged (e.g. called on every keystroke)
        for lab in self._icon_labels:
            lab.hide()
            self._lay.removeWidget(lab)              # out of the layout now, not at the next event loop
            lab.deleteLater()
        self._icon_labels = []
        self._icons = list(keys)
        self._icon_color = color
        for i, _ in enumerate(self._icons):
            lab = QLabel()
            lab.setObjectName("chipicon")
            self._lay.insertWidget(i, lab)
            self._icon_labels.append(lab)
        self._px = 0
        self._refresh_icons()

    def _refresh_icons(self) -> None:
        if not self._icon_labels:
            return
        px = max(10, int(self.label.fontMetrics().height() * 0.85))
        if px == self._px:
            return
        self._px = px
        palette = self.COLORS_DARK if self.dark else self.COLORS
        color = self._icon_color or palette.get(self.kind, "#475569")
        for lab, key in zip(self._icon_labels, self._icons):
            lab.setPixmap(pixmap(key, color, px))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._refresh_icons()

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        self._px = 0
        self._refresh_icons()
