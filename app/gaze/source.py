"""Abstract gaze source interface.

A GazeSource emits `sample(GazeSample)` for every gaze sample and exposes a status
(connected / samples arriving / live sample rate). Subclasses implement start() and stop()
and call self._emit_sample() for each sample.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from app import clock
from app.models import GazeSample

RATE_WINDOW_S = 1.0       # window for the live sample rate
RECEIVING_TIMEOUT_S = 0.5  # "samples arriving" if the last sample is newer than this


@dataclass(frozen=True)
class GazeStatus:
    connected: bool
    receiving: bool
    rate_hz: float
    message: str = ""


class GazeSource(QObject):
    sample = Signal(object)   # GazeSample
    status_changed = Signal(object)  # GazeStatus, emitted on connect/disconnect/error

    name: str = "abstract"

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._connected = False
        self._message = ""
        self._recent: deque[float] = deque()

    # --- to implement -------------------------------------------------------------
    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    # --- shared -------------------------------------------------------------------
    def status(self) -> GazeStatus:
        t = clock.now()
        self._prune(t)
        receiving = bool(self._recent) and (t - self._recent[-1]) < RECEIVING_TIMEOUT_S
        rate = len(self._recent) / RATE_WINDOW_S
        return GazeStatus(self._connected, receiving, rate, self._message)

    def _set_connected(self, connected: bool, message: str = "") -> None:
        self._connected = connected
        self._message = message
        if not connected:
            self._recent.clear()
        self.status_changed.emit(self.status())

    def _emit_sample(self, s: GazeSample) -> None:
        self._recent.append(s.t)
        self._prune(s.t)
        self.sample.emit(s)

    def _prune(self, t: float) -> None:
        while self._recent and t - self._recent[0] > RATE_WINDOW_S:
            self._recent.popleft()
