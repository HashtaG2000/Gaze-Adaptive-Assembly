"""P3 - Repetition completed (participant-facing): "Repetition N completed!" -> feedback."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from app.i18n import t
from app.ui.icons import Chip
from app.ui.style import ScaledScreen, button, icon_tile, label


class SavedScreen(ScaledScreen):
    continue_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.icon = icon_tile("check", "emerald_round")
        self.title = label("", "title")
        self.text = label("", "body")
        self.review = label(t("saved.review"), "muted")
        # achievements of this run (gamification only); hidden chips take no space
        self.badge_best = Chip(t("saved.badge_best"), "emerald", ["trophy"])
        self.badge_correct = Chip("", "blue", ["check_circle"])
        self.badge_points = Chip("", "amber", ["star"])
        self.badges = QHBoxLayout()
        self.badges.addStretch(1)
        for chip in (self.badge_best, self.badge_correct, self.badge_points):
            chip.hide()
            self.badges.addWidget(chip)
        self.badges.addStretch(1)
        self.btn = button(t("saved.continue") + "  ", "primary", "next", trailing=True)
        self.btn.clicked.connect(self._continue)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.btn)
        row.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(90, 60, 90, 60)
        lay.addStretch(3)
        lay.addWidget(self.icon, alignment=Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(40)
        lay.addWidget(self.title)
        lay.addSpacing(18)
        lay.addWidget(self.text)
        lay.addSpacing(26)
        lay.addLayout(self.badges)
        lay.addSpacing(26)
        lay.addWidget(self.review)
        lay.addSpacing(60)
        lay.addLayout(row)
        lay.addStretch(4)

    def show_saved(self, repetition: Optional[int], n_steps: int, duration_s: Optional[float] = None,
                   badges: Optional[dict] = None) -> None:
        """repetition None = trial run (not used by the controller, kept for completeness)."""
        self.title.setText(t("saved.title", n=repetition) if repetition is not None else t("saved.title_trial"))
        if duration_s is None:
            self.text.setText(t("saved.text", steps=n_steps))
        else:
            self.text.setText(t("saved.text_time", steps=n_steps, time=f"{duration_s:.1f}"))
        b = badges or {}
        self.badge_best.setVisible(bool(b.get("personal_best")))
        self.badge_correct.setText(t("saved.badge_correct", n=b.get("correct", 0)))
        self.badge_correct.setVisible(b.get("correct", 0) > 0)
        self.badge_points.setText(t("saved.badge_points", n=b.get("points", 0)))
        self.badge_points.setVisible(b.get("points", 0) > 0)
        self.btn.setEnabled(True)

    def _continue(self) -> None:
        if self.btn.isEnabled():
            self.btn.setEnabled(False)   # one click only (double clicks / buzzer bounce)
            self.continue_clicked.emit()
