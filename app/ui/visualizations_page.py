"""Workspace -> Visualizations: all analysis for one session - the live one or any recording.

Tabs (app/ui/session_tabs.py): Runs (step time vs. MTM, AOI dwell, metrics), Analytics (time across
runs, errors per run, intake + survey), Camera checks (photo vs. reference, model vs. observer
agreement), LLM calls, Decisions & versions. Loaded recordings are cached (LRU), so switching
between sessions is instant.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget

from app.core.files import LRUCache
from app.i18n import t
from app.pipeline.archive import ArchivedSession, load_session
from app.ui.session_tabs import SessionTabs, SessionView
from app.ui.workspace_widgets import plain

LIVE = "__live__"


class VisualizationsPage(QWidget):
    def __init__(self, media_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctl = None
        self._cache: LRUCache[ArchivedSession] = LRUCache(8)
        self.source = QComboBox()
        self.source.setMinimumWidth(380)
        self.source.currentIndexChanged.connect(self._source_changed)
        self.banner = plain("", "banner", wrap=False)
        self.banner.hide()
        self.tabs = SessionTabs(media_root)
        top = QHBoxLayout()
        top.addWidget(QLabel(t("workspace.source")))
        top.addWidget(self.source)
        top.addSpacing(12)
        top.addWidget(self.banner)
        top.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(top)
        lay.addWidget(self.tabs, 1)
        self.current: Optional[str] = None

    # --- sources -----------------------------------------------------------------------------
    def set_live(self, ctl) -> None:
        """Show the running session (or remove the live entry with ctl=None)."""
        self.ctl = ctl
        idx = self.source.findData(LIVE)
        if ctl is None:
            if idx >= 0:
                self.source.removeItem(idx)
            return
        label = t("workspace.live_source", pid=ctl.choice.participant_id if ctl.choice else "…")
        if idx < 0:
            self.source.insertItem(0, label, LIVE)
            idx = 0
        else:
            self.source.setItemText(idx, label)
        if self.source.currentIndex() == idx:
            self._load_live()
        else:
            self.source.setCurrentIndex(idx)

    def open_archive(self, folder: Path) -> bool:
        key = str(Path(folder).resolve())
        try:
            archived = load_session(Path(folder))
        except Exception as exc:  # noqa: BLE001 - shown to the experimenter
            QMessageBox.critical(self, t("cockpit.open_failed"), str(exc))
            return False
        self._cache.put(key, archived)
        idx = self.source.findData(key)
        if idx < 0:
            self.source.addItem(t("workspace.recording_source", pid=archived.participant_id,
                                  folder=Path(folder).name), key)
            idx = self.source.count() - 1
        if self.source.currentIndex() == idx:
            self._source_changed(idx)
        else:
            self.source.setCurrentIndex(idx)
        return True

    @property
    def showing_live(self) -> bool:
        return self.current == LIVE

    def _load_live(self) -> None:
        self.current = LIVE
        self.banner.hide()
        if self.ctl is not None:
            self.tabs.load(SessionView.from_controller(self.ctl))

    def _source_changed(self, idx: int) -> None:
        key = self.source.itemData(idx)
        if key is None:
            return
        if key == LIVE:
            self._load_live()
            return
        archived = self._cache.get(key)
        if archived is None:
            try:
                archived = load_session(Path(key))
            except Exception:  # noqa: BLE001 - folder vanished meanwhile
                return
            self._cache.put(key, archived)
        self.current = key
        self.banner.setText(t("cockpit.archived_banner", id=archived.dir.name)
                            + (("  ·  " + t("cockpit.archive_problems", problems="; ".join(archived.problems[:3])))
                               if archived.problems else ""))
        self.banner.show()
        self.tabs.load(SessionView.from_archive(archived))
