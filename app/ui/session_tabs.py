"""Session data tabs shared by the live cockpit and the read-only archive window:

    Runs                 per run: step time vs. MTM reference, AOI dwell % per step, metrics table
    Analytics            total time vs. MTM across all runs, intake answers, post-survey, request stats
    LLM calls            every call: full prompt, raw output, validation result, latency
    Decisions & versions participant decisions and every instruction set version used

All text from instruction files, the LLM or participants is shown as plain text.
"""
from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from app.i18n import items, t
from app.models import DecisionRecord, InstructionSet, LLMCallRecord, RunSummary, VerificationRecord
from app.pipeline.summary import SummaryHistory
from app.ui.charts import AOI_GROUPS, Chart, bars, colour, markers
from app.ui.icons import icon
from app.ui.style import C
from app.ui.workspace_widgets import ImagePair, btn


def verdict_colour(rec: VerificationRecord) -> Optional[str]:
    final = rec.reviewed or (None if rec.needs_review else rec.verdict)
    if final == "correct":
        return C["emerald_400"]
    if final == "error":
        return C["rose_400"]
    return C["amber_400"]                       # waiting for the observer


def describe_check(rec: VerificationRecord) -> str:
    parts = [t("workspace.check_line", run=rec.run, step=rec.step_index + 1, client=rec.client,
               latency=f"{rec.latency_s:.1f}" if rec.latency_s is not None else "–")]
    if rec.verdict:
        parts.append(t("workspace.check_verdict", verdict=rec.verdict,
                       confidence=f"{rec.confidence:.0%}" if rec.confidence is not None else "–"))
    if rec.reason:
        parts.append(rec.reason)
    if rec.error:
        parts.append(t("workspace.check_error", error=rec.error))
    if rec.reviewed:
        parts.append(t("workspace.check_reviewed", verdict=rec.reviewed))
    elif rec.needs_review:
        parts.append(t("workspace.check_needs_review"))
    return "\n".join(parts)


def check_statistics(recs: list[VerificationRecord]) -> str:
    """Automatic checks vs. the observer: how often did the model agree after review?"""
    if not recs:
        return t("workspace.no_checks")
    auto = sum(1 for r in recs if r.verdict in ("correct", "error") and not r.needs_review)
    review = sum(1 for r in recs if r.needs_review and not r.reviewed)
    reviewed = [r for r in recs if r.reviewed and r.verdict in ("correct", "error")]
    agree = sum(1 for r in reviewed if r.verdict == r.reviewed)
    confs = [r.confidence for r in recs if r.confidence is not None]
    text = t("workspace.check_stats", total=len(recs), auto=auto, review=review,
             conf=f"{sum(confs) / len(confs):.0%}" if confs else "–")
    if reviewed:
        text += "  ·  " + t("workspace.check_agreement", agree=agree, n=len(reviewed),
                             pct=f"{agree / len(reviewed):.0%}")
    return text


@dataclass
class SessionView:
    """What the tabs display; filled from a live controller or an archived session."""
    participant_id: str = ""
    condition: str = ""
    dir: Optional[Path] = None
    history: SummaryHistory = field(default_factory=SummaryHistory)
    llm_calls: list[LLMCallRecord] = field(default_factory=list)
    decisions: list[DecisionRecord] = field(default_factory=list)
    versions: list[InstructionSet] = field(default_factory=list)
    questionnaire: dict[str, Any] = field(default_factory=dict)
    survey_score: Optional[float] = None
    survey_done: bool = False
    verifications: dict[int, VerificationRecord] = field(default_factory=dict)   # camera checks by id
    live: bool = False

    @classmethod
    def from_controller(cls, c) -> "SessionView":
        return cls(participant_id=c.choice.participant_id if c.choice else "", condition=c.condition,
                   dir=c.logger.dir if c.logger else None, history=c.history, llm_calls=c.llm_calls,
                   decisions=c.decisions, versions=c.versions,
                   questionnaire=dict(c.choice.questionnaire) if c.choice else {},
                   survey_score=c.survey.score if c.survey else None, survey_done=c.survey is not None,
                   verifications=c.verifications, live=True)

    @classmethod
    def from_archive(cls, a) -> "SessionView":
        return cls(participant_id=a.participant_id, condition=str(a.meta.get("condition", "?")), dir=a.dir,
                   history=a.history, llm_calls=a.llm_calls, decisions=a.decisions, versions=a.versions,
                   questionnaire=dict(a.meta.get("questionnaire") or {}),
                   survey_score=a.survey.score if a.survey else None, survey_done=a.survey is not None,
                   verifications={v.id: v for v in a.verifications})


