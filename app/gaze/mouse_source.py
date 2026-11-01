"""Mouse position as gaze (for testing without a tracker). Always valid."""
from __future__ import annotations

from PySide6.QtCore import QObject, QRect, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QWidget

from app import clock
from app.gaze.source import GazeSource
from app.models import GazeSample


class MouseSource(GazeSource):
    name = "mouse"

    def __init__(self, rate_hz: int = 60, monitor_index: int = 0, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._monitor_index = monitor_index
        self._reference: QWidget | None = None
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(max(1, round(1000 / rate_hz)))
        self._timer.timeout.connect(self._tick)

    def set_reference_widget(self, widget: QWidget | None) -> None:
        """Normalise against this widget's area instead of the whole monitor.

        In fullscreen the player covers the monitor, so both are identical. In windowed
        test mode this keeps mouse coordinates aligned with the player's AOIs.
        """
        self._reference = widget

    def start(self) -> None:
        self._timer.start()
        self._set_connected(True, "Mouse as gaze")

    def stop(self) -> None:
        self._timer.stop()
        self._set_connected(False)

    def _reference_rect(self) -> QRect:
        if self._reference is not None and self._reference.isVisible():
            w = self._reference
            return QRect(w.mapToGlobal(w.rect().topLeft()), w.size())
        screens = QGuiApplication.screens()
        screen = screens[self._monitor_index] if self._monitor_index < len(screens) else QGuiApplication.primaryScreen()
        return screen.geometry()

    def _tick(self) -> None:
        r = self._reference_rect()
        pos = QCursor.pos()
        x = (pos.x() - r.x()) / r.width()
        y = (pos.y() - r.y()) / r.height()
        self._emit_sample(GazeSample(t=clock.now(), x_norm=x, y_norm=y, valid=True))
