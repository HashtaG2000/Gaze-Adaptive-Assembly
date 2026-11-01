"""P0 - Participant intake: ID, questionnaire (config.intake) and the item to build.

The participant ID follows config.participant_id (default: exactly 7 characters, A-Z and 0-9).
Typing is upper-cased automatically, longer input is refused, and an ID that already has a
session folder is rejected (unless allow_duplicates).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIntValidator, QValidator
from PySide6.QtWidgets import (
    QButtonGroup, QGridLayout, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget,
)

from app.config import AppConfig, FormQuestion
from app.i18n import t
from app.models import InstructionSet
from app.ui.style import OptionCard, ScaledScreen, ScreenHeader, button, label, page_layout

SAFE_ID_CHARS = re.compile(r"^[A-Z0-9_-]*$")


class ParticipantIdValidator(QValidator):
    """Upper-cases input, refuses unsafe characters and over-long IDs."""

    def __init__(self, pattern: str, length: int, parent=None) -> None:
        super().__init__(parent)
        self.rx = re.compile(pattern)
        self.length = length

    def validate(self, text: str, pos: int):  # noqa: D401 (Qt API)
        up = text.upper()
        if len(up) > self.length or not SAFE_ID_CHARS.match(up):
            return QValidator.State.Invalid, text, pos
        if len(up) == self.length and self.rx.fullmatch(up):
            return QValidator.State.Acceptable, up, pos
        return QValidator.State.Intermediate, up, pos


class _Question(QWidget):
    """One intake question in a light card; value() returns None when unanswered."""

    def __init__(self, q: FormQuestion, on_change: Callable[[], None]) -> None:
        super().__init__()
        self.q = q
        self.setObjectName("qcard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 16)
        title = q.label + ("" if q.required else f" {t('intake.optional')}")
        lay.addWidget(label(title, "field", align=Qt.AlignmentFlag.AlignLeft))
        self.group: Optional[QButtonGroup] = None
        self.edit: Optional[QWidget] = None
        self.number = False
        if q.type == "choice":
            self.group = QButtonGroup(self)
            self.group.setExclusive(True)
            grid = QGridLayout()
            grid.setSpacing(12)
            cols = min(2, len(q.options))      # 2x2 option cards inside a half-width card
            for i, opt in enumerate(q.options):
                card = OptionCard(opt)
                self.group.addButton(card, i)
                grid.addWidget(card, i // cols, i % cols)
            self.group.idToggled.connect(lambda *_: on_change())
            lay.addLayout(grid)
        elif q.type == "number":
            # a plain field: digits only, no spin arrows; out-of-range values count as unanswered
            self.lo = q.min if q.min is not None else 0
            self.hi = q.max if q.max is not None else 999
            edit = QLineEdit()
            edit.setValidator(QIntValidator(0, self.hi, edit))
            edit.setMaxLength(len(str(self.hi)))
            edit.setPlaceholderText(t("intake.number_hint", min=self.lo, max=self.hi))
            edit.textChanged.connect(lambda *_: on_change())
            self.number = True
            self.edit = edit
            lay.addWidget(edit)
        else:
            edit = QLineEdit()
            edit.setMaxLength(q.max_length)
            edit.textChanged.connect(lambda *_: on_change())
            self.edit = edit
            lay.addWidget(edit)

    def value(self) -> Any:
        if self.group is not None:
            i = self.group.checkedId()
            return self.q.options[i] if i >= 0 else None
        if self.number and isinstance(self.edit, QLineEdit):
            text = self.edit.text().strip()
            if not text.isdigit():
                return None
            v = int(text)
            return v if self.lo <= v <= self.hi else None
        if isinstance(self.edit, QLineEdit):
            return self.edit.text().strip()[: self.q.max_length] or None
        return None

    def reset(self) -> None:
        if self.group is not None:
            self.group.setExclusive(False)
            for b in self.group.buttons():
                b.setChecked(False)
            self.group.setExclusive(True)
        elif isinstance(self.edit, QLineEdit):
            self.edit.clear()


class IntakeScreen(ScaledScreen):
    submitted = Signal(str, object, object, object)  # participant_id, answers dict, set path, InstructionSet

    def __init__(self, cfg: AppConfig, sets: list[tuple[Path, InstructionSet]],
                 id_exists: Callable[[str], bool], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.sets = sets
        self.id_exists = id_exists
        pid_cfg = cfg.participant_id

        self.header = ScreenHeader("welcome", "blue")
        self.header.set(t("intake.title"), t("intake.subtitle"))

        # left: details as a two-column grid of cards (fits the screen without scrolling)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(14)
        ll.addWidget(label(t("intake.section_details"), "section", align=Qt.AlignmentFlag.AlignLeft))
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(14)
        id_card = QWidget(); id_card.setObjectName("qcard")
        id_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        il = QVBoxLayout(id_card)
        il.setContentsMargins(20, 14, 20, 16)
        il.addWidget(label(t("intake.id_label"), "field", align=Qt.AlignmentFlag.AlignLeft))
        self.pid = QLineEdit()
        self.pid.setMaxLength(pid_cfg.length)
        self.pid.setPlaceholderText(pid_cfg.example)
        self.pid.setValidator(ParticipantIdValidator(pid_cfg.pattern, pid_cfg.length, self.pid))
        self.pid.textChanged.connect(self._validate)
        il.addWidget(self.pid)
        il.addWidget(label(t("intake.id_hint", length=pid_cfg.length), "small", align=Qt.AlignmentFlag.AlignLeft))
        self.questions = [_Question(q, self._validate) for q in cfg.intake.questions]
        for i, w in enumerate([id_card, *self.questions]):
            grid.addWidget(w, i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        ll.addLayout(grid)
        ll.addStretch(1)

        # right: item to build
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(14)
        rl.addWidget(label(t("intake.section_item"), "section", align=Qt.AlignmentFlag.AlignLeft))
        self.item_group = QButtonGroup(self)
        self.item_group.setExclusive(True)
        for i, (_, iset) in enumerate(self.sets):
            card = OptionCard(iset.title, t("intake.item_steps", n=iset.n_steps), "item")
            self.item_group.addButton(card, i)
            rl.addWidget(card)
        rl.addStretch(1)
        self.item_group.idToggled.connect(lambda *_: self._validate())

        cols = QHBoxLayout()
        cols.setSpacing(40)
        cols.addWidget(left, 3)
        cols.addWidget(right, 1)
        right.setVisible(len(self.sets) != 1)   # a single item is selected automatically

        self.error = label("", "error")
        self.btn_start = button(t("intake.proceed_calibration") + "  ", "primary", "next", trailing=True)
        self.btn_start.clicked.connect(self._submit)
        buttons = QHBoxLayout()
        buttons.addWidget(self.error, 1)
        buttons.addWidget(self.btn_start)

        lay = page_layout(self)
        lay.addWidget(self.header)
        lay.addSpacing(26)
        lay.addLayout(cols, 1)
        lay.addSpacing(16)
        lay.addLayout(buttons)
        self.reset()

    # --- public -------------------------------------------------------------------------
    def set_calibration_next(self, enabled: bool) -> None:
        self.btn_start.setText((t("intake.proceed_calibration") if enabled else t("intake.proceed_start")) + "  ")

    def reset(self) -> None:
        self.pid.clear()
        for q in self.questions:
            q.reset()
        self.item_group.setExclusive(False)
        for b in self.item_group.buttons():
            b.setChecked(False)
        self.item_group.setExclusive(True)
        if len(self.sets) == 1:
            self.item_group.button(0).setChecked(True)
        self.error.setText("" if self.sets else t("intake.err_no_sets"))
        self._validate()

    def set_error(self, text: str) -> None:
        self.error.setText(text)
        self._validate()

    def answers(self) -> dict[str, Any]:
        return {q.q.id: q.value() for q in self.questions}

    # --- internals ------------------------------------------------------------------------
    def _pid_ok(self) -> bool:
        return self.cfg.participant_id.is_valid(self.pid.text())

    def _missing(self) -> list[str]:
        missing = [] if self._pid_ok() else [t("intake.id_label")]
        ans = self.answers()
        missing += [q.q.label for q in self.questions if q.q.required and ans[q.q.id] is None]
        if self.item_group.checkedId() < 0:
            missing.append(t("intake.section_item").capitalize())
        return missing

    def _validate(self) -> None:
        if self._pid_ok():
            self.header.set_chip(t("intake.id_chip", id=self.pid.text()), "neutral", icons=("user",))
        else:
            self.header.set_chip("", visible=False)
        self.btn_start.setEnabled(not self._missing() and bool(self.sets))

    def _submit(self) -> None:
        missing = self._missing()
        if missing:
            self.error.setText(t("intake.err_missing", fields=", ".join(missing)))
            return
        pid = self.pid.text()
        if not self.cfg.participant_id.allow_duplicates and self.id_exists(pid):
            self.error.setText(t("intake.err_id_exists"))
            return
        self.btn_start.setEnabled(False)
        path, iset = self.sets[self.item_group.checkedId()]
        self.submitted.emit(pid, self.answers(), path, iset)
