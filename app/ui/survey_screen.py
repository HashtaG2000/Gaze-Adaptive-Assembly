"""Post-session questionnaire (participant-facing), e.g. SUS from data/surveys/<id>.json.

Items are shown items_per_page at a time; "Next" is enabled when the page is complete.
Emits finished({item_id: value}).
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from app.i18n import t
from app.models import Survey
from app.ui.style import OptionCard, ScaledScreen, ScreenHeader, button, divider, label, page_layout


class _Item(QWidget):
    def __init__(self, number: int, text: str, values: list[int], labels: dict[str, str], on_change) -> None:
        super().__init__()
        self.setObjectName("qcard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.values = values
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 12, 20, 14)
        lay.addWidget(label(f"{number}. {text}", "field", align=Qt.AlignmentFlag.AlignLeft))
        row = QHBoxLayout()
        row.setSpacing(10)
        self.group = QButtonGroup(self)
        for i, v in enumerate(values):
            card = OptionCard(str(v), labels.get(str(v), ""))
            self.group.addButton(card, i)
            row.addWidget(card)
        self.group.idToggled.connect(lambda *_: on_change())
        lay.addLayout(row)

    def value(self) -> Optional[int]:
        i = self.group.checkedId()
        return self.values[i] if i >= 0 else None


class SurveyScreen(ScaledScreen):
    finished = Signal(object)   # dict item_id -> int

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.survey: Optional[Survey] = None
        self.answers: dict[str, int] = {}
        self.page = 0
        self.header = ScreenHeader("feedback", "blue")
        self.intro = label("", "muted", align=Qt.AlignmentFlag.AlignLeft)
        # questions scroll inside the page; header and Back / Next always stay on screen
        self.items_host = QWidget()
        self.items_host.setObjectName("screen")
        self.items_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.items_box = QVBoxLayout(self.items_host)
        self.items_box.setContentsMargins(0, 0, 12, 0)
        self.items_box.setSpacing(12)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.items_host)
        self.items: list[tuple[str, _Item]] = []
        self.btn_back = button("  " + t("survey.back"), "secondary", "back")
        self.btn_next = button(t("survey.next") + "  ", "primary", "next", trailing=True)
        self.btn_back.clicked.connect(lambda: self._go(-1))
        self.btn_next.clicked.connect(lambda: self._go(+1))
        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_back)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_next)
        lay = page_layout(self)
        lay.addWidget(self.header)
        lay.addSpacing(16)
        lay.addWidget(self.intro)
        lay.addSpacing(16)
        lay.addWidget(self.scroll, 1)
        lay.addSpacing(12)
        lay.addWidget(divider())
        lay.addSpacing(20)
        lay.addLayout(buttons)

    @property
    def pages(self) -> int:
        if self.survey is None:
            return 0
        per = self.survey.items_per_page
        return (len(self.survey.items) + per - 1) // per

    def start(self, survey: Survey) -> None:
        self.survey = survey
        self.answers = {}
        self.page = 0
        self.header.set(survey.title)
        self.intro.setText(survey.intro)
        self.intro.setVisible(bool(survey.intro))
        self._show_page()

    def _show_page(self) -> None:
        assert self.survey is not None
        while self.items_box.count():
            w = self.items_box.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        per = self.survey.items_per_page
        sc = self.survey.scale
        values = list(range(sc.min, sc.max + 1))
        self.items = []
        start = self.page * per
        for n, item in enumerate(self.survey.items[start:start + per], start=start + 1):
            w = _Item(n, item.text, values, sc.labels, self._validate)
            if item.id in self.answers and self.answers[item.id] in values:
                w.group.button(values.index(self.answers[item.id])).setChecked(True)
            self.items.append((item.id, w))
            self.items_box.addWidget(w)
        self.items_box.addStretch(1)
        self.scroll.verticalScrollBar().setValue(0)      # every page starts at its first question
        self.header.set_chip(t("survey.page", page=self.page + 1, pages=self.pages), "neutral")
        last = self.page == self.pages - 1
        self.btn_next.setText((t("survey.submit") if last else t("survey.next")) + "  ")
        self.btn_back.setVisible(self.page > 0)
        self._validate()

    def _validate(self) -> None:
        self.btn_next.setEnabled(all(w.value() is not None for _, w in self.items))

    def _go(self, delta: int) -> None:
        if self.survey is None:
            return
        for item_id, w in self.items:
            v = w.value()
            if v is not None:
                self.answers[item_id] = v
        if delta > 0 and not self.btn_next.isEnabled():
            return
        if delta > 0 and self.page == self.pages - 1:
            self.btn_next.setEnabled(False)
            self.finished.emit(dict(self.answers))
            return
        self.page = max(0, min(self.pages - 1, self.page + delta))
        self._show_page()


class CompleteScreen(ScaledScreen):
    """Final page: thank you."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from app.ui.style import icon_tile
        lay = QVBoxLayout(self)
        lay.setContentsMargins(120, 60, 120, 60)
        lay.addStretch(3)
        lay.addWidget(icon_tile("trophy", "emerald_round"), alignment=Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(40)
        lay.addWidget(label(t("complete.title"), "title"))
        lay.addSpacing(20)
        lay.addWidget(label(t("complete.text"), "muted"))
        lay.addStretch(4)
