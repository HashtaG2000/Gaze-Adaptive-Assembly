"""Workspace -> Live: everything the experimenter needs while a participant works.

    pre-flight     settings + hardware telemetry + Start session (collapses while a session runs)
    mirror         schematic of the participant screen: AOIs, current step, live point of regard
    camera check   reference image | board photo of the latest (or selected) step, the model's
                   verdict with confidence, Correct / Error to confirm or correct it, review queue
    observer       live Correct / Error per step (Ctrl+1 / Ctrl+2), streak
    feedback       the participant's latest layout answers
    calibration    eye-tracker check result: Accept & start / Repeat / Skip
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget,
)

from app.config import AppConfig
from app.i18n import section, t
from app.loop.controller import SessionController, State
from app.models import CalibrationRecord, StepEvent, VerificationRecord
from app.ui.icons import icon
from app.ui.mirror import StimulusMirror
from app.ui.session_tabs import describe_check, verdict_colour
from app.ui.style import C
from app.ui.workspace_widgets import ImagePair, btn, card, plain, section_label, value_tile


class LivePage(QWidget):
    def __init__(self, cfg: AppConfig, config_box: Optional[QWidget] = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.media_root = cfg.resolve(cfg.paths.media)
        self.ctl: Optional[SessionController] = None
        self.config_box = config_box
        self._obs_counts = {"correct": 0, "error": 0}
        self._shown_check: Optional[int] = None
        self._follow_latest = True

        self.status = plain(t("cockpit.no_session"), "muted")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        if config_box is not None:
            lay.addWidget(config_box)
        lay.addWidget(self.status)
        grid = QHBoxLayout()
        grid.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(10)
        left.addWidget(self._build_mirror(), 5)
        left.addWidget(self._build_camera(), 6)
        right = QVBoxLayout()
        right.setSpacing(10)
        right.addWidget(self._build_observer())
        right.addWidget(self._build_feedback())
        right.addWidget(self._build_calibration())
        right.addStretch(1)
        grid.addLayout(left, 3)
        grid.addLayout(right, 2)
        lay.addLayout(grid, 1)

    # --- cards ---------------------------------------------------------------------------------
    def _build_mirror(self) -> QWidget:
        self.mirror = StimulusMirror(self.cfg)
        self.mirror_page = plain("", "muted", wrap=False)
        self.por_label = plain("", "mono", wrap=False)
        self.overlay_check = QCheckBox(t("cockpit.overlay"))
        self.overlay_check.toggled.connect(self._toggle_overlay)
        w = card()
        ml = QVBoxLayout(w)
        ml.setContentsMargins(14, 10, 14, 10)
        head = QHBoxLayout()
        head.addWidget(section_label(t("cockpit.mirror")))
        head.addStretch(1)
        head.addWidget(self.mirror_page)
        ml.addLayout(head)
        ml.addWidget(self.mirror, 1)
        foot = QHBoxLayout()
        foot.addWidget(self.por_label)
        foot.addStretch(1)
        foot.addWidget(self.overlay_check)
        ml.addLayout(foot)
        return w

    def _build_camera(self) -> QWidget:
        self.pair = ImagePair()
        self.check_info = plain(t("workspace.camera_idle"), "")
        self.check_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.btn_check_ok = btn(t("cockpit.correct"), "check", "ok")
        self.btn_check_err = btn(t("cockpit.error"), "close", "danger")
        self.btn_check_ok.clicked.connect(lambda: self._review("correct"))
        self.btn_check_err.clicked.connect(lambda: self._review("error"))
        self.queue = QListWidget()
        self.queue.setMaximumHeight(96)
        self.queue.itemClicked.connect(lambda it: self.show_check(it.data(Qt.ItemDataRole.UserRole), follow=False))
        self.camera_state = plain("", "muted", wrap=False)
        w = card()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 10, 14, 10)
        head = QHBoxLayout()
        head.addWidget(section_label(t("workspace.camera_check")))
        head.addStretch(1)
        head.addWidget(self.camera_state)
        lay.addLayout(head)
        lay.addWidget(self.pair, 1)
        row = QHBoxLayout()
        row.addWidget(self.check_info, 1)
        row.addWidget(self.btn_check_ok)
        row.addWidget(self.btn_check_err)
        lay.addLayout(row)
        lay.addWidget(section_label(t("workspace.review_queue")))
        lay.addWidget(self.queue)
        return w

    def _build_observer(self) -> QWidget:
        self.obs_label = plain(t("cockpit.observer_idle"))
        self.obs_counts = plain("", "muted")
        self.streak_label = plain("", "tile_value", wrap=False)
        self.btn_correct = btn(t("cockpit.correct") + "  (Ctrl+1)", "check", "ok")
        self.btn_error = btn(t("cockpit.error") + "  (Ctrl+2)", "close", "danger")
        self.btn_correct.clicked.connect(lambda: self.observe("correct"))
        self.btn_error.clicked.connect(lambda: self.observe("error"))
        w = card()
        ol = QVBoxLayout(w)
        ol.setContentsMargins(14, 10, 14, 12)
        ol.addWidget(section_label(t("cockpit.observer")))
        ol.addWidget(self.obs_label)
        brow = QHBoxLayout()
        brow.addWidget(self.btn_correct)
        brow.addWidget(self.btn_error)
        ol.addLayout(brow)
        crow = QHBoxLayout()
        crow.addWidget(self.obs_counts, 1)
        crow.addWidget(self.streak_label)
        ol.addLayout(crow)
        return w

    def _build_feedback(self) -> QWidget:
        w = card()
        fl = QVBoxLayout(w)
        fl.setContentsMargins(14, 10, 14, 12)
        fl.addWidget(section_label(t("cockpit.feedback_cache")))
        grid = QGridLayout()
        grid.setSpacing(8)
        self.fb_values: dict[str, QLabel] = {}
        for i, key in enumerate(("fb_difficulty", "fb_compact", "fb_removed", "fb_shown", "fb_status")):
            tile, val = value_tile(t(f"cockpit.{key}"))
            self.fb_values[key] = val
            grid.addWidget(tile, i // 2, i % 2, 1, 2 if key == "fb_status" else 1)
        fl.addLayout(grid)
        return w

    def _build_calibration(self) -> QWidget:
        self.cal_label = plain(t("cockpit.calib_none"))
        self.btn_accept = btn(t("cockpit.accept_start"), "check", "ok")
        self.btn_repeat = btn(t("cockpit.repeat_check"), "refresh", "warn")
        self.btn_skip = btn(t("cockpit.skip_check"), "next")
        self.btn_accept.clicked.connect(lambda: self._calibration("accepted"))
        self.btn_repeat.clicked.connect(lambda: self._calibration("repeated"))
        self.btn_skip.clicked.connect(lambda: self._calibration("skipped"))
        w = card()
        cl = QVBoxLayout(w)
        cl.setContentsMargins(14, 10, 14, 12)
        cl.addWidget(section_label(t("cockpit.calib_gate")))
        cl.addWidget(self.cal_label)
        row = QHBoxLayout()
        for b in (self.btn_accept, self.btn_repeat, self.btn_skip):
            row.addWidget(b)
        cl.addLayout(row)
        return w

    # --- session ----------------------------------------------------------------------------
    def attach(self, c: SessionController) -> list:
        """Returns the (signal, slot) pairs the Workspace connects / disconnects."""
        self.ctl = c
        self._obs_counts = {"correct": 0, "error": 0}
        self._shown_check = None
        self._follow_latest = True
        self.mirror.clear()
        self.queue.clear()
        self.pair.show_pair(None, None)
        self.check_info.setText(t("workspace.camera_idle"))
        self.on_feedback({})
        self.on_streak(c.streak)
        self.cal_label.setText(t("cockpit.calib_none"))
        c.window.set_overlay_visible(self.overlay_check.isChecked())
        conns = [
            (c.observation_added, self.on_observation), (c.calibration_added, self.on_calibration),
            (c.feedback_updated, self.on_feedback), (c.streak_changed, self.on_streak),
            (c.window.page_changed, self.on_page), (c.player.step_event, self.on_step),
            (c.verification_added, self.on_verification), (c.verification_updated, self.on_verification),
        ]
        if c.source is not None:
            conns.append((c.source.sample, self.mirror.on_sample))
        return conns

    def on_state(self, state: str) -> None:
        if state == State.CALIBRATION.value:
            self.cal_label.setText(t("cockpit.calib_running"))
        self.update_mirror_state()

    def on_page(self, page: str) -> None:
        self.mirror_page.setText(t("cockpit.mirror_page", page=section("cockpit.page_names").get(page, page)))
        self.update_mirror_state()

    def on_step(self, e: StepEvent) -> None:
        if e.event == "start":
            self._obs_counts_text()
            self.update_mirror_state()

    def update_mirror_state(self) -> None:
        c = self.ctl
        if c is None:
            return
        page = c.window.page_name
        active = c.state in (State.PLAYING, State.TRIAL) and c.player.is_active
        iset = c.current_set
        text = ""
        if active and iset is not None and 0 <= c.player.current < iset.n_steps:
            text = iset.steps[c.player.current].text
        hidden = set(iset.settings.hidden_elements) if iset else set()
        self.mirror.set_state(section("cockpit.page_names").get(page, page), active, hidden, text)
        self.obs_label.setText(t("cockpit.observer_check", run=c.run_label, step=c.player.current + 1)
                               if active else t("cockpit.observer_idle"))

    def on_observation(self, rec) -> None:
        if rec.source == "observer":
            self._obs_counts[rec.verdict] = self._obs_counts.get(rec.verdict, 0) + 1
        self._obs_counts_text()

    def _obs_counts_text(self) -> None:
        self.obs_counts.setText(t("cockpit.observer_counts", correct=self._obs_counts["correct"],
                                  errors=self._obs_counts["error"]))

    def on_streak(self, n: int) -> None:
        self.streak_label.setText(t("cockpit.streak_value", n=n))

    def on_feedback(self, fb: dict) -> None:
        if not fb:
            for lab in self.fb_values.values():
                lab.setText("–")
            return
        direction = fb.get("direction")
        compact = {"compress": t("cockpit.yes"), "keep": t("cockpit.no")}.get(direction, str(direction or "–"))
        self.fb_values["fb_difficulty"].setText(str(fb.get("difficulty") or "–"))
        self.fb_values["fb_compact"].setText(compact)
        self.fb_values["fb_removed"].setText(", ".join(fb.get("hide") or []) or "–")
        self.fb_values["fb_shown"].setText(", ".join(fb.get("show") or []) or "–")
        self.fb_values["fb_status"].setText(str(fb.get("status") or "–"))

    def on_calibration(self, rec: CalibrationRecord) -> None:
        if rec.mean_error_deg is None:
            self.cal_label.setText(t("calibration.result_no_data"))
            return
        status = t("calibration.result_pass" if rec.passed else "calibration.result_fail",
                   err=f"{rec.mean_error_deg:.2f}", limit=f"{self.cfg.calibration.max_error_deg:.1f}")
        self.cal_label.setText(t("cockpit.calib_result", err=f"{rec.mean_error_deg:.2f}",
                                 max=f"{rec.max_error_deg or 0:.2f}", status=status))

    # --- camera check -------------------------------------------------------------------------
    def on_verification(self, rec: VerificationRecord) -> None:
        self._refresh_queue()
        if self._follow_latest or rec.id == self._shown_check:
            self.show_check(rec.id, follow=self._follow_latest)

    def _refresh_queue(self) -> None:
        c = self.ctl
        self.queue.clear()
        if c is None:
            return
        for rec in c.review_queue:
            item = QListWidgetItem(t("workspace.queue_item", run=rec.run, step=rec.step_index + 1,
                                     why=(rec.error or (f"{rec.verdict} {rec.confidence:.0%}"
                                                        if rec.confidence is not None else rec.verdict or "?")))[:120])
            item.setIcon(icon("warning", C["amber_400"]))
            item.setData(Qt.ItemDataRole.UserRole, rec.id)
            self.queue.addItem(item)

    def show_check(self, check_id: Optional[int], follow: bool = True) -> None:
        c = self.ctl
        self._follow_latest = follow
        rec = c.verifications.get(check_id) if (c is not None and check_id is not None) else None
        self._shown_check = rec.id if rec else None
        self.btn_check_ok.setEnabled(rec is not None)
        self.btn_check_err.setEnabled(rec is not None)
        if rec is None:
            self.pair.show_pair(None, None)
            self.check_info.setText(t("workspace.camera_idle"))
            return
        photo = c.logger.dir / rec.photo if (c.logger and rec.photo) else None
        ref = self.media_root / rec.reference if rec.reference else None
        self.pair.show_pair(ref, photo, verdict_colour(rec))
        self.check_info.setText(describe_check(rec))

    def _review(self, verdict: str) -> None:
        if self.ctl is not None and self._shown_check is not None:
            self.ctl.review(self._shown_check, verdict)
            self._follow_latest = True
            nxt = self.ctl.review_queue
            if nxt:                                   # walk through the queue
                self.show_check(nxt[0].id, follow=False)

    # --- actions ------------------------------------------------------------------------------
    def observe(self, verdict: str) -> None:
        if self.ctl is not None:
            self.ctl.observe(verdict)

    def _calibration(self, decision: str) -> None:
        if self.ctl is not None:
            self.ctl.calibration_decision(decision)
            self.refresh()

    def _toggle_overlay(self, on: bool) -> None:
        if self.ctl is not None:
            self.ctl.window.set_overlay_visible(on)

    def refresh(self) -> None:
        """Periodic update (button states, POR, camera status)."""
        c = self.ctl
        live = c is not None and c.state != State.ENDED
        in_run = live and c.state in (State.PLAYING, State.TRIAL) and c.player.is_active
        in_cal = live and c.state == State.CALIBRATION
        self.btn_correct.setEnabled(bool(in_run))
        self.btn_error.setEnabled(bool(in_run))
        self.btn_accept.setEnabled(bool(in_cal and c.pending_calibration is not None))
        self.btn_repeat.setEnabled(bool(in_cal and c.pending_calibration is not None))
        self.btn_skip.setEnabled(bool(in_cal))
        if self.config_box is not None:
            self.config_box.setVisible(not live)       # pre-flight collapses while a session runs
        por = self.mirror.por
        self.por_label.setText(t("cockpit.por", x=f"{por[0]:.3f}", y=f"{por[1]:.3f}") + "   " +
                               t("cockpit.aoi", aoi=self.mirror.aoi) if por else t("cockpit.aoi", aoi=self.mirror.aoi))
        if c is not None and live:
            st = c.camera.status()
            pending = c.checks_pending
            self.camera_state.setText(t("workspace.camera_status", camera=st.message,
                                        pending=pending, review=len(c.review_queue)))
        else:
            self.camera_state.setText("")
