"""Workspace -> Recordings: every stored session (newest first), filterable, with details.

Selecting a row shows the session's details and its last board photo; "Open in Visualizations"
loads it (read-only) into the Visualizations page. The running session is marked "live".
The index is cached by file modification time, so refreshing is cheap.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLineEdit, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.i18n import items, t
from app.pipeline.archive import SessionInfo, list_sessions
from app.ui.workspace_widgets import ImageView, btn, card, export_dir, load_pixmap, plain, section_label

THUMB = QSize(72, 40)


def ago(when: Optional[datetime], now: Optional[datetime] = None) -> str:
    """Relative time like the reference UI: 'just now', '5 minutes ago', '3 days ago', ..."""
    if when is None:
        return "–"
    now = now or datetime.now(when.tzinfo)
    secs = max(0, int((now - when).total_seconds()))
    for unit, size in (("year", 365 * 86400), ("month", 30 * 86400), ("day", 86400), ("hour", 3600),
                       ("minute", 60)):
        if secs >= size:
            n = secs // size
            return t("workspace.ago", n=n, unit=t(f"workspace.unit_{unit}{'s' if n != 1 else ''}"))
    return t("workspace.just_now")


def hms(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


class RecordingsPage(QWidget):
    open_requested = Signal(object)      # session folder (Path)

    def __init__(self, sessions_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.root = Path(sessions_root)
        self.sessions: list[SessionInfo] = []
        self.live_folder: Optional[Path] = None

        self.filter = QLineEdit()
        self.filter.setPlaceholderText(t("archive.filter"))
        self.filter.setMaxLength(64)
        self.filter.textChanged.connect(self._apply_filter)
        self.btn_refresh = btn(t("archive.refresh"), "refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        cols = items("workspace.recording_columns") or ["Recording", "Duration", "Recorded", "Condition", "Runs",
                                                        "Status"]
        self.table = QTableWidget(0, len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(THUMB.height() + 12)
        self.table.setIconSize(THUMB)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._show_details)
        self.table.doubleClicked.connect(lambda *_: self._open())

        top = QHBoxLayout()
        top.addWidget(self.filter, 1)
        top.addWidget(self.btn_refresh)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addLayout(top)
        ll.addWidget(self.table, 1)

        # details
        self.preview = ImageView(t("workspace.last_photo"))
        self.preview.setMinimumHeight(200)
        self.details = plain("", "")
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.btn_open = btn(t("workspace.open_visualizations"), "chart", "start")
        self.btn_open.setObjectName("start")
        self.btn_folder = btn(t("workspace.open_folder"), "sessions")
        self.btn_export = btn(t("cockpit.export"), "export")
        self.btn_open.clicked.connect(self._open)
        self.btn_folder.clicked.connect(self._open_folder)
        self.btn_export.clicked.connect(lambda: export_dir(self._selected().folder if self._selected() else None,
                                                           self))
        right = card()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(14, 10, 14, 12)
        rl.addWidget(section_label(t("workspace.details")))
        rl.addWidget(self.preview, 2)
        rl.addWidget(self.details, 1)
        rl.addWidget(self.btn_open)
        row = QHBoxLayout()
        row.addWidget(self.btn_folder)
        row.addWidget(self.btn_export)
        rl.addLayout(row)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([820, 420])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        self.refresh()

    def set_live(self, folder: Optional[Path]) -> None:
        self.live_folder = Path(folder).resolve() if folder else None
        self.refresh()

    def refresh(self) -> None:
        keep = self._selected().folder if self._selected() else None
        self.sessions = list_sessions(self.root)
        self.table.setRowCount(len(self.sessions))
        status = {"ok": t("archive.status_ok"), "incomplete": t("archive.status_incomplete"),
                  "broken": t("archive.status_broken")}
        for r, s in enumerate(self.sessions):
            live = self.live_folder is not None and s.folder.resolve() == self.live_folder
            name = QTableWidgetItem(f"{s.participant_id}\n{s.folder.name}")
            pm = load_pixmap(s.last_photo)
            if pm is not None:
                name.setIcon(QIcon(pm.scaled(THUMB, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                             Qt.TransformationMode.SmoothTransformation)))
            vals = [hms(s.duration_s), ago(s.started), s.condition, s.runs,
                    t("workspace.status_live") if live else status.get(s.status, s.status)]
            self.table.setItem(r, 0, name)
            for c, v in enumerate(vals, start=1):
                self.table.setItem(r, c, QTableWidgetItem(str(v)))
        self._apply_filter(self.filter.text())
        if keep is not None:
            for r, s in enumerate(self.sessions):
                if s.folder == keep:
                    self.table.selectRow(r)
        self._show_details()

    def _apply_filter(self, text: str) -> None:
        q = text.strip().lower()
        for r, s in enumerate(self.sessions):
            hay = f"{s.participant_id} {s.condition} {s.folder.name} {s.llm_client}".lower()
            self.table.setRowHidden(r, bool(q) and q not in hay)

    def _selected(self) -> Optional[SessionInfo]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        r = rows[0].row()
        return self.sessions[r] if 0 <= r < len(self.sessions) else None

    def _show_details(self) -> None:
        s = self._selected()
        for b in (self.btn_open, self.btn_folder, self.btn_export):
            b.setEnabled(s is not None and s.status != "broken")
        if s is None:
            self.preview.set_image(None, t("workspace.select_recording"))
            self.details.setText("")
            return
        self.preview.set_image(s.last_photo, t("workspace.no_photos"))
        started = f"{s.started:%Y-%m-%d %H:%M}" if s.started else "–"
        self.details.setText(t("workspace.recording_details", pid=s.participant_id, started=started,
                               condition=s.condition, llm=s.llm_client or "–", runs=s.runs,
                               duration=hms(s.duration_s), folder=s.folder.name))

    def _open(self) -> None:
        s = self._selected()
        if s is not None and s.status != "broken":
            self.open_requested.emit(s.folder)

    def _open_folder(self) -> None:
        s = self._selected()
        if s is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(s.folder)))
