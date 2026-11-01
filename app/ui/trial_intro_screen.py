"""Trial intro (participant-facing): the LLM's message, what changes, and that the next run is a
trial run which does not count as a repetition.

Buttons: "Start trial run" (accepted) / "Keep my current layout" (rejected).
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.i18n import t
from app.ui.icons import Chip, pixmap
from app.ui.style import ACCENT, ScaledScreen, ScreenHeader, button, divider, label, page_layout


class _ChangeItem(QWidget):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.mark = QLabel()
        self.text = label(text, "body", align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)
        row.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.text, 1)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        px = max(14, self.text.fontMetrics().height())
        self.mark.setPixmap(pixmap("check_circle", ACCENT, px))


class TrialIntroScreen(ScaledScreen):
    accepted = Signal()
    rejected = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.header = ScreenHeader("proposal", "blue")
        self.header.set(t("trial_intro.title"), t("trial_intro.subtitle"))
        self.message = label("", "msgcard", align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.level_chip = Chip("", "emerald", ["level_up"])
        self.level_chip.hide()
        self.items_box = QVBoxLayout()
        self.items_box.setSpacing(14)
        self.note = label(t("trial_intro.note"), "muted", align=Qt.AlignmentFlag.AlignLeft)
        self.btn_reject = button("  " + t("trial_intro.keep_current"), "secondary", "revert")
        self.btn_accept = button(t("trial_intro.start") + "  ", "success", "play", trailing=True)
        self.btn_reject.clicked.connect(lambda: self._decide(self.rejected))
        self.btn_accept.clicked.connect(lambda: self._decide(self.accepted))
        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_reject)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_accept)
        chip_row = QHBoxLayout()
        chip_row.addWidget(self.level_chip)
        chip_row.addStretch(1)

        lay = page_layout(self)
        lay.addWidget(self.header)
        lay.addSpacing(30)
        lay.addWidget(self.message)
        lay.addSpacing(16)
        lay.addLayout(chip_row)
        lay.addSpacing(20)
        lay.addWidget(label(t("trial_intro.changes_title"), "section", align=Qt.AlignmentFlag.AlignLeft))
        lay.addSpacing(12)
        lay.addLayout(self.items_box)
        lay.addStretch(1)
        lay.addWidget(self.note)
        lay.addSpacing(20)
        lay.addWidget(divider())
        lay.addSpacing(24)
        lay.addLayout(buttons)

    def show_intro(self, message: str, items: list[str], level_up: Optional[str] = None) -> None:
        self.message.setText(message)
        while self.items_box.count():
            w = self.items_box.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for text in items:
            self.items_box.addWidget(_ChangeItem(text))
        self.level_chip.setText(level_up or "")
        self.level_chip.setVisible(bool(level_up))
        self.btn_accept.setEnabled(True)
        self.btn_reject.setEnabled(True)

    def _decide(self, signal) -> None:
        if not self.btn_accept.isEnabled():
            return
        self.btn_accept.setEnabled(False)
        self.btn_reject.setEnabled(False)
        signal.emit()
