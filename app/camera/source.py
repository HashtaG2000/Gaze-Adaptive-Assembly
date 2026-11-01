"""Board camera: takes a photo of the assembly board on request.

    CameraSource   interface: start(), stop(), capture(request_id, path, hint), status()
    UsbCamera      USB webcam via Qt Multimedia (QCamera + QImageCapture)
    MockCamera     no hardware: "photographs" the step's reference image (optionally with a
                   simulated error every n-th photo), or draws a placeholder
    make_camera()  'usb' | 'mock' | 'off'

Photos are written as JPEG (atomically: temp file + rename). Results arrive asynchronously via
captured(request_id, path) / failed(request_id, message); nothing blocks the participant screen.
"""
from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter

from app.config import CameraConfig

CAMERA_NAMES = ("off", "usb", "mock")


@dataclass
class CameraStatus:
    ready: bool
    message: str = ""


def save_jpeg(img: QImage, path: Path, quality: int) -> bool:
    """Write atomically, so a reader never sees a half-written photo."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    ok = img.save(str(tmp), "JPG", quality)
    if ok:
        os.replace(tmp, path)
    elif tmp.exists():
        tmp.unlink()
    return ok


def list_cameras() -> list[str]:
    from PySide6.QtMultimedia import QMediaDevices
    return [d.description() for d in QMediaDevices.videoInputs()]


class CameraSource(QObject):
    captured = Signal(int, str)     # request id, photo path
    failed = Signal(int, str)       # request id, message
    name = "camera"

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def capture(self, request_id: int, path: Path, hint: Optional[Path] = None) -> None:
        raise NotImplementedError

    def status(self) -> CameraStatus:
        return CameraStatus(False, "no camera")

    def video_sink(self):
        """Live preview sink (QVideoSink) or None."""
        return None


class NoCamera(CameraSource):
    name = "off"

    def capture(self, request_id: int, path: Path, hint: Optional[Path] = None) -> None:
        QTimer.singleShot(0, lambda: self.failed.emit(request_id, "camera is off"))

    def status(self) -> CameraStatus:
        return CameraStatus(False, "camera off")


class MockCamera(CameraSource):
    """Copies the reference image (= a correctly built board); every n-th photo gets a red
    'missing brick' patch so the error path can be tested without hardware."""
    name = "mock"

    def __init__(self, cfg: CameraConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.count = 0

    def status(self) -> CameraStatus:
        return CameraStatus(True, "mock camera")

    def capture(self, request_id: int, path: Path, hint: Optional[Path] = None) -> None:
        self.count += 1
        error = self.cfg.mock_error_every_n > 0 and self.count % self.cfg.mock_error_every_n == 0
        QTimer.singleShot(self.cfg.settle_ms, lambda: self._write(request_id, Path(path), hint, error))

    def _write(self, request_id: int, path: Path, hint: Optional[Path], error: bool) -> None:
        img = QImage(str(hint)) if hint is not None and Path(hint).is_file() else QImage()
        if img.isNull():
            img = QImage(960, 540, QImage.Format.Format_RGB32)
            img.fill(QColor("#d9d4c7"))
        else:
            img = img.convertToFormat(QImage.Format.Format_RGB32)
        if error:   # a "misplaced brick": a dark patch in the middle of the board
            p = QPainter(img)
            p.fillRect(QRectF(img.width() * 0.38, img.height() * 0.30, img.width() * 0.24, img.height() * 0.28),
                       QColor("#1b1b1b"))
            p.end()
        if save_jpeg(img, path, self.cfg.jpeg_quality):
            self.captured.emit(request_id, str(path))
        else:
            self.failed.emit(request_id, f"cannot write {path.name}")


class UsbCamera(CameraSource):
    """USB webcam. Requests that arrive before the camera is ready are queued."""
    name = "usb"

    def __init__(self, cfg: CameraConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtMultimedia import QCamera, QImageCapture, QMediaCaptureSession, QMediaDevices, QVideoSink
        self.cfg = cfg
        devices = QMediaDevices.videoInputs()
        if not devices:
            raise RuntimeError("no camera found - connect the USB webcam")
        if cfg.device_index >= len(devices):
            raise RuntimeError(f"camera {cfg.device_index} not found ({len(devices)} available)")
        self.device = devices[cfg.device_index]
        self.camera = QCamera(self.device, self)
        fmt = self._best_format(self.device, cfg.resolution)
        if fmt is not None:
            self.camera.setCameraFormat(fmt)
        self.session = QMediaCaptureSession(self)
        self.session.setCamera(self.camera)
        self.image_capture = QImageCapture(self)
        self.session.setImageCapture(self.image_capture)
        self.sink = QVideoSink(self)               # keeps frames flowing (and feeds previews)
        self.session.setVideoSink(self.sink)
        self._pending: deque[tuple[int, Path]] = deque()          # waiting for readiness
        self._in_flight: dict[int, tuple[int, Path]] = {}         # Qt capture id -> (request, path)
        self._error = ""
        self.image_capture.imageCaptured.connect(self._on_image)
        self.image_capture.errorOccurred.connect(self._on_error)
        self.image_capture.readyForCaptureChanged.connect(lambda ready: ready and self._drain())
        self.camera.errorOccurred.connect(lambda _e, msg: setattr(self, "_error", msg))

    @staticmethod
    def _best_format(device, resolution: tuple[int, int]):
        formats = device.videoFormats()
        if not formats:
            return None
        w, h = resolution
        return min(formats, key=lambda f: (abs(f.resolution().width() - w) + abs(f.resolution().height() - h),
                                           -f.maxFrameRate()))

    def start(self) -> None:
        self.camera.start()

    def stop(self) -> None:
        self.camera.stop()
        for req, _ in list(self._pending) + list(self._in_flight.values()):
            self.failed.emit(req, "camera stopped")
        self._pending.clear()
        self._in_flight.clear()

    def status(self) -> CameraStatus:
        if self._error:
            return CameraStatus(False, self._error)
        ready = self.camera.isActive() and self.image_capture.isReadyForCapture()
        return CameraStatus(ready, self.device.description() + ("" if ready else " (starting)"))

    def video_sink(self):
        return self.sink

    def capture(self, request_id: int, path: Path, hint: Optional[Path] = None) -> None:
        self._pending.append((request_id, Path(path)))
        QTimer.singleShot(self.cfg.settle_ms, self._drain)

    def _drain(self) -> None:
        while self._pending and self.image_capture.isReadyForCapture():
            req, path = self._pending.popleft()
            qt_id = self.image_capture.capture()
            if qt_id < 0:
                self.failed.emit(req, self.image_capture.errorString() or "capture failed")
                continue
            self._in_flight[qt_id] = (req, path)
        if self._pending and not self.camera.isActive():
            self.camera.start()

    def _on_image(self, qt_id: int, img: QImage) -> None:
        entry = self._in_flight.pop(qt_id, None)
        if entry is None:
            return
        req, path = entry
        if save_jpeg(img, path, self.cfg.jpeg_quality):
            self.captured.emit(req, str(path))
        else:
            self.failed.emit(req, f"cannot write {path.name}")

    def _on_error(self, qt_id: int, _err, message: str) -> None:
        entry = self._in_flight.pop(qt_id, None)
        if entry is not None:
            self.failed.emit(entry[0], message or "capture failed")


def make_camera(name: str, cfg: CameraConfig) -> CameraSource:
    if name == "usb":
        return UsbCamera(cfg)
    if name == "mock":
        return MockCamera(cfg)
    if name == "off":
        return NoCamera()
    raise ValueError(f"unknown camera {name!r}")
