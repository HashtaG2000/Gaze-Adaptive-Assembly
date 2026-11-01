"""Workspace -> Downloads: every file the app has produced, in one place.

    Sessions   one folder per participant session: logs, summaries, photos, versions, exports
    App files  configuration, instruction sets, media library, LLM rules and prompts, AOI layouts,
               application log

Folders are listed lazily (on expand), so the page opens instantly with many sessions.
Actions: open, show in folder, save a copy elsewhere, export a session package, export the AOI
layout. Only files inside the app's own folders are listed.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QHeaderView, QMessageBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app.config import AppConfig
from app.i18n import items, t
from app.pipeline.aoi_export import export_aoi_layout
from app.pipeline.archive import list_sessions
from app.ui.icons import icon
from app.ui.style import C
from app.ui.workspace_widgets import btn, export_dir, plain

PATH_ROLE = Qt.ItemDataRole.UserRole
LOADED_ROLE = Qt.ItemDataRole.UserRole + 1
MAX_ENTRIES = 2000           # per folder (photos folders can be large)


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


class DownloadsPage(QWidget):
    def __init__(self, cfg: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.sessions_root = cfg.resolve(cfg.paths.sessions)
        self.exports_root = cfg.resolve(cfg.paths.exports)
        cols = items("workspace.download_columns") or ["Name", "Type", "Size", "Modified"]
        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(cols))
        self.tree.setHeaderLabels(cols)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(cols)):
            self.tree.header().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemExpanded.connect(self._expand)
        self.tree.itemSelectionChanged.connect(self._selection)
        self.tree.itemDoubleClicked.connect(lambda *_: self._open())
        self.info = plain("", "muted")
        self.btn_open = btn(t("workspace.dl_open"), "play")
        self.btn_folder = btn(t("workspace.open_folder"), "sessions")
        self.btn_copy = btn(t("workspace.dl_copy"), "copy")
        self.btn_export = btn(t("cockpit.export"), "export")
        self.btn_aoi = btn(t("workspace.dl_aoi"), "steps")
        self.btn_refresh = btn(t("archive.refresh"), "refresh")
        self.btn_open.clicked.connect(self._open)
        self.btn_folder.clicked.connect(self._show_in_folder)
        self.btn_copy.clicked.connect(self._save_copy)
        self.btn_export.clicked.connect(self._export_session)
        self.btn_aoi.clicked.connect(self._export_aoi)
        self.btn_refresh.clicked.connect(self.refresh)
        row = QHBoxLayout()
        for b in (self.btn_open, self.btn_folder, self.btn_copy):
            row.addWidget(b)
        row.addStretch(1)
        for b in (self.btn_export, self.btn_aoi, self.btn_refresh):
            row.addWidget(b)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(row)
        lay.addWidget(self.tree, 1)
        lay.addWidget(self.info)
        self.refresh()

    # --- tree --------------------------------------------------------------------------------
    def refresh(self) -> None:
        self.tree.clear()
        sessions = self._group(t("workspace.dl_sessions"), self.sessions_root)
        for s in list_sessions(self.sessions_root):
            self._add(sessions, s.folder, label=f"{s.participant_id}  ·  {s.folder.name}")
        sessions.setExpanded(True)
        app_files = self._group(t("workspace.dl_app_files"), None)
        c = self.cfg
        for path in (c.resolve(c.paths.exports), c.resolve(c.paths.instruction_sets), c.resolve(c.paths.media_catalog),
                     c.resolve(c.paths.rules), c.resolve(c.paths.prompts), c.resolve(c.paths.surveys),
                     c.resolve(c.paths.logs), c.resolve(Path("config.yaml"))):
            if path.exists():
                self._add(app_files, path)
        app_files.setExpanded(True)
        self._selection()

    def _group(self, title: str, path: Optional[Path]) -> QTreeWidgetItem:
        item = QTreeWidgetItem([title, "", "", ""])
        item.setIcon(0, icon("sessions", C["purple_300"]))
        item.setData(0, PATH_ROLE, str(path) if path else None)
        item.setData(0, LOADED_ROLE, True)
        self.tree.addTopLevelItem(item)
        return item

    def _add(self, parent: QTreeWidgetItem, path: Path, label: Optional[str] = None) -> QTreeWidgetItem:
        try:
            st = path.stat()
        except OSError:
            st = None
        is_dir = path.is_dir()
        kind = t("workspace.dl_folder") if is_dir else (path.suffix.lstrip(".").upper() or "–")
        size = "" if is_dir or st is None else human_size(st.st_size)
        modified = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M") if st else ""
        item = QTreeWidgetItem([label or path.name, kind, size, modified])
        item.setIcon(0, icon("sessions" if is_dir else "copy", C["zinc_300"] if not is_dir else C["amber_300"]))
        item.setData(0, PATH_ROLE, str(path))
        item.setData(0, LOADED_ROLE, not is_dir)
        if is_dir:
            item.addChild(QTreeWidgetItem(["…"]))      # placeholder: children are listed on expand
        parent.addChild(item)
        return item

    def _expand(self, item: QTreeWidgetItem) -> None:
        if item.data(0, LOADED_ROLE):
            return
        item.takeChildren()
        item.setData(0, LOADED_ROLE, True)
        path = Path(item.data(0, PATH_ROLE))
        try:
            entries = sorted(path.iterdir(), key=lambda q: (not q.is_dir(), q.name.lower()))
        except OSError:
            return
        for child in entries[:MAX_ENTRIES]:
            if child.name.endswith((".part", ".tmp")):
                continue
            self._add(item, child)
        if len(entries) > MAX_ENTRIES:
            item.addChild(QTreeWidgetItem([t("workspace.dl_more", n=len(entries) - MAX_ENTRIES)]))

    # --- selection ---------------------------------------------------------------------------
    def selected_path(self) -> Optional[Path]:
        items_ = self.tree.selectedItems()
        raw = items_[0].data(0, PATH_ROLE) if items_ else None
        return Path(raw) if raw else None

    def _session_of(self, path: Optional[Path]) -> Optional[Path]:
        if path is None:
            return None
        try:
            rel = path.resolve().relative_to(self.sessions_root.resolve())
        except ValueError:
            return None
        return self.sessions_root / rel.parts[0] if rel.parts else None

    def _selection(self) -> None:
        path = self.selected_path()
        exists = path is not None and path.exists()
        self.btn_open.setEnabled(exists)
        self.btn_folder.setEnabled(exists)
        self.btn_copy.setEnabled(exists and path.is_file())
        self.btn_export.setEnabled(self._session_of(path) is not None)
        self.info.setText(str(path) if path else t("workspace.dl_hint"))

    # --- actions -----------------------------------------------------------------------------
    def _open(self) -> None:
        path = self.selected_path()
        if path is not None and path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _show_in_folder(self) -> None:
        path = self.selected_path()
        if path is not None and path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path if path.is_dir() else path.parent)))

    def _save_copy(self) -> None:
        path = self.selected_path()
        if path is None or not path.is_file():
            return
        target, _ = QFileDialog.getSaveFileName(self, t("workspace.dl_copy"), path.name)
        if not target:
            return
        try:
            shutil.copy2(path, target)
        except OSError as exc:
            QMessageBox.critical(self, t("workspace.dl_copy"), str(exc))

    def _export_session(self) -> None:
        folder = self._session_of(self.selected_path())
        if folder is not None and export_dir(folder, self) is not None:
            self.refresh()

    def _export_aoi(self) -> None:
        try:
            json_path, png_path = export_aoi_layout(self.cfg, self.exports_root)
        except Exception as exc:  # noqa: BLE001 - shown to the experimenter
            QMessageBox.critical(self, t("workspace.dl_aoi"), str(exc))
            return
        QMessageBox.information(self, t("workspace.dl_aoi"), t("workspace.dl_aoi_done", json=json_path.name,
                                                                  png=png_path.name, folder=json_path.parent))
        self.refresh()
