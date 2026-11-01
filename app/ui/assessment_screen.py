"""P4 - Layout assessment after a repetition (participant-facing), two parts:

    P4-A  1. difficulty (config.feedback.difficulty_options)
          2. compaction: Yes (combine) / No (keep) / More detailed (only offered where possible)
    P4-B  elements to remove (or show again, if hidden), or "None of the above"

Emits finished(ParticipantRequest). The UI never allows an invalid request: compaction beyond
level 2 or below level 0 is not offered, and text, images and videos can never all be hidden.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QGridLayout, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from app.i18n import t
from app.models import CONTENT_ELEMENTS, HIDEABLE_ELEMENTS, InstructionSettings, ParticipantRequest
from app.ui.style import OptionCard, ScaledScreen, ScreenHeader, button, divider, label, page_layout

MAX_MERGE_LEVEL = 2
ICON = {"video": "video", "text": "text", "image": "image", "gamification": "gamification"}


def _label(key: str, fallback: str) -> str:
    value = t(key)
    return fallback if value == key else value


class _Page(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("screen")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


class AssessmentScreen(ScaledScreen):
    finished = Signal(object)   # ParticipantRequest

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = InstructionSettings()
        self.difficulty_options: list[str] = []
        self.stack = QStackedWidget()
        self.part1 = self._build_part1()
        self.part2 = self._build_part2()
        self.stack.addWidget(self.part1)
        self.stack.addWidget(self.part2)
        lay = page_layout(self)
        lay.addWidget(self.stack, 1)

    # --- part 1 ---------------------------------------------------------------------------
    def _build_part1(self) -> QWidget:
        page = _Page()
        self.header1 = ScreenHeader("difficulty", "blue")
        self.header1.set(t("assess.part1_title"), t("assess.part1_subtitle"))
        self.diff_group = QButtonGroup(self)
        self.diff_row = QHBoxLayout()
        self.diff_row.setSpacing(18)
        self.dir_group = QButtonGroup(self)
        self.dir_cards: dict[str, OptionCard] = {}
        dir_row = QHBoxLayout()
        dir_row.setSpacing(18)
        for i, (d, icon_key) in enumerate((("compress", "compact"), ("keep", "steps"), ("detail", "elements"))):
            card = OptionCard(t(f"assess.compact.{d}"), "", icon_key)
            self.dir_group.addButton(card, i)
            self.dir_cards[d] = card
            dir_row.addWidget(card)
        self.compact_note = label(t("assess.compact_max"), "small", align=Qt.AlignmentFlag.AlignLeft)
        self.diff_group.idToggled.connect(lambda *_: self._validate1())
        self.dir_group.idToggled.connect(lambda *_: self._validate1())
        self.btn_next = button(t("assess.next_part") + "  ", "primary", "next", trailing=True)
        self.btn_next.clicked.connect(self._to_part2)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.btn_next)

        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.header1)
        lay.addSpacing(34)
        lay.addWidget(label(t("assess.q_difficulty"), "question", align=Qt.AlignmentFlag.AlignLeft))
        lay.addSpacing(14)
        lay.addLayout(self.diff_row)
        lay.addSpacing(40)
        lay.addWidget(label(t("assess.q_compact"), "question", align=Qt.AlignmentFlag.AlignLeft))
        lay.addSpacing(14)
        lay.addLayout(dir_row)
        lay.addSpacing(8)
        lay.addWidget(self.compact_note)
        lay.addStretch(1)
        lay.addWidget(divider())
        lay.addSpacing(24)
        lay.addLayout(buttons)
        return page

    # --- part 2 ---------------------------------------------------------------------------
    def _build_part2(self) -> QWidget:
        page = _Page()
        self.header2 = ScreenHeader("elements", "blue")
        self.header2.set(t("assess.part2_title"), t("assess.part2_subtitle"))
        self.el_cards: dict[str, OptionCard] = {}
        grid = QGridLayout()
        grid.setSpacing(18)
        for i, el in enumerate(HIDEABLE_ELEMENTS):
            card = OptionCard("", "", ICON.get(el))
            card.toggled.connect(lambda on, e=el: self._on_element(e, on))
            self.el_cards[el] = card
            grid.addWidget(card, i // 2, i % 2)
        self.none_card = OptionCard(t("assess.none.label"), t("assess.none.hint"), "check")
        self.none_card.toggled.connect(self._on_none)
        n = len(HIDEABLE_ELEMENTS)
        grid.addWidget(self.none_card, (n + 1) // 2, 0, 1, 2)
        self.last_note = label(t("assess.last_visible"), "small", align=Qt.AlignmentFlag.AlignLeft)
        self.btn_back = button("  " + t("player.back"), "secondary", "back")
        self.btn_back.clicked.connect(lambda: self.stack.setCurrentWidget(self.part1))
        self.btn_submit = button(t("assess.submit") + "  ", "primary", "check", trailing=True)
        self.btn_submit.clicked.connect(self._submit)
        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_back)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_submit)

        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.header2)
        lay.addSpacing(34)
        lay.addWidget(label(t("assess.q_changes"), "question", align=Qt.AlignmentFlag.AlignLeft))
        lay.addSpacing(14)
        lay.addLayout(grid)
        lay.addSpacing(8)
        lay.addWidget(self.last_note)
        lay.addStretch(1)
        lay.addWidget(divider())
        lay.addSpacing(24)
        lay.addLayout(buttons)
        return page

    # --- public ---------------------------------------------------------------------------
    def start(self, settings: InstructionSettings, difficulty_options: list[str]) -> None:
        self.settings = settings
        self._fill_difficulty(difficulty_options)
        level = settings.merge_level
        _uncheck(self.dir_group)
        self.dir_cards["compress"].setVisible(level < MAX_MERGE_LEVEL)
        self.dir_cards["detail"].setVisible(level > 0)
        self.compact_note.setVisible(level >= MAX_MERGE_LEVEL)
        hidden = set(settings.hidden_elements)
        for el, card in self.el_cards.items():
            mode = "show" if el in hidden else "remove"
            card.set_texts(t(f"assess.{mode}.{el}.label"), t(f"assess.{mode}.{el}.hint"))
            _set_silent(card, False)
        _set_silent(self.none_card, True)
        self._refresh_elements()
        self.btn_submit.setEnabled(True)
        self.btn_next.setEnabled(False)
        self.stack.setCurrentWidget(self.part1)
        self._validate1()

    def request(self) -> Optional[ParticipantRequest]:
        diff_id, dir_id = self.diff_group.checkedId(), self.dir_group.checkedId()
        if diff_id < 0 or dir_id < 0:
            return None
        direction = ("compress", "keep", "detail")[dir_id]
        hidden = set(self.settings.hidden_elements)
        chosen = [el for el, c in self.el_cards.items() if c.isChecked()]
        return ParticipantRequest(difficulty=self.difficulty_options[diff_id], direction=direction,
                                  hide=[e for e in chosen if e not in hidden],
                                  show=[e for e in chosen if e in hidden])

    # --- internals ------------------------------------------------------------------------
    def _fill_difficulty(self, options: list[str]) -> None:
        if options == self.difficulty_options:
            _uncheck(self.diff_group)
            return
        for b in self.diff_group.buttons():
            self.diff_group.removeButton(b)
            b.deleteLater()
        self.difficulty_options = list(options)
        for i, opt in enumerate(options):
            card = OptionCard(_label(f"assess.difficulty.{opt}", opt.capitalize()))
            self.diff_group.addButton(card, i)
            self.diff_row.addWidget(card)

    def _validate1(self) -> None:
        self.btn_next.setEnabled(self.diff_group.checkedId() >= 0 and self.dir_group.checkedId() >= 0)

    def _to_part2(self) -> None:
        if self.btn_next.isEnabled():
            self.stack.setCurrentWidget(self.part2)

    def _hidden_after(self) -> set[str]:
        hidden = set(self.settings.hidden_elements)
        for el, c in self.el_cards.items():
            if c.isChecked():
                hidden ^= {el}   # checked = toggle its current visibility
        return hidden

    def _on_element(self, _el: str, on: bool) -> None:
        if on and self.none_card.isChecked():
            _set_silent(self.none_card, False)
        if not on and not any(c.isChecked() for c in self.el_cards.values()):
            _set_silent(self.none_card, True)
        self._refresh_elements()

    def _on_none(self, on: bool) -> None:
        if on:
            for c in self.el_cards.values():
                _set_silent(c, False)
        elif not any(c.isChecked() for c in self.el_cards.values()):
            self.none_card.blockSignals(True)
            self.none_card.setChecked(True)   # "nothing" stays selected until something else is
            self.none_card.blockSignals(False)
        self._refresh_elements()

    def _refresh_elements(self) -> None:
        """Disable the card that would hide the last visible content element."""
        hidden = self._hidden_after()
        visible_content = [e for e in CONTENT_ELEMENTS if e not in hidden]
        blocked = False
        for el, card in self.el_cards.items():
            would_hide = el not in hidden and el in CONTENT_ELEMENTS and len(visible_content) == 1
            card.setEnabled(card.isChecked() or not would_hide)
            blocked |= would_hide
        self.last_note.setVisible(blocked)

    def _submit(self) -> None:
        if not self.btn_submit.isEnabled():
            return
        req = self.request()
        if req is None:
            self.stack.setCurrentWidget(self.part1)
            return
        self.btn_submit.setEnabled(False)
        self.finished.emit(req)


def _set_silent(card: OptionCard, on: bool) -> None:
    """Check / uncheck without triggering the toggle handlers (the check mark is still redrawn)."""
    card.blockSignals(True)
    card.setChecked(on)
    card.blockSignals(False)
    card._refresh_mark()


def _uncheck(group: QButtonGroup) -> None:
    group.setExclusive(False)
    for b in group.buttons():
        b.setChecked(False)
    group.setExclusive(True)
