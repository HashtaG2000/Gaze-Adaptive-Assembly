"""P5 feedback - after the trial run: keep the new layout or go back (participant-facing).

With gamification on, the header shows a star score for the trial run (time vs. reference).
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QWidget

from app.i18n import t
from app.ui.style import ScaledScreen, ScreenHeader, button, divider, label, page_layout


def stars_text(score: float) -> str:
    return t("feedback.stars", score=f"{score:.1f}")


def star_icons(score: float) -> list[str]:
    """Icon keys for a 0.5-step score, e.g. 3.5 -> 3 full, 1 half, 1 empty."""
    score = max(0.0, min(5.0, score))
    full = int(score)
    half = 1 if score - full >= 0.5 else 0
    return ["star"] * full + ["star_half"] * half + ["star_empty"] * (5 - full - half)


class TrialFeedbackScreen(ScaledScreen):
    keep = Signal()
    revert = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.header = ScreenHeader("feedback", "amber")
        self.header.set(t("feedback.title"), t("feedback.subtitle"))
        self.title = label(t("feedback.question"), "title")
        self.subtitle = label(t("feedback.text"), "muted")
        self.btn_keep = button("  " + t("feedback.keep"), "success", "check_double")
        self.btn_revert = button("  " + t("feedback.revert"), "danger", "revert")
        self.btn_keep.clicked.connect(lambda: self._decide(self.keep))
        self.btn_revert.clicked.connect(lambda: self._decide(self.revert))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.btn_revert)
        buttons.addSpacing(36)
        buttons.addWidget(self.btn_keep)
        buttons.addStretch(1)
        lay = page_layout(self)
        lay.addWidget(self.header)
        lay.addStretch(3)
        lay.addWidget(self.title)
        lay.addSpacing(30)
        lay.addWidget(self.subtitle)
        lay.addStretch(4)
        lay.addWidget(divider())
        lay.addSpacing(30)
        lay.addLayout(buttons)

    def reset(self, score: Optional[float] = None) -> None:
        """score: gamification star score 0-5 (None = no chip)."""
        if score is None:
            self.header.set_chip("", "amber", visible=False)
        else:
            self.header.set_chip(stars_text(score), "amber", icons=tuple(star_icons(score)), icon_color="#f59e0b")
        self.btn_keep.setEnabled(True)
        self.btn_revert.setEnabled(True)

    def _decide(self, signal) -> None:
        if not self.btn_keep.isEnabled():
            return
        self.btn_keep.setEnabled(False)
        self.btn_revert.setEnabled(False)
        signal.emit()
