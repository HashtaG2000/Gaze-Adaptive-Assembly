"""Screen 3 - Waiting while the LLM works (participant-facing).

Also used while a new version is generated, and for short notices.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QVBoxLayout, QWidget

from app.i18n import t
from app.ui.icons import pixmap
from app.ui.style import ACCENT, BLUE_100, BLUE_200, BLUE_50, ScaledScreen, icon_tile, label


class Spinner(QWidget):
    """Rotating arc on a light ring with a small 'thinking' glyph in the centre."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._angle = 0.0
        self.running = True
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)
        self.setFixedSize(180, 180)

    def showEvent(self, e) -> None:
        if self.running:
            self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)

    def _tick(self) -> None:
        self._angle = (self._angle + 7) % 360
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        ring = QRectF(12, 12, w - 24, w - 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(BLUE_50))
        p.drawEllipse(ring)
        p.setPen(QPen(QColor(BLUE_200), 12))
        p.drawEllipse(ring)
        p.setPen(QPen(QColor(ACCENT), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(ring, int(-self._angle * 16), 90 * 16)
        # pulsing brain icon in the centre ("thinking")
        a = 0.5 + 0.5 * math.sin(math.radians(self._angle * 2))
        px = int(w * 0.36)
        p.setOpacity(0.55 + 0.45 * a)
        c = ring.center()
        p.drawPixmap(int(c.x() - px / 2), int(c.y() - px / 2), self._brain(px))
        p.setOpacity(1.0)
        p.end()

    def _brain(self, px: int):
        if getattr(self, "_brain_px", None) != px:
            self._brain_px, self._brain_pm = px, pixmap("thinking", ACCENT, px)
        return self._brain_pm


class AnalysingScreen(ScaledScreen):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spinner = Spinner()
        self.notice_icon = icon_tile("warning", "amber")
        self.notice_icon.hide()
        self.title = label("", "title")
        self.subtitle = label("", "muted")
        lay = QVBoxLayout(self)
        lay.addStretch(3)
        lay.addWidget(self.spinner, alignment=Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.notice_icon, alignment=Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(50)
        lay.addWidget(self.title)
        lay.addSpacing(20)
        lay.addWidget(self.subtitle)
        lay.addStretch(4)

    def _set(self, title: str, text: str, spinning: bool) -> None:
        self.title.setText(title)
        self.subtitle.setText(text)
        self.spinner.running = spinning
        self.spinner.setVisible(spinning)
        self.notice_icon.setVisible(not spinning)

    def show_analysing(self, condition: str = "gaze") -> None:
        key = "analysing.text_gaze" if condition == "gaze" else "analysing.text_time_only"
        self._set(t("analysing.title"), t(key), True)

    def show_generating(self) -> None:
        self._set(t("analysing.generating_title"), t("analysing.generating_text"), True)

    def show_notice(self, title: str, text: str) -> None:
        self._set(title, text, False)
