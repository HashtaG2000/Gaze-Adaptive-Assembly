"""Session setup (experimenter, Workspace -> Live): pre-flight settings and hardware telemetry.

The selected gaze source and board camera run as soon as they are chosen (so their status and the
camera preview are visible); "Start session" is enabled only while gaze samples arrive and no
session is running. On start both are handed to the session. The participant part (ID, questionnaire, item) is
the intake screen in the participant window (app/ui/intake_screen.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, get_args

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.camera.source import CAMERA_NAMES, CameraSource, list_cameras, make_camera
from app.gaze.source import GazeSource
from app.i18n import t
from app.llm.factory import CLIENT_NAMES
from app.models import InstructionSet, SessionMeta
from app.vision.clients import VISION_NAMES
from app.ui.icons import set_button_icon
from app.ui.style import C

CONDITIONS: tuple[str, ...] = get_args(SessionMeta.model_fields["condition"].annotation) or ("gaze", "time_only")
GAZE_SOURCES: tuple[str, ...] = ("mouse", "gazepoint", "mock")
LLM_CLIENTS: tuple[str, ...] = CLIENT_NAMES


@dataclass
class SetupChoice:
    participant_id: str
    instruction_set_path: Path
    instruction_set: InstructionSet
    condition: str
    gaze_source_name: str
    llm_client: str
    questionnaire: dict[str, Any] = field(default_factory=dict)
    gamification: bool = True   # gamification elements shown at session start
    calibration: bool = True    # eye-tracker check before the first repetition


SourceFactory = Callable[[str], GazeSource]


def card() -> QWidget:
    w = QWidget()
    w.setObjectName("card")
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    return w


def section_label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("section")
    return lab


class SessionConfigBox(QWidget):
    """Pre-flight card. start_requested(config dict, running GazeSource, CameraSource)."""
    start_requested = Signal(object, object, object)

    def __init__(self, source_factory: SourceFactory, camera_cfg=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.source_factory = source_factory
        self.camera_cfg = camera_cfg
        self.source: Optional[GazeSource] = None
        self.camera: Optional[CameraSource] = None
        self._source_error = ""
        self._camera_error = ""
        self._session_active = False

        self.condition = QComboBox()
        self.condition.addItems(list(CONDITIONS))
        self.gaze_combo = QComboBox()
        self.gaze_combo.addItems(list(GAZE_SOURCES))
        self.llm_combo = QComboBox()
        self.llm_combo.addItems(list(LLM_CLIENTS))
        self.gamify_check = QCheckBox(t("cockpit.gamification_start"))
        self.gamify_check.setChecked(True)
        self.calib_check = QCheckBox(t("cockpit.calibration_start"))
        self.calib_check.setChecked(True)
        self.camera_combo = QComboBox()
        self.camera_combo.addItems(list(CAMERA_NAMES))
        self.device_combo = QComboBox()
        self.checker_combo = QComboBox()
        self.checker_combo.addItems(list(VISION_NAMES))
        self.st_connected, self.st_receiving, self.st_rate = QLabel(), QLabel(), QLabel()
        self.st_camera = QLabel()
        self.st_message = QLabel()
        self.st_message.setWordWrap(True)
        self.st_message.setTextFormat(Qt.TextFormat.PlainText)
        self.st_message.setObjectName("muted")
        self.start_btn = QPushButton("  " + t("cockpit.start_session"))
        self.start_btn.setObjectName("start")
        set_button_icon(self.start_btn, "play", "#ffffff", disabled=C["zinc_400"])
        self.start_btn.setIconSize(QSize(18, 18))
        self.start_btn.clicked.connect(self._on_start)

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        form.addRow(t("cockpit.condition"), self.condition)
        form.addRow(t("cockpit.gaze_source"), self.gaze_combo)
        form.addRow(t("cockpit.llm_client"), self.llm_combo)
        cam_row = QHBoxLayout()
        cam_row.addWidget(self.camera_combo, 1)
        cam_row.addWidget(self.device_combo, 2)
        form.addRow(t("workspace.camera"), cam_row)
        form.addRow(t("workspace.checker"), self.checker_combo)
        form.addRow("", self.gamify_check)
        form.addRow("", self.calib_check)
        left = QVBoxLayout()
        left.addWidget(section_label(t("cockpit.preflight")))
        left.addLayout(form)

        tiles = QHBoxLayout()
        for caption, value in ((t("cockpit.tracker_connected"), self.st_connected),
                               (t("cockpit.samples_arriving"), self.st_receiving),
                               (t("cockpit.sample_rate"), self.st_rate),
                               (t("workspace.camera").upper(), self.st_camera)):
            tiles.addWidget(self._tile(caption, value))
        middle = QVBoxLayout()
        middle.addWidget(section_label(t("cockpit.telemetry")))
        middle.addLayout(tiles)
        middle.addWidget(self.st_message)

        box = card()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.addLayout(left, 2)
        lay.addSpacing(24)
        lay.addLayout(middle, 3)
        lay.addSpacing(24)
        lay.addWidget(self.start_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)

        self.gaze_combo.currentTextChanged.connect(self._switch_source)
        self.camera_combo.currentTextChanged.connect(lambda *_: self._switch_camera())
        self.device_combo.currentIndexChanged.connect(lambda *_: self._switch_camera())
        self._fill_devices()
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._switch_source(self.gaze_combo.currentText())
        self._switch_camera()

    @staticmethod
    def _tile(caption: str, value: QLabel) -> QWidget:
        w = QWidget()
        w.setObjectName("tile")
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(2)
        c = QLabel(caption)
        c.setObjectName("tile_caption")
        value.setObjectName("tile_value")
        v.addWidget(c)
        v.addWidget(value)
        return w

    def config(self) -> dict[str, Any]:
        return {"condition": self.condition.currentText(), "gaze_source_name": self.gaze_combo.currentText(),
                "llm_client": self.llm_combo.currentText(), "gamification": self.gamify_check.isChecked(),
                "calibration": self.calib_check.isChecked(), "camera": self.camera_combo.currentText(),
                "camera_device": max(0, self.device_combo.currentIndex()),
                "checker": self.checker_combo.currentText()}

    def set_session_active(self, active: bool) -> None:
        """Lock the controls while a session runs; restart the source preview afterwards."""
        self._session_active = active
        for w in (self.condition, self.gaze_combo, self.llm_combo, self.gamify_check, self.calib_check,
                  self.camera_combo, self.device_combo, self.checker_combo):
            w.setEnabled(not active)
        if not active and self.source is None:
            self._switch_source(self.gaze_combo.currentText())
        if not active and self.camera is None:
            self._switch_camera()
        self._refresh()

    # --- camera ------------------------------------------------------------------------------
    def _fill_devices(self) -> None:
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        try:
            names = list_cameras()
        except Exception:  # noqa: BLE001 - multimedia backend unavailable
            names = []
        self.device_combo.addItems(names or [t("workspace.no_cameras")])
        if self.camera_cfg is not None and self.camera_cfg.device_index < len(names):
            self.device_combo.setCurrentIndex(self.camera_cfg.device_index)
        self.device_combo.blockSignals(False)

    def stop_camera(self) -> None:
        if self.camera is not None:
            try:
                self.camera.stop()
            finally:
                self.camera.deleteLater()
                self.camera = None

    def _switch_camera(self) -> None:
        if self._session_active or self.camera_cfg is None:
            return
        self.stop_camera()
        self._camera_error = ""
        name = self.camera_combo.currentText()
        self.device_combo.setVisible(name == "usb")
        cfg = self.camera_cfg.model_copy(update={"device_index": max(0, self.device_combo.currentIndex())})
        try:
            self.camera = make_camera(name, cfg)
            self.camera.start()
        except Exception as exc:  # noqa: BLE001 - shown in the pre-flight
            self._camera_error = str(exc)
            self.camera = None
        self._refresh()

    def stop_source(self) -> None:
        if self.source is not None:
            try:
                self.source.stop()
            finally:
                self.source.deleteLater()
                self.source = None

    def _switch_source(self, name: str) -> None:
        if self._session_active:
            return
        self.stop_source()
        self._source_error = ""
        try:
            self.source = self.source_factory(name)
            self.source.start()
        except Exception as exc:  # noqa: BLE001 - shown in the cockpit
            self._source_error = f"{name}: {exc}"
            self.source = None
        self._refresh()

    @staticmethod
    def _yes_no(label: QLabel, ok: bool) -> None:
        label.setText(t("cockpit.yes") if ok else t("cockpit.no"))
        label.setStyleSheet(f"color: {C['emerald_400'] if ok else C['rose_400']};")

    def _refresh(self) -> None:
        st = self.source.status() if self.source is not None else None
        if self._session_active:
            for lab in (self.st_connected, self.st_receiving, self.st_rate, self.st_camera):
                lab.setText("–")
                lab.setStyleSheet("")
            self.st_message.setText(t("cockpit.session_running"))
        else:
            cam = self.camera.status() if self.camera is not None else None
            self.st_camera.setText(self._camera_error[:40] or (cam.message[:40] if cam else "–"))
            self.st_camera.setStyleSheet(f"color: {C['emerald_400'] if cam and cam.ready else C['zinc_400']};")
            self._yes_no(self.st_connected, bool(st and st.connected))
            self._yes_no(self.st_receiving, bool(st and st.receiving))
            self.st_rate.setText(f"{st.rate_hz:.0f} Hz" if st else "–")
            self.st_rate.setStyleSheet("")
            self.st_message.setText(self._source_error or (st.message if st else ""))
        self.start_btn.setEnabled(not self._session_active and bool(st and st.receiving))

    def _on_start(self) -> None:
        if self.source is None or self._session_active:
            return
        config = self.config()
        source, self.source = self.source, None   # hand ownership to the session
        camera, self.camera = self.camera, None
        self.start_requested.emit(config, source, camera)
