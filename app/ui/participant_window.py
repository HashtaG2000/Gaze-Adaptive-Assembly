"""The single fullscreen participant window. Pages:

    intake       P0  participant ID, questionnaire, item to build
    calibration  P1  eye-tracker check (start of session, drift checks)
    player       P2  instruction player (regular repetitions and the trial run, P5)
    saved        P3  "Repetition N completed!"
    assess       P4  layout assessment (difficulty, compaction, elements)
    analysing        waiting while the LLM works
    trial_intro      what changes; the next run is a trial run
    feedback         keep / revert after the trial run
    survey           post-session questionnaire
    complete         thank you

Using one window keeps the gaze reference area identical for all screens.
Experimenter shortcut: Ctrl+Shift+Q ends the session immediately.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import QStackedWidget, QWidget

from app.config import AppConfig
from app.i18n import t
from app.models import InstructionSet
from app.ui.analysing_screen import AnalysingScreen
from app.ui.assessment_screen import AssessmentScreen
from app.ui.calibration_screen import CalibrationScreen
from app.ui.intake_screen import IntakeScreen
from app.ui.player_screen import PlayerScreen
from app.ui.saved_screen import SavedScreen
from app.ui.style import C, MONO_NAME
from app.ui.survey_screen import CompleteScreen, SurveyScreen
from app.ui.trial_feedback_screen import TrialFeedbackScreen
from app.ui.trial_intro_screen import TrialIntroScreen


class AoiOverlay(QWidget):
    """Testing aid: dashed AOI rectangles and the live gaze point on top of the participant
    window (toggle in the cockpit; keep it off during real sessions)."""

    def __init__(self, cfg: AppConfig, parent: QWidget) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._gaze: Optional[tuple[float, float]] = None
        self.hide()

    def on_sample(self, s) -> None:
        if self.isVisible():
            self._gaze = (s.x_norm, s.y_norm) if s.valid and s.x_norm is not None and s.y_norm is not None else None
            self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sx, sy = self.width() / self.cfg.screen.width, self.height() / self.cfg.screen.height
        f = QFont(MONO_NAME)
        f.setPixelSize(max(9, round(14 * min(sx, sy))))
        f.setBold(True)
        p.setFont(f)
        for name, r in self.cfg.aoi.items():
            rect = QRectF(r.x * sx, r.y * sy, r.w * sx, r.h * sy)
            p.setPen(QPen(QColor(C["blue_600"]), 1.5, Qt.PenStyle.DashLine))
            p.setBrush(QColor(37, 99, 235, 12))
            p.drawRect(rect)
            p.setPen(QColor(C["blue_700"]))
            p.drawText(rect.adjusted(6, 3, -6, -3), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                       f"AOI: {name} [x:{r.x}, y:{r.y}, w:{r.w}, h:{r.h}]")
        if self._gaze is not None:
            c = QPointF(self._gaze[0] * self.width(), self._gaze[1] * self.height())
            p.setPen(Qt.PenStyle.NoPen)
            gaze = QColor(C["gaze"])
            for rad, alpha in ((26, 50), (16, 110), (7, 230)):
                gaze.setAlpha(alpha)
                p.setBrush(gaze)
                p.drawEllipse(c, rad, rad)
        p.end()


class ParticipantWindow(QStackedWidget):
    end_requested = Signal()
    closed = Signal()
    page_changed = Signal(str)

    def __init__(self, cfg: AppConfig, sets: list[tuple[Path, InstructionSet]],
                 id_exists: Callable[[str], bool], media_root: Optional[Path] = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle(t("app.participant_window"))
        self.intake = IntakeScreen(cfg, sets, id_exists)
        self.intake.set_calibration_next(cfg.calibration.enabled)
        self.calibration = CalibrationScreen(cfg.calibration)
        self.player = PlayerScreen(cfg, media_root or cfg.resolve(cfg.paths.media))
        self.saved = SavedScreen()
        self.assess = AssessmentScreen()
        self.analysing = AnalysingScreen()
        self.trial_intro = TrialIntroScreen()
        self.feedback = TrialFeedbackScreen()
        self.survey = SurveyScreen()
        self.complete = CompleteScreen()
        self.pages: dict[str, QWidget] = {
            "intake": self.intake, "calibration": self.calibration, "player": self.player, "saved": self.saved,
            "assess": self.assess, "analysing": self.analysing, "trial_intro": self.trial_intro,
            "feedback": self.feedback, "survey": self.survey, "complete": self.complete,
        }
        for w in self.pages.values():
            self.addWidget(w)
        sc = QShortcut(QKeySequence("Ctrl+Shift+Q"), self)
        sc.activated.connect(self.end_requested.emit)
        self.overlay = AoiOverlay(cfg, self)

    @property
    def page_name(self) -> str:
        cur = self.currentWidget()
        return next((n for n, w in self.pages.items() if w is cur), "")

    def set_calibration_enabled(self, on: bool) -> None:
        self.intake.set_calibration_next(on and self.cfg.calibration.enabled)

    def set_overlay_visible(self, on: bool) -> None:
        self.overlay.setGeometry(self.rect())
        self.overlay.setVisible(on)
        self.overlay.raise_()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.overlay.setGeometry(self.rect())
        self.overlay.raise_()

    def show_page(self, page: QWidget) -> None:
        self.setCurrentWidget(page)
        page.setFocus()
        self.overlay.raise_()
        self.page_changed.emit(self.page_name)

    def closeEvent(self, event) -> None:
        self.closed.emit()
        event.accept()
