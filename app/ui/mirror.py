"""Live stimulus mirror for the cockpit: a schematic of the participant screen (AOI layout from
config.yaml), the current step, hidden elements, and the live point of regard (POR).

Samples arrive at the tracker rate (60-150 Hz); the widget only stores the latest sample and
repaints on its own timer (~30 Hz), so a fast tracker never floods the GUI thread.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from app.config import AppConfig
from app.models import GazeSample
from app.pipeline.aoi import AoiMap
from app.ui.style import C, FONT_NAME, MONO_NAME

REPAINT_MS = 33
ELEMENT_OF_AOI = {"text": "text", "image": "image", "video": "video"}


class StimulusMirror(QWidget):
    def __init__(self, cfg: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.aoi_map = AoiMap.from_config(cfg)
        self.setMinimumSize(320, 180)
        self.page = ""
        self.step_text = ""
        self.hidden: set[str] = set()
        self.active = False         # True while the player shows a run
        self.por: Optional[tuple[float, float]] = None
        self.aoi = "–"
        self._dirty = False
        self._timer = QTimer(self)
        self._timer.setInterval(REPAINT_MS)
        self._timer.timeout.connect(self._repaint)
        self._timer.start()

    # --- data -------------------------------------------------------------------------------
    def on_sample(self, s: GazeSample) -> None:
        if s.valid and s.x_norm is not None and s.y_norm is not None:
            self.por = (s.x_norm, s.y_norm)
            self.aoi = self.aoi_map.map_point(s.x_norm, s.y_norm, True)
        else:
            self.por = None
            self.aoi = "invalid"
        self._dirty = True

    def set_state(self, page: str, active: bool, hidden: set[str], step_text: str = "") -> None:
        self.page, self.active, self.hidden, self.step_text = page, active, set(hidden), step_text
        self._dirty = True

    def clear(self) -> None:
        self.por, self.aoi, self.page, self.step_text, self.active = None, "–", "", "", False
        self._dirty = True

    def _repaint(self) -> None:
        if self._dirty and self.isVisible():
            self._dirty = False
            self.update()

    # --- drawing ----------------------------------------------------------------------------
    def _canvas(self) -> QRectF:
        sw, sh = self.cfg.screen.width, self.cfg.screen.height
        w, h = self.width() - 4, self.height() - 4
        k = min(w / sw, h / sh)
        return QRectF((self.width() - sw * k) / 2, (self.height() - sh * k) / 2, sw * k, sh * k)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        canvas = self._canvas()
        k = canvas.width() / self.cfg.screen.width
        p.setPen(QPen(QColor(C["cockpit_border"]), 1))
        p.setBrush(QColor("#ffffff" if self.active else C["zinc_800"]))
        p.drawRoundedRect(canvas, 6, 6)
        f = QFont(MONO_NAME)
        f.setPixelSize(10)
        p.setFont(f)
        if self.active:
            for name, r in self.cfg.aoi.items():
                rect = QRectF(canvas.left() + r.x * k, canvas.top() + r.y * k, r.w * k, r.h * k)
                hidden = ELEMENT_OF_AOI.get(name) in self.hidden
                focus = name == self.aoi
                fill = QColor(C["blue_600"] if focus else C["slate_200"])
                fill.setAlpha(90 if focus else (40 if hidden else 140))
                p.setBrush(fill)
                p.setPen(QPen(QColor(C["blue_600"] if focus else C["slate_400"]), 1.5 if focus else 1,
                              Qt.PenStyle.DashLine if hidden else Qt.PenStyle.SolidLine))
                p.drawRoundedRect(rect, 3, 3)
                p.setPen(QColor(C["slate_700"] if not hidden else C["slate_400"]))
                p.drawText(rect.adjusted(3, 1, -3, -1), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                           name + (" (hidden)" if hidden else ""))
            if self.step_text and "text" not in self.hidden:
                tr = self.cfg.aoi.get("text")
                if tr is not None:
                    fb = QFont(FONT_NAME)
                    fb.setPixelSize(max(8, int(26 * k)))
                    p.setFont(fb)
                    p.setPen(QColor(C["slate_800"]))
                    rect = QRectF(canvas.left() + tr.x * k, canvas.top() + tr.y * k, tr.w * k, tr.h * k)
                    p.drawText(rect.adjusted(8, 12, -8, -2),
                               Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap,
                               self.step_text)
        else:
            p.setPen(QColor(C["zinc_400"]))
            p.drawText(canvas, Qt.AlignmentFlag.AlignCenter, self.page or "–")
        if self.por is not None:
            c = QPointF(canvas.left() + self.por[0] * canvas.width(), canvas.top() + self.por[1] * canvas.height())
            gaze = QColor(C["gaze"])
            p.setPen(Qt.PenStyle.NoPen)
            for rad, alpha in ((12, 60), (7, 120), (3.5, 255)):
                gaze.setAlpha(alpha)
                p.setBrush(gaze)
                p.drawEllipse(c, rad, rad)
        p.end()