def _ro_text(name: str = "") -> QPlainTextEdit:
    w = QPlainTextEdit()
    w.setReadOnly(True)
    if name:
        w.setObjectName(name)
    return w


def _table(columns: list[str]) -> QTableWidget:
    tb = QTableWidget(0, len(columns))
    tb.setHorizontalHeaderLabels(columns)
    tb.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    tb.setAlternatingRowColors(True)
    tb.verticalHeader().setVisible(False)
    tb.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    tb.horizontalHeader().setStretchLastSection(True)
    return tb


def _section(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("section")
    return lab


class SessionTabs(QTabWidget):
    review_requested = Signal(int, str)    # camera check id, "correct" | "error" (live session only)

    def __init__(self, media_root: Optional[Path] = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view = SessionView()
        self.media_root = Path(media_root) if media_root else None
        self.addTab(self._build_runs(), t("cockpit.tabs.runs"))
        self.addTab(self._build_analytics(), t("cockpit.tabs.analytics"))
        self.addTab(self._build_checks(), t("workspace.tab_checks"))
        self.addTab(self._build_llm(), t("cockpit.tabs.llm"))
        self.addTab(self._build_decisions(), t("cockpit.tabs.decisions").replace("&", "&&"))

    # --- loading ----------------------------------------------------------------------------
    def load(self, view: SessionView) -> None:
        self.view = view
        self.run_combo.blockSignals(True)
        self.run_combo.clear()
        self.run_combo.blockSignals(False)
        self.llm_list.clear()
        self.ver_list.clear()
        self.dec_table.setRowCount(0)
        self.step_table.setRowCount(0)
        self.time_chart.clear()
        self.dwell_chart.clear()
        for w in (self.llm_prompt, self.llm_raw, self.ver_json):
            w.clear()
        self.llm_meta.clear()
        for s in view.history.all():
            self.add_summary(s, select=False)
        if self.run_combo.count():
            self.run_combo.setCurrentIndex(self.run_combo.count() - 1)
        for r in view.llm_calls:
            self.add_llm_call(r)
        for d in view.decisions:
            self.add_decision(d)
        for v in view.versions:
            self.add_version(v)
        self.refresh_checks()
        self.refresh_analytics()

    def set_survey(self, score: Optional[float], done: bool) -> None:
        self.view.survey_score, self.view.survey_done = score, done
        self.refresh_analytics()

    # --- runs -------------------------------------------------------------------------------
    def _build_runs(self) -> QWidget:
        self.run_combo = QComboBox()
        self.run_combo.setMinimumWidth(240)
        self.run_combo.currentIndexChanged.connect(self._show_run)
        self.run_info = QLabel()
        self.run_info.setObjectName("muted")
        self.run_info.setTextFormat(Qt.TextFormat.PlainText)
        top = QHBoxLayout()
        top.addWidget(QLabel(t("cockpit.run") + ":"))
        top.addWidget(self.run_combo)
        top.addSpacing(12)
        top.addWidget(self.run_info, 1)
        self.time_chart = Chart(t("cockpit.step_time"), t("cockpit.seconds"), t("cockpit.step"))
        self.dwell_chart = Chart(t("cockpit.aoi_dwell"), t("cockpit.percent"), t("cockpit.step"))
        charts = QHBoxLayout()
        charts.addWidget(self.time_chart.widget)
        charts.addWidget(self.dwell_chart.widget)
        self.step_table = _table([])
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addLayout(top)
        lay.addLayout(charts, 3)
        lay.addWidget(self.step_table, 2)
        return w

    def add_summary(self, s: RunSummary, select: bool = True) -> None:
        tag = f"{s.run}: {s.label}  (v{s.version_level})"
        self.run_combo.addItem(tag, s.run)
        if select:
            self.run_combo.setCurrentIndex(self.run_combo.count() - 1)
            self.refresh_analytics()

    def _version(self, level: int) -> Optional[InstructionSet]:
        return next((v for v in self.view.versions if v.version_level == level), None)

    def _show_run(self) -> None:
        run = self.run_combo.currentData()
        s = self.view.history.get(run) if run is not None else None
        tc, dc = self.time_chart, self.dwell_chart
        tc.clear()
        dc.clear()
        if s is None or not s.steps:
            self.step_table.setRowCount(0)
            self.run_info.setText(t("cockpit.no_runs") if s is None else "")
            return
        iset = self._version(s.version_level)
        expected: list[Optional[float]] = [None] * len(s.steps)
        if iset is not None:
            for i, m in enumerate(s.steps):
                if 0 <= m.step_index < iset.n_steps:
                    expected[i] = iset.steps[m.step_index].expected_time_s
        x = [m.step_index + 1 for m in s.steps]
        dur = [m.duration_s for m in s.steps]
        self.run_info.setText(t("cockpit.run_info", total=f"{s.totals.total_duration_s:.1f}",
                                ref=f"{s.totals.total_expected_s:.1f}", back=s.totals.total_back_presses,
                                err=s.totals.total_observer_errors, level=s.settings.merge_level,
                                hidden=", ".join(s.settings.hidden_elements) or "–"))
        tc.add(bars(x, dur, colour(0)), t("cockpit.duration"))
        ref_x = [xi for xi, e in zip(x, expected) if e]
        if ref_x:
            tc.add(markers(ref_x, [e for e in expected if e]), t("cockpit.reference"))
        tc.set_ticks([(i, str(i)) for i in x])
        tc.plot.setYRange(0, max(dur + [e for e in expected if e] + [1.0]) * 1.15)
        tc.hover.texts = [
            (f"Step {m.step_index + 1}: {m.duration_s:.2f} s\nreference {e:.2f} s (ratio {m.duration_vs_expected_ratio:.2f})"
             if e else f"Step {m.step_index + 1}: {m.duration_s:.2f} s") + f"\nback presses {m.back_presses}"
            for m, e in zip(s.steps, expected)]

        bottoms = [0.0] * len(s.steps)
        for name, members, col in AOI_GROUPS:
            vals = [sum(m.dwell_pct.get(a, 0.0) for a in members) for m in s.steps]
            dc.add(bars(x, vals, col, y0=bottoms), name)
            bottoms = [b + v for b, v in zip(bottoms, vals)]
        dc.set_ticks([(i, str(i)) for i in x])
        dc.plot.setYRange(0, 100)
        dc.hover.texts = [
            f"Step {m.step_index + 1}\n" + "\n".join(
                f"{name}: {sum(m.dwell_pct.get(a, 0.0) for a in members):.0f} %" for name, members, _ in AOI_GROUPS)
            + f"\nvalid samples: {m.n_valid_samples}" for m in s.steps]

        cols = (items("cockpit.step_columns") or ["Step", "Duration s", "Reference s", "Ratio", "Back",
                                                  "Observer errors", "Valid samples", "First AOI", "Revisits"]) \
            + [g[0] + " %" for g in AOI_GROUPS] + ["transitions"]
        self.step_table.setColumnCount(len(cols))
        self.step_table.setHorizontalHeaderLabels(cols)
        self.step_table.setRowCount(len(s.steps))
        for r, (m, e) in enumerate(zip(s.steps, expected)):
            vals = [m.step_index + 1, f"{m.duration_s:.2f}", f"{e:.2f}" if e else "",
                    f"{m.duration_vs_expected_ratio:.2f}", m.back_presses, m.observer_errors, m.n_valid_samples,
                    m.first_aoi or "", m.revisit_count] \
                + [f"{sum(m.dwell_pct.get(a, 0.0) for a in members):.1f}" for _, members, _ in AOI_GROUPS] \
                + [" > ".join(m.transitions)]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c < len(vals) - 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.step_table.setItem(r, c, item)

    # --- analytics ----------------------------------------------------------------------------
    def _build_analytics(self) -> QWidget:
        self.total_chart = Chart(t("cockpit.analytics_time"), t("cockpit.seconds"), t("cockpit.run"))
        self.error_chart = Chart(t("cockpit.analytics_errors"), t("cockpit.count"), t("cockpit.run"))
        self.analytics_text = QPlainTextEdit()
        self.analytics_text.setReadOnly(True)
        self.analytics_text.setMaximumWidth(460)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(_section(t("cockpit.analytics_survey")))
        rl.addWidget(self.analytics_text, 1)
        w = QWidget()
        charts = QVBoxLayout()
        charts.addWidget(self.total_chart.widget, 1)
        charts.addWidget(self.error_chart.widget, 1)
        lay = QHBoxLayout(w)
        lay.addLayout(charts, 3)
        lay.addWidget(right, 2)
        return w

    def refresh_analytics(self) -> None:
        runs = self.view.history.all()
        ch = self.total_chart
        ch.clear()
        if runs:
            x = list(range(1, len(runs) + 1))
            reg = [(xi, s) for xi, s in zip(x, runs) if not s.is_trial]
            tri = [(xi, s) for xi, s in zip(x, runs) if s.is_trial]
            if reg:
                ch.add(bars([xi for xi, _ in reg], [s.totals.total_duration_s for _, s in reg], colour(0)),
                       t("cockpit.regular"))
            if tri:
                ch.add(bars([xi for xi, _ in tri], [s.totals.total_duration_s for _, s in tri], colour(2)),
                       t("cockpit.trial"))
            ch.add(markers(x, [s.totals.total_expected_s for s in runs]), t("cockpit.reference"))
            ch.set_ticks([(xi, t("cockpit.trial") if s.is_trial else f"#{s.repetition}") for xi, s in zip(x, runs)])
            top = max([s.totals.total_duration_s for s in runs] + [s.totals.total_expected_s for s in runs] + [1.0])
            ch.plot.setYRange(0, top * 1.15)
            ch.hover.texts = [f"{s.label} (v{s.version_level})\n{s.totals.total_duration_s:.1f} s, reference "
                              f"{s.totals.total_expected_s:.1f} s\nback presses {s.totals.total_back_presses}, "
                              f"observer errors {s.totals.total_observer_errors}" for s in runs]
        self._refresh_errors(runs)
        v = self.view
        lines = [f"{v.participant_id}  ·  {v.condition}", ""]
        lines.append(t("cockpit.intake_answers") + ":")
        lines += [f"  {k}: {'–' if val is None else val}" for k, val in v.questionnaire.items()] or ["  –"]
        lines.append("")
        lines.append(t("cockpit.sus_score", score=f"{v.survey_score:.1f}") if v.survey_score is not None
                     else t("cockpit.no_survey") if not v.survey_done else t("cockpit.sus_score", score="–"))
        dec = [d.decision for d in v.decisions]
        lines.append(t("cockpit.analytics_requests", n=dec.count("request"), kept=dec.count("keep"),
                       reverted=dec.count("revert")))
        lines.append("")
        for d in v.decisions:
            if d.decision == "request" and d.detail:
                lines.append(f"  run {d.run}: difficulty {d.detail.get('difficulty') or '–'}, "
                             f"{d.detail.get('direction')}, hide {', '.join(d.detail.get('hide') or []) or '–'}, "
                             f"show {', '.join(d.detail.get('show') or []) or '–'}")
        lines += ["", t("cockpit.error_steps") + ":"]
        lines += self._error_step_lines(runs) or ["  –"]
        self.analytics_text.setPlainText("\n".join(str(x) for x in lines))

    def _refresh_errors(self, runs: list[RunSummary]) -> None:
        """Observer errors and Back presses per run, as grouped bars."""
        ch = self.error_chart
        ch.clear()
        if not runs:
            return
        x = list(range(1, len(runs) + 1))
        errors = [s.totals.total_observer_errors for s in runs]
        backs = [s.totals.total_back_presses for s in runs]
        ch.add(bars([xi - 0.19 for xi in x], errors, colour(4), width=0.36), t("cockpit.observer_errors"))
        ch.add(bars([xi + 0.19 for xi in x], backs, colour(3), width=0.36), t("cockpit.back_presses"))
        ch.set_ticks([(xi, t("cockpit.trial") if s.is_trial else f"#{s.repetition}") for xi, s in zip(x, runs)])
        ch.plot.setYRange(0, max(errors + backs + [1]) * 1.25)
        ch.hover.texts = [f"{s.label}\nobserver errors {e}\nback presses {b}\n"
                          f"error rate {e / max(1, len(s.steps)):.0%} of steps"
                          for s, e, b in zip(runs, errors, backs)]

    @staticmethod
    def _error_step_lines(runs: list[RunSummary]) -> list[str]:
        lines = []
        for s in runs:
            bad = [f"step {m.step_index + 1}" + (f" ({m.observer_errors}× error)" if m.observer_errors else "")
                   + (f" ({m.back_presses}× back)" if m.back_presses else "")
                   for m in s.steps if m.observer_errors or m.back_presses]
            if bad:
                lines.append(f"  {s.label}: " + ", ".join(bad))
        return lines

    def update_summary(self, s: RunSummary) -> None:
        """A run's metrics changed after the run (late camera verdict)."""
        if self.run_combo.currentData() == s.run:
            self._show_run()
        self.refresh_analytics()

    # --- camera checks ------------------------------------------------------------------------
    def _build_checks(self) -> QWidget:
        cols = items("workspace.check_columns") or ["#", "Run", "Step", "Check", "Confidence", "Final", "Status"]
        self.check_table = _table(cols)
        self.check_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.check_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.check_table.itemSelectionChanged.connect(self._show_check)
        self.check_stats = QLabel()
        self.check_stats.setObjectName("muted")
        self.check_stats.setTextFormat(Qt.TextFormat.PlainText)
        self.check_stats.setWordWrap(True)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.check_stats)
        ll.addWidget(self.check_table, 1)
        self.check_pair = ImagePair()
        self.check_detail = QLabel()
        self.check_detail.setTextFormat(Qt.TextFormat.PlainText)
        self.check_detail.setWordWrap(True)
        self.check_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.btn_check_ok = btn(t("cockpit.correct"), "check", "ok")
        self.btn_check_err = btn(t("cockpit.error"), "close", "danger")
        self.btn_check_ok.clicked.connect(lambda: self._review("correct"))
        self.btn_check_err.clicked.connect(lambda: self._review("error"))
        brow = QHBoxLayout()
        brow.addWidget(QLabel(t("workspace.observer_decides")))
        brow.addStretch(1)
        brow.addWidget(self.btn_check_ok)
        brow.addWidget(self.btn_check_err)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.check_pair, 1)
        rl.addWidget(self.check_detail)
        rl.addLayout(brow)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([560, 700])
        return split

    def _check_ids(self) -> list[int]:
        return sorted(self.view.verifications)

    def refresh_checks(self) -> None:
        recs = [self.view.verifications[i] for i in self._check_ids()]
        selected = self._selected_check()
        self.check_table.setRowCount(len(recs))
        for r, rec in enumerate(recs):
            final = rec.reviewed or (rec.verdict if not rec.needs_review else None)
            status = (t("workspace.status_reviewed") if rec.reviewed else t("workspace.status_review")
                      if rec.needs_review else t("workspace.status_auto"))
            vals = [rec.id, rec.run, rec.step_index + 1, rec.verdict or ("error: " + (rec.error or "")[:40] if rec.error else "–"),
                    f"{rec.confidence:.0%}" if rec.confidence is not None else "–", final or "–", status]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                item.setData(Qt.ItemDataRole.UserRole, rec.id)
                if c == 6 and rec.needs_review and not rec.reviewed:
                    item.setForeground(QColor(C["amber_300"]))
                self.check_table.setItem(r, c, item)
        self.check_stats.setText(check_statistics(recs))
        if selected is not None and selected in self.view.verifications:
            self._select_check(selected)
        elif recs and selected is None:
            self._select_check(recs[-1].id)
        self._show_check()

    def add_verification(self, rec: VerificationRecord) -> None:
        self.view.verifications[rec.id] = rec
        self.refresh_checks()

    def _selected_check(self) -> Optional[int]:
        rows = self.check_table.selectionModel().selectedRows() if self.check_table.selectionModel() else []
        if not rows:
            return None
        item = self.check_table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _select_check(self, check_id: int) -> None:
        for r in range(self.check_table.rowCount()):
            item = self.check_table.item(r, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == check_id:
                self.check_table.selectRow(r)
                return

    def _show_check(self) -> None:
        cid = self._selected_check()
        rec = self.view.verifications.get(cid) if cid is not None else None
        self.btn_check_ok.setEnabled(rec is not None and self.view.live)
        self.btn_check_err.setEnabled(rec is not None and self.view.live)
        if rec is None:
            self.check_pair.show_pair(None, None)
            self.check_detail.setText("")
            return
        photo = self.view.dir / rec.photo if (self.view.dir and rec.photo) else None
        ref = self.media_root / rec.reference if (self.media_root and rec.reference) else None
        self.check_pair.show_pair(ref, photo, verdict_colour(rec))
        self.check_detail.setText(describe_check(rec))

    def _review(self, verdict: str) -> None:
        cid = self._selected_check()
        if cid is not None and self.view.live:
            self.review_requested.emit(cid, verdict)

    # --- LLM ----------------------------------------------------------------------------------
    def _build_llm(self) -> QWidget:
        self.llm_list = QListWidget()
        self.llm_list.currentRowChanged.connect(self._show_llm)
        self.llm_meta = QLabel()
        self.llm_meta.setWordWrap(True)
        self.llm_meta.setTextFormat(Qt.TextFormat.RichText)   # built from escaped text only
        self.llm_meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.llm_prompt = _ro_text()
        self.llm_raw = _ro_text("raw")
        detail = QTabWidget()
        detail.addTab(self.llm_prompt, t("cockpit.prompt"))
        detail.addTab(self.llm_raw, t("cockpit.raw"))
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(self.llm_meta)
        rl.addWidget(detail, 1)
        split = QSplitter()
        split.addWidget(self.llm_list)
        split.addWidget(right)
        split.setSizes([320, 1000])
        return split

    def add_llm_call(self, r: LLMCallRecord) -> None:
        ok = "valid" if r.validation.ok else "INVALID"
        item = QListWidgetItem(f"run {r.run} · {r.call} #{r.attempt} · {r.client} · {ok}")
        item.setIcon(icon("valid" if r.validation.ok else "invalid",
                          C["emerald_400"] if r.validation.ok else C["rose_400"]))
        if not r.validation.ok:
            item.setForeground(QColor(C["rose_300"]))
        self.llm_list.addItem(item)
        self.llm_list.setCurrentRow(self.llm_list.count() - 1)

    def _show_llm(self, row: int) -> None:
        calls = self.view.llm_calls
        if not 0 <= row < len(calls):
            return
        r = calls[row]
        v = r.validation
        e = html.escape
        ok_col = C["emerald_400"] if v.ok else C["rose_400"]
        parts = [f"<b>{e(r.call)}</b> (attempt {r.attempt}) · client <b>{e(r.client)}</b> · run {r.run} · "
                 f"latency {r.latency_s} s · {r.wall_time:%H:%M:%S}",
                 f"Validation: <b style='color:{ok_col}'>{'OK' if v.ok else 'INVALID'}</b>"]
        if v.errors:
            parts.append("Errors:<br>" + "<br>".join(f"• {e(x)}" for x in v.errors[:50]))
        if v.warnings:
            parts.append("Warnings:<br>" + "<br>".join(f"• {e(x)}" for x in v.warnings[:50]))
        if r.error:
            parts.append(f"Call error: {e(r.error[:2000])}")
        self.llm_meta.setText("<br>".join(parts))
        self.llm_prompt.setPlainText(r.prompt)
        raw = r.raw_response or ""
        try:
            raw = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
        except (ValueError, TypeError):
            pass
        self.llm_raw.setPlainText(raw or "(no response)")

    # --- decisions & versions -----------------------------------------------------------------
    def _build_decisions(self) -> QWidget:
        cols = items("cockpit.decision_columns") or ["Time", "Run", "Decision", "From", "To", "Request"]
        self.dec_table = _table(cols)
        self.ver_list = QListWidget()
        self.ver_list.currentRowChanged.connect(self._show_version)
        self.ver_json = _ro_text()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(_section(t("cockpit.decisions").upper()))
        ll.addWidget(self.dec_table)
        mid = QWidget()
        ml = QVBoxLayout(mid)
        ml.addWidget(_section(t("cockpit.versions").upper()))
        ml.addWidget(self.ver_list)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(mid)
        split.addWidget(self.ver_json)
        split.setSizes([480, 300, 600])
        return split

    def add_decision(self, d: DecisionRecord) -> None:
        r = self.dec_table.rowCount()
        self.dec_table.insertRow(r)
        req = ""
        if d.detail:
            req = (f"difficulty {d.detail.get('difficulty') or '–'}; {d.detail.get('direction')}; "
                   f"hide {d.detail.get('hide') or '–'}; show {d.detail.get('show') or '–'}")
        for c, v in enumerate([f"{d.wall_time:%H:%M:%S}", d.run, d.decision, d.from_version,
                               "" if d.to_version is None else d.to_version, req]):
            self.dec_table.setItem(r, c, QTableWidgetItem(str(v)))
        self.refresh_analytics()

    def add_version(self, v: InstructionSet) -> None:
        s = v.settings
        self.ver_list.addItem(f"v{v.version_level}: merge {s.merge_level}, hidden [{', '.join(s.hidden_elements)}], "
                              f"{v.n_steps} steps")
        self.ver_list.setCurrentRow(self.ver_list.count() - 1)

    def _show_version(self, row: int) -> None:
        if 0 <= row < len(self.view.versions):
            self.ver_json.setPlainText(self.view.versions[row].model_dump_json(indent=2))
