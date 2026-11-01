"""Workspace: the experimenter's window (separate, dark; never shown to the participant).

    Header   live chips (tracker, state, participant, run, camera checks) + Drift check, Export, End
    Sidebar  Live | Recordings | Enrichments | Visualizations | Downloads

    Live            pre-flight + Start session, mirror, camera check, observer, feedback, calibration
    Recordings      every stored session with details -> open in Visualizations
    Enrichments     instruction sets, media library, reference images, LLM rules
    Visualizations  all analysis for the live session or a recording
    Downloads       every file the app produced (sessions, exports, AOI layout, configuration)
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QStackedWidget, QVBoxLayout,
    QWidget,
)

from app.camera.source import CameraSource, NoCamera
from app.config import AppConfig
from app.core.repository import ContentRepository
from app.i18n import t
from app.loop.controller import SessionController, State
from app.ui.downloads_page import DownloadsPage
from app.ui.enrichments import EnrichmentsPage
from app.ui.icons import Chip, IconTile, icon
from app.ui.live_page import LivePage
from app.ui.recordings_page import RecordingsPage
from app.ui.style import C, cockpit_stylesheet
from app.ui.visualizations_page import VisualizationsPage
from app.ui.workspace_widgets import btn, export_dir

STATUS_MS = 500
PAGES = (("live", "live"), ("recordings", "sessions"), ("enrichments", "author"), ("visualizations", "chart"),
         ("downloads", "import"))


class Workspace(QMainWindow):
    def __init__(self, cfg: AppConfig, repo: ContentRepository, config_box: Optional[QWidget] = None,
                 preview_camera: Optional[Callable[[], Optional[CameraSource]]] = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.repo = repo
        self.ctl: Optional[SessionController] = None
        self._connections: list = []
        self._preview_camera = preview_camera or (lambda: None)
        self.setWindowTitle(t("app.researcher_window"))
        self.resize(1480, 940)

        # --- header -------------------------------------------------------------------------
        tile = IconTile("cockpit", "#ffffff", "icon_tile")
        t1 = QLabel(t("cockpit.title"))
        t1.setObjectName("app_title")
        t2 = QLabel(t("cockpit.subtitle"))
        t2.setObjectName("app_sub")
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(t1)
        titles.addWidget(t2)
        self.tracker_chip = Chip(t("cockpit.tracker_idle"), "neutral", ["dot"], dark=True)
        self.state_chip = Chip("", "neutral", ["live"], dark=True)
        self.pid_chip = Chip("", "blue", ["user"], dark=True)
        self.run_chip = Chip("", "purple", ["repetition"], dark=True)
        self.check_chip = Chip("", "amber", ["camera"], dark=True)
        self.drift_btn = btn(t("cockpit.drift_check"), "bullseye", "warn")
        self.export_btn = btn(t("cockpit.export"), "export")
        self.end_btn = btn(t("cockpit.end_session"), "power", "danger")
        self.drift_btn.clicked.connect(self._drift)
        self.export_btn.clicked.connect(self._export)
        self.end_btn.clicked.connect(self._end)
        header = QHBoxLayout()
        header.addWidget(tile)
        header.addSpacing(10)
        header.addLayout(titles, 1)
        for w in (self.tracker_chip, self.state_chip, self.pid_chip, self.run_chip, self.check_chip):
            header.addWidget(w)
        header.addSpacing(10)
        for w in (self.drift_btn, self.export_btn, self.end_btn):
            header.addWidget(w)

        # --- pages ----------------------------------------------------------------------------
        self.live = LivePage(cfg, config_box)
        self.recordings = RecordingsPage(cfg.resolve(cfg.paths.sessions))
        self.enrichments = EnrichmentsPage(repo, self.camera_for_references)
        self.visualizations = VisualizationsPage(cfg.resolve(cfg.paths.media))
        self.downloads = DownloadsPage(cfg)
        self.recordings.open_requested.connect(self.open_recording)
        self.visualizations.tabs.review_requested.connect(self._review)
        self.pages = {"live": self.live, "recordings": self.recordings, "enrichments": self.enrichments,
                      "visualizations": self.visualizations, "downloads": self.downloads}
        self.stack = QStackedWidget()
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setFixedWidth(200)
        for key, icon_key in PAGES:
            self.stack.addWidget(self.pages[key])
            item = QListWidgetItem(icon(icon_key, C["zinc_300"]), t(f"workspace.nav_{key}"))
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.nav.addItem(item)
        self.nav.currentRowChanged.connect(self._page_changed)

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        root = QWidget()
        root.setObjectName("root")
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(12)
        lay.addLayout(header)
        lay.addLayout(body, 1)
        self.setCentralWidget(root)
        self.setStyleSheet(cockpit_stylesheet())
        self.nav.setCurrentRow(0)

        # observer shortcuts (Workspace focused): Ctrl+1 correct, Ctrl+2 error
        QShortcut(QKeySequence("Ctrl+1"), self, activated=lambda: self.live.observe("correct"))
        QShortcut(QKeySequence("Ctrl+2"), self, activated=lambda: self.live.observe("error"))

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(STATUS_MS)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()
        self._refresh_status()

    # --- navigation -----------------------------------------------------------------------
    def show_page(self, key: str) -> None:
        for r in range(self.nav.count()):
            if self.nav.item(r).data(Qt.ItemDataRole.UserRole) == key:
                self.nav.setCurrentRow(r)
                return

    def _page_changed(self, row: int) -> None:
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)
            key = self.nav.item(row).data(Qt.ItemDataRole.UserRole)
            if key == "recordings":
                self.recordings.refresh()
            elif key == "downloads":
                self.downloads.refresh()

    def open_recording(self, folder: Path) -> None:
        if self.visualizations.open_archive(Path(folder)):
            self.show_page("visualizations")

    def camera_for_references(self) -> Optional[CameraSource]:
        """The running session's camera, else the pre-flight preview camera."""
        c = self.ctl
        if c is not None and c.state != State.ENDED and not isinstance(c.camera, NoCamera):
            return c.camera
        return self._preview_camera()

    # --- session ----------------------------------------------------------------------------
    def attach(self, controller: SessionController) -> None:
        """Show this session (clears the previous session's live views)."""
        for sig, slot in self._connections:
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self.ctl = c = controller
        tabs = self.visualizations.tabs
        self._connections = self.live.attach(c) + [
            (c.state_changed, self._on_state), (c.started, self._on_started), (c.ended, self._on_ended),
            (c.summary_added, self._live_tab(tabs.add_summary)), (c.summary_updated, self._live_tab(tabs.update_summary)),
            (c.llm_call_added, self._live_tab(tabs.add_llm_call)), (c.decision_added, self._live_tab(tabs.add_decision)),
            (c.version_added, self._live_tab(tabs.add_version)),
            (c.verification_added, self._live_tab(tabs.add_verification)),
            (c.verification_updated, self._live_tab(tabs.add_verification)),
        ]
        for sig, slot in self._connections:
            sig.connect(slot)
        self.visualizations.set_live(c)
        self.show_page("live")
        self._refresh_status()

    def _live_tab(self, fn):
        """Forward live updates to Visualizations only while it shows the live session."""
        def slot(*args):
            if self.visualizations.showing_live:
                fn(*args)
        return slot

    def _review(self, check_id: int, verdict: str) -> None:
        if self.ctl is not None and self.visualizations.showing_live:
            self.ctl.review(check_id, verdict)

    def _on_started(self) -> None:
        if self.ctl is not None:
            self.visualizations.set_live(self.ctl)      # participant ID / intake known now
            self.recordings.set_live(self.ctl.logger.dir if self.ctl.logger else None)
        self._refresh_status()

    def _on_state(self, state: str) -> None:
        self.live.on_state(state)
        self._refresh_status()

    def _on_ended(self) -> None:
        c = self.ctl
        if c is not None and self.visualizations.showing_live:
            self.visualizations.tabs.set_survey(c.survey.score if c.survey else None, c.survey is not None)
        self.live.mirror.clear()
        self.recordings.set_live(None)
        self._refresh_status()

    # --- actions ----------------------------------------------------------------------------
    def _drift(self) -> None:
        if self.ctl is None:
            return
        self.ctl.request_drift_check()
        if self.ctl.drift_requested:
            self.live.status.setText(t("cockpit.drift_queued"))

    def _export(self) -> None:
        if self.ctl is None or self.ctl.logger is None:
            return
        self.ctl.logger.flush()
        export_dir(self.ctl.logger.dir, self)

    def _end(self) -> None:
        if self.ctl is None or self.ctl.state == State.ENDED:
            return
        if QMessageBox.question(self, t("cockpit.confirm_end_title"), t("cockpit.confirm_end")) \
                == QMessageBox.StandardButton.Yes:
            self.ctl.end_session()

    # --- header -----------------------------------------------------------------------------
    @staticmethod
    def _chip(chip: Chip, text: str, kind: Optional[str] = None) -> None:
        chip.setText(text)
        chip.setVisible(bool(text))
        if kind is not None and chip.kind != kind:
            chip.set_kind(kind)

    def _refresh_status(self) -> None:
        c = self.ctl
        live = c is not None and c.state != State.ENDED
        self.live.refresh()
        self.drift_btn.setEnabled(bool(live and c.choice is not None and c.state not in (
            State.CALIBRATION, State.SURVEY, State.COMPLETE)))
        self.end_btn.setEnabled(bool(live))
        self.export_btn.setEnabled(c is not None and c.logger is not None)
        if c is None:
            for chip in (self.state_chip, self.pid_chip, self.run_chip, self.check_chip):
                self._chip(chip, "")
            self._chip(self.tracker_chip, t("cockpit.tracker_idle"), "neutral")
            return
        self._chip(self.state_chip, c.state.value, "bad" if c.state == State.ENDED else "blue")
        if c.source is not None and live:
            st = c.source.status()
            self._chip(self.tracker_chip, t("cockpit.tracker_rate", rate=f"{st.rate_hz:.0f}") if st.receiving
                       else t("cockpit.tracker_none"), "ok" if st.receiving else "bad")
        else:
            self._chip(self.tracker_chip, t("cockpit.tracker_stopped"), "neutral")
        review = len(c.review_queue)
        self._chip(self.check_chip, t("workspace.chip_review", n=review) if review else "", "amber")
        if c.choice is None:
            self._chip(self.pid_chip, "")
            self._chip(self.run_chip, "")
            self.live.status.setText(t("cockpit.waiting_intake", condition=c.config.get("condition", "?")))
            return
        self._chip(self.pid_chip, c.choice.participant_id)
        self._chip(self.run_chip, c.run_label if c.run else "")
        cur = c.current_set
        if self.live.status.text() != t("cockpit.drift_queued") or not c.drift_requested:
            parts = [c.choice.condition, f"gaze {c.choice.gaze_source_name}", f"LLM {c.choice.llm_client}",
                     f"camera {c.camera.name}", f"checker {getattr(c.checker, 'name', 'off')}"]
            if cur is not None:
                parts.append(f"v{cur.version_level} · merge {cur.settings.merge_level} · "
                             f"hidden: {', '.join(cur.settings.hidden_elements) or '–'}")
            if c.logger is not None:
                parts.append(str(c.logger.dir))
            self.live.status.setText("  ·  ".join(parts))

    def closeEvent(self, event) -> None:
        if self.ctl is not None and self.ctl.state != State.ENDED:
            if QMessageBox.question(self, t("cockpit.confirm_end_title"), t("cockpit.confirm_close")) \
                    != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.ctl.force_end()
        event.accept()
