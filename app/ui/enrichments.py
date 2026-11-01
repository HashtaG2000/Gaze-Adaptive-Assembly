"""Workspace -> Enrichments: edits the content the app and the LLMs work with.

    Instruction sets  base sets: steps (type, part, positions, text, MTM time, media, reference)
    Media library     import images / videos, role and a description the LLM receives
    Reference images  per step the correct board photo for the camera check (file or camera)
    LLM rules         the analysis rules (on/off, category, text)

Everything is stored as JSON through ContentRepository: validated with the same pydantic models
the app uses, written atomically, and picked up by the next session without a restart.
Nothing is saved while a check fails; the errors are listed instead.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from pydantic import ValidationError
from PySide6.QtCore import QRegularExpression, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPixmap, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from app.core.files import UnsafePathError, safe_name
from app.core.repository import ContentRepository, SetEntry
from app.i18n import items, t
from app.models import InstructionSet, Media, MediaCatalog, MediaItem, Rule, RuleSet, Step
from app.models.instruction import MAX_STEP_TEXT
from app.camera.source import CameraSource
from app.ui.icons import icon, set_button_icon
from app.ui.style import C
from app.ui.workspace_widgets import ImageView, load_pixmap, plain

STEP_TYPES = ("pick", "place")          # base sets only use atomic steps
ROLES = ("part", "position", "method")
SLUG = QRegularExpression(r"[a-z0-9][a-z0-9_-]{0,63}")
MAX_ERRORS_SHOWN = 15


def _btn(text: str, icon_key: str, name: str = "") -> QPushButton:
    b = QPushButton("  " + text.replace("&", "&&"))   # "&" is a Qt mnemonic marker
    if name:
        b.setObjectName(name)
    set_button_icon(b, icon_key, C["emerald_300"] if name == "ok" else C["rose_300"] if name == "danger"
                    else C["zinc_200"], disabled=C["zinc_500"])
    b.setIconSize(QSize(14, 14))
    return b


def _table(columns: list[str]) -> QTableWidget:
    tb = QTableWidget(0, len(columns))
    tb.setHorizontalHeaderLabels(columns)
    tb.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tb.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    tb.setAlternatingRowColors(True)
    tb.verticalHeader().setVisible(False)
    tb.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    tb.horizontalHeader().setStretchLastSection(True)
    return tb


def _cell(tb: QTableWidget, r: int, c: int) -> str:
    item = tb.item(r, c)
    return item.text().strip() if item else ""


def _combo(options: tuple[str, ...], value: str) -> QComboBox:
    cb = QComboBox()
    cb.addItems(list(options))
    if value in options:
        cb.setCurrentText(value)
    return cb


def _errors(exc: Exception) -> list[str]:
    if isinstance(exc, ValidationError):
        return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return [str(exc)]


def _show_errors(parent: QWidget, errors: list[str]) -> None:
    shown = errors[:MAX_ERRORS_SHOWN] + ([f"… (+{len(errors) - MAX_ERRORS_SHOWN})"] if len(errors) > MAX_ERRORS_SHOWN else [])
    QMessageBox.warning(parent, t("author.save"), t("author.invalid", errors="\n".join(shown)))


def _confirm(parent: QWidget, name: str) -> bool:
    return QMessageBox.question(parent, t("author.delete"), t("author.confirm_delete", name=name)) \
        == QMessageBox.StandardButton.Yes


# --- instruction sets -------------------------------------------------------------------------------

class SetEditor(QWidget):
    COLS = ("type", "part", "positions", "text", "expected", "media")

    def __init__(self, repo: ContentRepository) -> None:
        super().__init__()
        self.repo = repo
        self.entries: list[SetEntry] = []
        self.current_path: Optional[Path] = None
        self.loaded_id: Optional[str] = None
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._select)
        self.btn_new = _btn(t("author.new"), "plus")
        self.btn_dup = _btn(t("author.duplicate"), "copy")
        self.btn_del = _btn(t("author.delete"), "trash", "danger")
        self.btn_new.clicked.connect(self._new)
        self.btn_dup.clicked.connect(self._duplicate)
        self.btn_del.clicked.connect(self._delete)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.list, 1)
        row = QHBoxLayout()
        for b in (self.btn_new, self.btn_dup, self.btn_del):
            row.addWidget(b)
        ll.addLayout(row)

        self.id_edit = QLineEdit()
        self.id_edit.setValidator(QRegularExpressionValidator(SLUG, self.id_edit))
        self.id_edit.setMaxLength(64)
        self.title_edit = QLineEdit()
        self.title_edit.setMaxLength(120)
        self.problems = QLabel()
        self.problems.setObjectName("muted")
        self.problems.setWordWrap(True)
        self.problems.setTextFormat(Qt.TextFormat.PlainText)
        form = QFormLayout()
        form.addRow(t("author.id"), self.id_edit)
        form.addRow(t("author.title"), self.title_edit)
        cols = items("author.set_columns") or ["Type", "Part", "Positions", "Text", "Expected s", "Media",
                                               "Reference"]
        self.table = _table(cols)
        self.table.setColumnWidth(0, 90)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 380)
        self.table.setColumnWidth(4, 90)
        self.btn_add = _btn(t("author.add_step"), "plus")
        self.btn_rm = _btn(t("author.remove_step"), "trash")
        self.btn_up = _btn(t("author.move_up"), "up")
        self.btn_down = _btn(t("author.move_down"), "down")
        self.btn_save = _btn(t("author.save"), "save", "ok")
        self.btn_add.clicked.connect(self._add_step)
        self.btn_rm.clicked.connect(self._remove_step)
        self.btn_up.clicked.connect(lambda: self._move(-1))
        self.btn_down.clicked.connect(lambda: self._move(+1))
        self.btn_save.clicked.connect(self._save)
        hint = QLabel(t("author.media_hint"))
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        buttons = QHBoxLayout()
        for b in (self.btn_add, self.btn_rm, self.btn_up, self.btn_down):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_save)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addLayout(form)
        rl.addWidget(self.problems)
        rl.addWidget(self.table, 1)
        rl.addWidget(hint)
        rl.addLayout(buttons)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([260, 1000])
        lay = QVBoxLayout(self)
        lay.addWidget(split)
        self.reload()

    # --- list ---------------------------------------------------------------------------------
    def reload(self, select: Optional[Path] = None) -> None:
        self.entries = self.repo.list_sets()
        self.list.blockSignals(True)
        self.list.clear()
        for e in self.entries:
            title = e.iset.title if e.iset else e.path.name
            item = QListWidgetItem(f"{title}\n{e.path.name}")
            item.setIcon(icon("valid" if e.ok else "warning", C["emerald_400"] if e.ok else C["amber_400"]))
            self.list.addItem(item)
        self.list.blockSignals(False)
        row = next((i for i, e in enumerate(self.entries) if select and e.path == select), 0 if self.entries else -1)
        self.list.setCurrentRow(row)
        if row < 0:
            self._load(None, None)

    def _select(self, row: int) -> None:
        if 0 <= row < len(self.entries):
            e = self.entries[row]
            self._load(e.iset, e.path, list(e.errors))

    def _load(self, iset: Optional[InstructionSet], path: Optional[Path], problems: Optional[list[str]] = None) -> None:
        self.current_path = path
        self.loaded_id = iset.id if iset else None
        self.id_edit.setText(iset.id if iset else "")
        self.title_edit.setText(iset.title if iset else "")
        self.problems.setText("\n".join((problems or [])[:8]))
        self.problems.setVisible(bool(problems))
        self.table.setRowCount(0)
        for s in (iset.steps if iset else []):
            self._append_row(s.type, s.part_id, ", ".join(s.position_ids), s.text, f"{s.expected_time_s:g}",
                             "; ".join(m.path for m in s.media), s.reference_image or "")
        self.btn_del.setEnabled(path is not None)
        self.btn_dup.setEnabled(iset is not None)

    # --- rows ---------------------------------------------------------------------------------
    def _append_row(self, stype: str, part: str, positions: str, text: str, expected: str, media: str,
                    reference: str = "", at: Optional[int] = None) -> None:
        r = self.table.rowCount() if at is None else at
        self.table.insertRow(r)
        self.table.setCellWidget(r, 0, _combo(STEP_TYPES, stype))
        for c, v in ((1, part), (2, positions), (3, text), (4, expected), (5, media), (6, reference)):
            self.table.setItem(r, c, QTableWidgetItem(v))

    def _row_values(self, r: int) -> tuple[str, ...]:
        cb = self.table.cellWidget(r, 0)
        stype = cb.currentText() if isinstance(cb, QComboBox) else "pick"
        return (stype, *(_cell(self.table, r, c) for c in range(1, 7)))

    def _add_step(self) -> None:
        r = self.table.currentRow()
        at = r + 1 if r >= 0 else self.table.rowCount()
        prev = self._row_values(at - 1) if at > 0 else None
        stype = "place" if prev and prev[0] == "pick" else "pick"
        self._append_row(stype, prev[1] if prev and stype == "place" else "", "", "", "2", "", "", at)
        self.table.selectRow(at)

    def _remove_step(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)

    def _move(self, delta: int) -> None:
        r = self.table.currentRow()
        to = r + delta
        if r < 0 or not 0 <= to < self.table.rowCount():
            return
        vals = self._row_values(r)
        self.table.removeRow(r)
        self._append_row(*vals, at=to)
        self.table.selectRow(to)

    # --- build / save -------------------------------------------------------------------------
    def _media(self, paths: str, row: int, errors: list[str]) -> list[Media]:
        by_path = self.repo.catalog().by_path()
        out = []
        for p in (x.strip().replace("\\", "/") for x in paths.split(";")):
            if not p:
                continue
            item = by_path.get(p)
            mtype = item.type if item else self.repo.media_type_of(p)
            if mtype is None:
                errors.append(f"step {row + 1}: unknown media type for {p!r}")
                continue
            role = item.role if item else "method" if mtype == "video" else "part"
            try:
                out.append(Media(type=mtype, path=p, role=role))
            except ValidationError as exc:
                errors += [f"step {row + 1}: {e}" for e in _errors(exc)]
        return out

    def build(self) -> tuple[Optional[InstructionSet], list[str]]:
        errors: list[str] = []
        steps = []
        for r in range(self.table.rowCount()):
            stype, part, positions, text, expected, media, reference = self._row_values(r)
            try:
                exp = float(expected.replace(",", "."))
            except ValueError:
                errors.append(f"step {r + 1}: expected time must be a number")
                continue
            try:
                steps.append(Step(index=r, type=stype, text=text[:MAX_STEP_TEXT], part_id=part,
                                  position_ids=[p.strip() for p in positions.split(",") if p.strip()],
                                  expected_time_s=exp, media=self._media(media, r, errors),
                                  reference_image=reference.replace("\\", "/") or None))
            except ValidationError as exc:
                errors += [f"step {r + 1}: {e}" for e in _errors(exc)]
        if errors:
            return None, errors
        try:
            iset = InstructionSet(id=self.id_edit.text().strip(), title=self.title_edit.text(), steps=steps)
        except ValidationError as exc:
            return None, _errors(exc)
        return iset, self.repo.missing_media(iset)

    def _save(self) -> None:
        iset, errors = self.build()
        if iset is None or errors:
            _show_errors(self, errors)
            return
        # same set (id unchanged): overwrite its file; new or renamed set: new file, never overwrite another
        path = self.current_path if self.current_path is not None and iset.id == self.loaded_id else None
        if path is None:
            path = self.repo.set_path(iset.id)
            if path.exists():
                _show_errors(self, [f"a set file {path.name} already exists"])
                return
        try:
            saved = self.repo.save_set(iset, path)
        except (ValueError, OSError, UnsafePathError) as exc:
            _show_errors(self, [str(exc)])
            return
        self.reload(select=saved)
        QMessageBox.information(self, t("author.save"), t("author.saved"))

    def _new(self) -> None:
        self.list.clearSelection()
        self._load(None, None)
        self._append_row("pick", "", "", "", "1.5", "")
        self._append_row("place", "", "", "", "2.5", "")

    def _duplicate(self) -> None:
        iset, _ = self.build()
        rows = [self._row_values(r) for r in range(self.table.rowCount())]
        new_id = safe_name(f"{self.id_edit.text() or 'set'}_copy").lower().replace(".", "_")[:64]
        title = self.title_edit.text()
        self._load(None, None)
        self.id_edit.setText(new_id)
        self.title_edit.setText(f"{title} (copy)"[:120])
        for v in rows:
            self._append_row(*v)

    def _delete(self) -> None:
        if self.current_path is None or not _confirm(self, self.current_path.name):
            return
        try:
            self.repo.delete_set(self.current_path)
        except (OSError, UnsafePathError) as exc:
            _show_errors(self, [str(exc)])
            return
        self.reload()


# --- media library -----------------------------------------------------------------------------------

class MediaEditor(QWidget):
    def __init__(self, repo: ContentRepository) -> None:
        super().__init__()
        self.repo = repo
        cols = items("author.media_columns") or ["ID", "Type", "Role", "Path", "Description"]
        self.table = _table(cols)
        self.table.setColumnWidth(0, 200)
        self.table.setColumnWidth(1, 70)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 320)
        self.btn_import = _btn(t("author.import_media"), "import")
        self.btn_rm = _btn(t("author.delete"), "trash", "danger")
        self.btn_save = _btn(t("author.save"), "save", "ok")
        self.btn_import.clicked.connect(self._import)
        self.btn_rm.clicked.connect(self._remove)
        self.btn_save.clicked.connect(self._save)
        hint = QLabel(t("author.media_desc_hint"))
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_import)
        buttons.addWidget(self.btn_rm)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_save)
        lay = QVBoxLayout(self)
        lay.addWidget(self.table, 1)
        lay.addWidget(hint)
        lay.addLayout(buttons)
        self.reload()

    def reload(self) -> None:
        self.table.setRowCount(0)
        for it in self.repo.catalog().items:
            self._append(it.id, it.type, it.role, it.path, it.description, ", ".join(it.tags))

    def _append(self, mid: str, mtype: str, role: str, path: str, desc: str, tags: str = "") -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        id_item = QTableWidgetItem(mid)
        id_item.setData(Qt.ItemDataRole.UserRole, tags)
        self.table.setItem(r, 0, id_item)
        type_item = QTableWidgetItem(mtype)
        type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(r, 1, type_item)
        self.table.setCellWidget(r, 2, _combo(ROLES, role))
        path_item = QTableWidgetItem(path)
        path_item.setFlags(path_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if self.repo.media_file(path) is None:
            path_item.setForeground(QColor(C["rose_400"]))
            path_item.setToolTip("file missing")
        self.table.setItem(r, 3, path_item)
        self.table.setItem(r, 4, QTableWidgetItem(desc))

    def _ids(self) -> set[str]:
        return {_cell(self.table, r, 0) for r in range(self.table.rowCount())}

    def _import(self) -> None:
        exts = " ".join(f"*.{e}" for e in self.repo.cfg.limits.image_extensions + self.repo.cfg.limits.video_extensions)
        files, _ = QFileDialog.getOpenFileNames(self, t("author.import_media"), "", f"Media ({exts})")
        errors = []
        ids = self._ids()
        for f in files:
            try:
                rel = self.repo.import_media(Path(f))
            except (ValueError, OSError, UnsafePathError) as exc:
                errors.append(f"{Path(f).name}: {exc}")
                continue
            base = safe_name(Path(rel).stem).replace(".", "_")[:60] or "media"
            mid, n = base, 1
            while mid in ids:
                n += 1
                mid = f"{base}_{n}"
            ids.add(mid)
            mtype = self.repo.media_type_of(rel) or "image"
            self._append(mid, mtype, "method" if mtype == "video" else "part", rel, "")
        if errors:
            _show_errors(self, errors)

    def _remove(self) -> None:
        r = self.table.currentRow()
        if r >= 0 and _confirm(self, _cell(self.table, r, 0)):
            self.table.removeRow(r)   # the catalog entry only; the file stays in the media folder

    def _save(self) -> None:
        entries, errors = [], []
        for r in range(self.table.rowCount()):
            cb = self.table.cellWidget(r, 2)
            tags_raw = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) if self.table.item(r, 0) else ""
            try:
                entries.append(MediaItem(id=_cell(self.table, r, 0), type=_cell(self.table, r, 1),
                                         role=cb.currentText() if isinstance(cb, QComboBox) else "part",
                                         path=_cell(self.table, r, 3), description=_cell(self.table, r, 4)[:400],
                                         tags=[x.strip() for x in str(tags_raw or "").split(",") if x.strip()][:20]))
            except ValidationError as exc:
                errors += [f"row {r + 1}: {e}" for e in _errors(exc)]
        try:
            catalog = MediaCatalog(items=entries)
        except ValidationError as exc:
            errors += _errors(exc)
        if errors:
            _show_errors(self, errors)
            return
        try:
            self.repo.save_catalog(catalog)
        except OSError as exc:
            _show_errors(self, [str(exc)])
            return
        QMessageBox.information(self, t("author.save"), t("author.saved"))


# --- rules ----------------------------------------------------------------------------------------

class RulesEditor(QWidget):
    def __init__(self, repo: ContentRepository) -> None:
        super().__init__()
        self.repo = repo
        self.ruleset = RuleSet()
        cols = items("author.rule_columns") or ["On", "Category", "Rule"]
        self.table = _table(["ID"] + cols)
        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(1, 50)
        self.table.setColumnWidth(2, 140)
        self.table.setWordWrap(True)
        self.draft = QCheckBox(t("author.rules_draft"))
        self.btn_add = _btn(t("author.add_rule"), "plus")
        self.btn_rm = _btn(t("author.remove_rule"), "trash", "danger")
        self.btn_save = _btn(t("author.save"), "save", "ok")
        self.btn_add.clicked.connect(self._add)
        self.btn_rm.clicked.connect(self._remove)
        self.btn_save.clicked.connect(self._save)
        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_add)
        buttons.addWidget(self.btn_rm)
        buttons.addStretch(1)
        buttons.addWidget(self.draft)
        buttons.addWidget(self.btn_save)
        lay = QVBoxLayout(self)
        lay.addWidget(self.table, 1)
        lay.addLayout(buttons)
        self.reload()

    def reload(self) -> None:
        self.ruleset = self.repo.rules()
        self.draft.setChecked(self.ruleset.draft)
        self.table.setRowCount(0)
        for rule in self.ruleset.rules:
            self._append(rule.id, rule.enabled, rule.category, rule.text)
        self.table.resizeRowsToContents()

    def _append(self, rid: str, enabled: bool, category: str, text: str) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(rid))
        on = QTableWidgetItem()
        on.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        on.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        self.table.setItem(r, 1, on)
        self.table.setItem(r, 2, QTableWidgetItem(category))
        self.table.setItem(r, 3, QTableWidgetItem(text))

    def _add(self) -> None:
        ids = {_cell(self.table, r, 0) for r in range(self.table.rowCount())}
        n = self.table.rowCount() + 1
        while f"R{n:02d}" in ids:
            n += 1
        self._append(f"R{n:02d}", True, "General", "")
        self.table.selectRow(self.table.rowCount() - 1)

    def _remove(self) -> None:
        r = self.table.currentRow()
        if r >= 0 and _confirm(self, _cell(self.table, r, 0)):
            self.table.removeRow(r)

    def _save(self) -> None:
        rules, errors = [], []
        for r in range(self.table.rowCount()):
            on = self.table.item(r, 1)
            try:
                rules.append(Rule(id=_cell(self.table, r, 0), enabled=bool(on and on.checkState() == Qt.CheckState.Checked),
                                  category=_cell(self.table, r, 2)[:40] or "General", text=_cell(self.table, r, 3)))
            except ValidationError as exc:
                errors += [f"row {r + 1}: {e}" for e in _errors(exc)]
        try:
            ruleset = RuleSet(version=self.ruleset.version, draft=self.draft.isChecked(), note=self.ruleset.note,
                              rules=rules)
        except ValidationError as exc:
            errors += _errors(exc)
        if errors:
            _show_errors(self, errors)
            return
        try:
            self.repo.save_rules(ruleset)
        except OSError as exc:
            _show_errors(self, [str(exc)])
            return
        self.ruleset = ruleset
        QMessageBox.information(self, t("author.save"), t("author.saved"))


# --- reference images (camera check) ------------------------------------------------------------

class ReferenceEditor(QWidget):
    """One reference photo per base step: the correct board AFTER that step. Imported from a file
    or taken with the board camera (live preview); stored under data/media/references/<set>/."""
    PREVIEW_MS = 200
    _next_request = -1                      # negative ids never collide with session photo ids

    def __init__(self, repo: ContentRepository, camera_provider: Callable[[], Optional[CameraSource]]) -> None:
        super().__init__()
        self.repo = repo
        self.camera_provider = camera_provider
        self.entries: list[SetEntry] = []
        self.iset: Optional[InstructionSet] = None
        self.path: Optional[Path] = None
        self.refs: list[Optional[str]] = []
        self._waiting: dict[int, int] = {}       # request id -> step index
        self._connected: set[int] = set()        # cameras whose signals are connected

        self.set_combo = QComboBox()
        self.set_combo.currentIndexChanged.connect(self._load_set)
        cols = items("workspace.reference_columns") or ["Step", "Type", "Text", "Reference image"]
        self.table = _table(cols)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setIconSize(QSize(64, 40))
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 70)
        self.table.setColumnWidth(2, 360)
        self.table.itemSelectionChanged.connect(self._show_selected)
        self.preview_ref = ImageView(t("workspace.reference"))
        self.preview_cam = ImageView(t("workspace.camera_live"))
        self.status = plain("", "muted")
        self.btn_import = _btn(t("workspace.ref_import"), "import")
        self.btn_capture = _btn(t("workspace.ref_capture"), "camera", "ok")
        self.btn_clear = _btn(t("workspace.ref_clear"), "trash", "danger")
        self.btn_save = _btn(t("author.save"), "save", "ok")
        self.btn_import.clicked.connect(self._import)
        self.btn_capture.clicked.connect(self._capture)
        self.btn_clear.clicked.connect(self._clear)
        self.btn_save.clicked.connect(self._save)
        hint = plain(t("workspace.ref_hint"), "muted")
        top = QHBoxLayout()
        top.addWidget(QLabel(t("workspace.instruction_set")))
        top.addWidget(self.set_combo, 1)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addLayout(top)
        ll.addWidget(self.table, 1)
        ll.addWidget(hint)
        previews = QHBoxLayout()
        previews.addWidget(self.preview_ref, 1)
        previews.addWidget(self.preview_cam, 1)
        buttons = QHBoxLayout()
        for b in (self.btn_import, self.btn_capture, self.btn_clear):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_save)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addLayout(previews, 1)
        rl.addWidget(self.status)
        rl.addLayout(buttons)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([560, 700])
        lay = QVBoxLayout(self)
        lay.addWidget(split)
        self._timer = QTimer(self)
        self._timer.setInterval(self.PREVIEW_MS)
        self._timer.timeout.connect(self._preview_frame)
        self.reload()

    # --- sets -------------------------------------------------------------------------------
    def reload(self) -> None:
        self.entries = [e for e in self.repo.list_sets() if e.iset is not None and e.iset.is_base()]
        self.set_combo.blockSignals(True)
        self.set_combo.clear()
        for e in self.entries:
            self.set_combo.addItem(f"{e.iset.title}  ({e.path.name})")
        self.set_combo.blockSignals(False)
        self._load_set(self.set_combo.currentIndex())

    def _load_set(self, idx: int) -> None:
        entry = self.entries[idx] if 0 <= idx < len(self.entries) else None
        self.iset = entry.iset if entry else None
        self.path = entry.path if entry else None
        self.refs = [s.reference_image for s in self.iset.steps] if self.iset else []
        self._fill()

    def _fill(self) -> None:
        steps = self.iset.steps if self.iset else []
        self.table.setRowCount(len(steps))
        for r, s in enumerate(steps):
            ref = self.refs[r]
            vals = [s.index + 1, s.type, s.text, ref or t("workspace.no_reference")]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c == 3:
                    pm = load_pixmap(self.repo.media_file(ref)) if ref else None
                    if pm is not None:
                        item.setIcon(QIcon(pm.scaled(64, 40, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation)))
                    else:
                        item.setForeground(QColor(C["rose_400"] if ref else C["amber_300"]))
                self.table.setItem(r, c, item)
        missing = sum(1 for r in self.refs if not r)
        self.status.setText(t("workspace.ref_status", n=len(steps), missing=missing))
        self._show_selected()

    def _row(self) -> int:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return rows[0].row() if rows else -1

    def _show_selected(self) -> None:
        r = self._row()
        ok = 0 <= r < len(self.refs)
        for b in (self.btn_import, self.btn_capture, self.btn_clear):
            b.setEnabled(ok)
        self.btn_save.setEnabled(self.iset is not None)
        ref = self.refs[r] if ok else None
        self.preview_ref.set_image(self.repo.media_file(ref) if ref else None, t("workspace.no_reference"))

    # --- camera preview ---------------------------------------------------------------------
    def showEvent(self, e) -> None:
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)

    def _preview_frame(self) -> None:
        cam = self.camera_provider()
        sink = cam.video_sink() if cam is not None else None
        frame = sink.videoFrame() if sink is not None else None
        if frame is None or not frame.isValid():
            self.preview_cam.pixmap = None
            self.preview_cam.placeholder = t("workspace.camera_none") if cam is None else cam.status().message
            self.preview_cam.update()
            return
        img = frame.toImage()
        if not img.isNull():
            self.preview_cam.pixmap = QPixmap.fromImage(img.scaled(640, 360, Qt.AspectRatioMode.KeepAspectRatio))
            self.preview_cam.update()

    # --- actions ----------------------------------------------------------------------------
    def _target_dir(self) -> str:
        return f"references/{self.iset.id}" if self.iset else "references"

    def _import(self) -> None:
        r = self._row()
        if r < 0 or self.iset is None:
            return
        exts = " ".join(f"*.{e}" for e in self.repo.cfg.limits.image_extensions)
        f, _ = QFileDialog.getOpenFileName(self, t("workspace.ref_import"), "", f"Images ({exts})")
        if f:
            self.import_file(r, Path(f))

    def import_file(self, row: int, src: Path) -> bool:
        if self.repo.media_type_of(src.name) != "image":
            _show_errors(self, [t("workspace.ref_not_image")])
            return False
        try:
            self.refs[row] = self.repo.import_media(src, self._target_dir())
        except (ValueError, OSError, UnsafePathError) as exc:
            _show_errors(self, [str(exc)])
            return False
        self._fill()
        self.table.selectRow(row)
        return True

    def _capture(self) -> None:
        r = self._row()
        cam = self.camera_provider()
        if r < 0 or self.iset is None:
            return
        if cam is None or not cam.status().ready:
            _show_errors(self, [t("workspace.camera_not_ready")])
            return
        if id(cam) not in self._connected:
            cam.captured.connect(self._on_captured)
            cam.failed.connect(self._on_failed)
            self._connected.add(id(cam))
        rid = ReferenceEditor._next_request
        ReferenceEditor._next_request -= 1
        self._waiting[rid] = r
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        rel = f"{self._target_dir()}/step_{r + 1:02d}_{stamp}.jpg"
        self.status.setText(t("workspace.ref_capturing"))
        cam.capture(rid, self.repo.media_dir / rel)

    def _on_captured(self, rid: int, path: str) -> None:
        r = self._waiting.pop(rid, None)
        if r is None:
            return
        try:
            self.refs[r] = Path(path).resolve().relative_to(self.repo.media_dir.resolve()).as_posix()
        except ValueError:
            return
        self._fill()
        self.table.selectRow(r)

    def _on_failed(self, rid: int, message: str) -> None:
        if self._waiting.pop(rid, None) is not None:
            _show_errors(self, [message])

    def _clear(self) -> None:
        r = self._row()
        if 0 <= r < len(self.refs):
            self.refs[r] = None
            self._fill()
            self.table.selectRow(r)

    def save(self) -> bool:
        if self.iset is None or self.path is None:
            return False
        steps = [s.model_copy(update={"reference_image": ref}) for s, ref in zip(self.iset.steps, self.refs)]
        try:
            new = InstructionSet.model_validate(self.iset.model_copy(update={"steps": steps}).model_dump())
            self.repo.save_set(new, self.path)
        except (ValidationError, ValueError, OSError, UnsafePathError) as exc:
            _show_errors(self, _errors(exc))
            return False
        self.iset = new
        return True

    def _save(self) -> None:
        if self.save():
            QMessageBox.information(self, t("author.save"), t("author.saved"))


class EnrichmentsPage(QTabWidget):
    """Workspace -> Enrichments: instruction sets, media library, reference images, LLM rules."""

    def __init__(self, repo: ContentRepository, camera_provider: Callable[[], Optional[CameraSource]],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo
        self.sets = SetEditor(repo)
        self.media = MediaEditor(repo)
        self.references = ReferenceEditor(repo, camera_provider)
        self.rules = RulesEditor(repo)
        self.addTab(self.sets, icon("steps", C["zinc_300"]), t("author.tabs.sets"))
        self.addTab(self.media, icon("media", C["zinc_300"]), t("author.tabs.media"))
        self.addTab(self.references, icon("camera", C["zinc_300"]), t("workspace.tab_references"))
        self.addTab(self.rules, icon("rules", C["zinc_300"]), t("author.tabs.rules"))
        self.currentChanged.connect(self._tab_changed)

    def _tab_changed(self, idx: int) -> None:
        if self.widget(idx) is self.references:
            self.references.reload()     # sets may have been edited in the first tab
