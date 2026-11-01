"""P1 - Eye-tracker check (participant-facing): targets one after another, then the result.

Timeline per target: settle_s (ring collapses, eyes move there) -> sample_s (gaze collected).
The controller forwards gaze samples via feed(); finished(list[list[(x, y)]]) is emitted when all
targets are done. The result is shown until the experimenter decides in the cockpit.
"""
from __future__ import annotations

import math
import time
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from app.config import CalibrationConfig
from app.i18n import t
from app.models import CalibrationRecord, GazeSample
from app.ui.style import C, FONT_NAME, MONO_NAME

GET_READY_S = 1.2
TICK_MS = 30


class CalibrationScreen(QWidget):
    finished = Signal(object)   # list of per-target sample lists

    def __init__(self, cal: CalibrationConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cal = cal
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background: #1e232a;")
        self.points: list[tuple[float, float]] = []
        self.samples: list[list[tuple[float, float]]] = []
        self.index = -1
        self._t0 = 0.0
        self._running = False
        self._result: Optional[tuple[str, bool]] = None
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    # --- control ----------------------------------------------------------------------------
    def start(self, points: list[tuple[float, float]]) -> None:
        self.points = [tuple(p) for p in points]
        self.samples = [[] for _ in self.points]
        self.index = -1
        self._result = None
        self._running = True
        self._t0 = time.monotonic()
        self._timer.start()
        self.update()

    def stop(self) -> None:
        self._running = False
        self._timer.stop()

    def feed(self, s: GazeSample) -> None:
        if not self._running or self.index < 0 or not s.valid or s.x_norm is None or s.y_norm is None:
            return
        if self._phase_elapsed() >= self.cal.settle_s:
            self.samples[self.index].append((s.x_norm, s.y_norm))

    def show_result(self, rec: CalibrationRecord, limit: float) -> None:
        if rec.mean_error_deg is None:
            text = t("calibration.result_no_data")
        else:
            key = "calibration.result_pass" if rec.passed else "calibration.result_fail"
            text = t(key, err=f"{rec.mean_error_deg:.2f}", limit=f"{limit:.1f}")
        self._result = (text, rec.passed)
        self.update()

    # --- timeline -------------------------------------------------------------------------------
    def _phase_elapsed(self) -> float:
        elapsed = time.monotonic() - self._t0 - GET_READY_S
        per = self.cal.settle_s + self.cal.sample_s
        return elapsed - max(self.index, 0) * per

    def _tick(self) -> None:
        elapsed = time.monotonic() - self._t0 - GET_READY_S
        per = self.cal.settle_s + self.cal.sample_s
        idx = int(elapsed // per) if elapsed >= 0 else -1
        if idx >= len(self.points):
            self._running = False
            self._timer.stop()
            self.index = len(self.points)
            self.update()
            self.finished.emit([list(s) for s in self.samples])
            return
        self.index = idx
        self.update()

    # --- drawing --------------------------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        s = min(w / 1920, h / 1080) or 1.0
        p.fillRect(self.rect(), QColor("#1e232a"))
        # faint markers for all targets
        for i, (x, y) in enumerate(self.points):
            c = QPointF(x * w, y * h)
            p.setPen(QPen(QColor("#475569"), 3 * s))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 18 * s, 18 * s)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#64748b"))
            p.drawEllipse(c, 5 * s, 5 * s)
        # active target: collapsing ring
        if self._running and 0 <= self.index < len(self.points):
            x, y = self.points[self.index]
            c = QPointF(x * w, y * h)
            ph = self._phase_elapsed()
            settle = max(self.cal.settle_s, 1e-3)
            k = max(0.0, 1.0 - ph / settle)
            ring = (22 + 70 * k) * s
            p.setPen(QPen(QColor(C["blue_400"]), 4 * s))
            p.setBrush(QColor(23, 37, 84, 200))
            p.drawEllipse(c, ring, ring)
            pulse = 0.6 + 0.4 * math.sin(time.monotonic() * 8)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(c, 9 * s * pulse + 3 * s, 9 * s * pulse + 3 * s)
        # top status bar
        f = QFont(MONO_NAME)
        f.setPixelSize(max(10, round(24 * s)))
        p.setFont(f)
        if self._running and self.index < 0:
            status = t("calibration.get_ready")
        elif self.points:
            status = t("calibration.point", i=min(self.index + 1, len(self.points)), n=len(self.points))
        else:
            status = ""
        self._pill(p, QRectF(40 * s, 30 * s, w - 80 * s, 64 * s), status, QColor("#e2e8f0"), s)
        # bottom: instruction or result
        f2 = QFont(FONT_NAME)
        f2.setPixelSize(max(10, round(28 * s)))
        p.setFont(f2)
        if self._result is not None:
            text, ok = self._result
            colour = QColor(C["emerald_400"] if ok else C["rose_400"])
            self._pill(p, QRectF(w * 0.2, h * 0.42, w * 0.6, 90 * s), text, colour, s)
            self._pill(p, QRectF(w * 0.2, h - 120 * s, w * 0.6, 70 * s), t("calibration.wait"), QColor("#cbd5e1"), s)
        else:
            self._pill(p, QRectF(w * 0.15, h - 120 * s, w * 0.7, 70 * s), t("calibration.instruction"),
                       QColor("#cbd5e1"), s)
        p.end()

    @staticmethod
    def _pill(p: QPainter, r: QRectF, text: str, colour: QColor, s: float) -> None:
        if not text:
            return
        p.setPen(QPen(QColor(255, 255, 255, 30), 1.5))
        p.setBrush(QColor(0, 0, 0, 128))
        p.drawRoundedRect(r, 12 * s, 12 * s)
        p.setPen(colour)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)
