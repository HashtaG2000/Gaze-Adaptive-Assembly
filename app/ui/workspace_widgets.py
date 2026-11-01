"""Small building blocks shared by the Workspace pages (dark theme from style.cockpit_stylesheet)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from app.core.files import LRUCache
from app.i18n import t
from app.pipeline.export import export_session
from app.ui.icons import set_button_icon
from app.ui.style import C, FONT_NAME

ICON_COL = {"": C["zinc_200"], "danger": C["rose_300"], "ok": C["emerald_300"], "warn": C["amber_300"],
            "start": "#ffffff"}
_pixmaps: LRUCache[Optional[QPixmap]] = LRUCache(64)


def btn(text: str, icon_key: str, name: str = "", checkable: bool = False) -> QPushButton:
    b = QPushButton("  " + text.replace("&", "&&"))   # "&" is a Qt mnemonic marker
    if name:
        b.setObjectName(name)
    b.setCheckable(checkable)
    set_button_icon(b, icon_key, ICON_COL.get(name, C["zinc_200"]), disabled=C["zinc_500"])
    b.setIconSize(QSize(15, 15))
    return b


def card() -> QWidget:
    w = QWidget()
    w.setObjectName("card")
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    return w


def section_label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("section")
    return lab


def plain(text: str = "", name: str = "", wrap: bool = True) -> QLabel:
    lab = QLabel()
    lab.setTextFormat(Qt.TextFormat.PlainText)    # file / LLM / participant text: never HTML
    lab.setText(text)
    lab.setWordWrap(wrap)
    if name:
        lab.setObjectName(name)
    return lab


def value_tile(caption: str) -> tuple[QWidget, QLabel]:
    w = QWidget()
    w.setObjectName("tile")
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    v = QVBoxLayout(w)
    v.setContentsMargins(10, 6, 10, 6)
    v.setSpacing(1)
    c = QLabel(caption.upper())
    c.setObjectName("tile_caption")
    val = plain("–", "tile_value")
    v.addWidget(c)
    v.addWidget(val)
    return w, val


def load_pixmap(path: Optional[Path]) -> Optional[QPixmap]:
    """Decoded image from an LRU cache (re-decoded when the file changes)."""
    if path is None:
        return None
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    key = (str(p), st.st_mtime_ns, st.st_size)
    hit = _pixmaps.get(key)
    if hit is not None or key in _pixmaps:
        return hit
    pm = QPixmap(str(p))
    pm = None if pm.isNull() else pm
    _pixmaps.put(key, pm)
    return pm


class ImageView(QWidget):
    """An image scaled to fit (aspect kept), with a caption and an optional coloured frame."""

    def __init__(self, caption: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.caption = caption
        self.pixmap: Optional[QPixmap] = None
        self.placeholder = ""
        self.frame: Optional[str] = None
        self.setMinimumSize(160, 110)

    def set_image(self, path: Optional[Path], placeholder: str = "", frame: Optional[str] = None) -> None:
        self.pixmap = load_pixmap(path)
        self.placeholder = placeholder
        self.frame = frame
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(self.frame or C["cockpit_border"]), 2 if self.frame else 1))
        p.setBrush(QColor("#0b0b0d"))
        p.drawRoundedRect(r, 8, 8)
        cap_h = 22
        area = r.adjusted(6, cap_h, -6, -6)
        if self.pixmap is not None:
            pm = self.pixmap.scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(area.x() + (area.width() - pm.width()) // 2, area.y() + (area.height() - pm.height()) // 2, pm)
        else:
            p.setPen(QColor(C["zinc_500"]))
            p.drawText(area, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.placeholder or "–")
        f = p.font()
        f.setFamily(FONT_NAME)
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(C["zinc_400"]))
        p.drawText(r.adjusted(10, 4, -10, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, self.caption.upper())
        p.end()


class ImagePair(QWidget):
    """Reference image and camera photo side by side (camera check)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.reference = ImageView(t("workspace.reference"))
        self.photo = ImageView(t("workspace.photo"))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self.reference, 1)
        lay.addWidget(self.photo, 1)

    def show_pair(self, reference: Optional[Path], photo: Optional[Path], frame: Optional[str] = None) -> None:
        self.reference.set_image(reference, t("workspace.no_reference"))
        self.photo.set_image(photo, t("workspace.no_photo"), frame)


def export_dir(folder: Optional[Path], parent: QWidget) -> Optional[Path]:
    if folder is None:
        return None
    try:
        out = export_session(folder)
    except Exception as exc:  # noqa: BLE001 - shown to the experimenter
        QMessageBox.critical(parent, t("cockpit.export_failed"), str(exc))
        return None
    QMessageBox.information(parent, t("cockpit.export"), t("cockpit.export_done", path=out))
    return out
