"""Content repository: instruction sets, media catalog, LLM rules and surveys (all JSON files).

Reads go through mtime-validated caches, so repeated access is free and edits made in the
Author tool are picked up immediately. Writes are validated first and then written atomically.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from app.config import AppConfig
from app.core.files import FileCache, UnsafePathError, read_json, safe_join, safe_name, write_json_atomic
from app.models import InstructionSet, MediaCatalog, RuleSet, Survey, base_structure_errors

log = logging.getLogger(__name__)
SET_FILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\.json$")


def _errors(exc: Exception) -> list[str]:
    if isinstance(exc, ValidationError):
        return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return [str(exc)]


@dataclass(frozen=True)
class SetEntry:
    path: Path
    iset: Optional[InstructionSet]
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.iset is not None and not self.errors


class ContentRepository:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.sets_dir = cfg.resolve(cfg.paths.instruction_sets)
        self.media_dir = cfg.resolve(cfg.paths.media)
        self.catalog_path = cfg.resolve(cfg.paths.media_catalog)
        self.rules_path = cfg.resolve(cfg.paths.rules)
        self.surveys_dir = cfg.resolve(cfg.paths.surveys)
        self._sets: FileCache[SetEntry] = FileCache(self._parse_set, max_entries=256)
        self._catalog: FileCache[MediaCatalog] = FileCache(lambda p: MediaCatalog.model_validate(read_json(p)), 4)
        self._rules: FileCache[RuleSet] = FileCache(lambda p: RuleSet.model_validate(read_json(p)), 4)
        self._surveys: FileCache[Survey] = FileCache(lambda p: Survey.model_validate(read_json(p)), 16)

    # --- instruction sets -------------------------------------------------------------
    def _parse_set(self, path: Path) -> SetEntry:
        try:
            iset = InstructionSet.model_validate(read_json(path))
        except (OSError, ValueError, ValidationError) as exc:
            return SetEntry(path, None, tuple(_errors(exc)))
        errors = list(base_structure_errors(iset)) if iset.is_base() else ["not a base set (level 0)"]
        errors += self.missing_media(iset)
        return SetEntry(path, iset, tuple(errors))

    def list_sets(self) -> list[SetEntry]:
        if not self.sets_dir.is_dir():
            return []
        entries = []
        for f in sorted(self.sets_dir.glob("*.json")):
            if SET_FILE_RE.match(f.name):
                try:
                    entries.append(self._sets.get(f))
                except OSError as exc:
                    entries.append(SetEntry(f, None, (str(exc),)))
        return entries

    def usable_sets(self) -> list[tuple[Path, InstructionSet]]:
        return [(e.path, e.iset) for e in self.list_sets() if e.ok]

    def set_path(self, set_id: str) -> Path:
        return safe_join(self.sets_dir, f"{set_id}.json")

    def save_set(self, iset: InstructionSet, path: Optional[Path] = None) -> Path:
        """Validate and write a base set. path: keep an existing file name (must be in the sets folder)."""
        if not iset.is_base():
            raise ValueError("only base sets (level 0, no merged steps) can be saved")
        errors = base_structure_errors(iset)
        if errors:
            raise ValueError("; ".join(errors))
        path = self._own_set_file(path) if path is not None else self.set_path(iset.id)
        write_json_atomic(path, iset.model_dump(mode="json"))
        self._sets.invalidate(path)
        return path

    def delete_set(self, path: Path) -> None:
        path = self._own_set_file(path)
        if path.exists():
            path.unlink()
        self._sets.invalidate(path)

    def _own_set_file(self, path: Path) -> Path:
        """A set file inside the sets folder with a safe name (never anything else)."""
        path = Path(path)
        if not SET_FILE_RE.match(path.name):
            raise UnsafePathError(f"invalid instruction set file name {path.name!r}")
        return safe_join(self.sets_dir, path.name)

    # --- media files ------------------------------------------------------------------------
    def media_type_of(self, name: str) -> Optional[str]:
        ext = name.rpartition(".")[2].lower()
        if ext in self.cfg.limits.image_extensions:
            return "image"
        if ext in self.cfg.limits.video_extensions:
            return "video"
        return None

    def import_media(self, src: Path, subdir: str = "imported") -> str:
        """Copy a file into the media folder (safe name, allowed type, size limit).

        Returns the relative media path. Never overwrites: a numbered name is chosen instead.
        """
        src = Path(src)
        if not src.is_file():
            raise ValueError(f"not a file: {src.name}")
        name = safe_name(src.name)
        if self.media_type_of(name) is None:
            raise ValueError(f"file type not allowed: {src.name}")
        size = src.stat().st_size
        if size > self.cfg.limits.max_media_mb * 1024 * 1024:
            raise ValueError(f"file larger than {self.cfg.limits.max_media_mb} MB: {src.name}")
        # nested sub-folders allowed ("references/<set>"); every part reduced to a safe name
        sub = "/".join(safe_name(part, "imported").replace(".", "_") for part in subdir.replace("\\", "/").split("/")
                       if part.strip()) or "imported"
        target_dir = safe_join(self.media_dir, sub)
        target_dir.mkdir(parents=True, exist_ok=True)
        stem, _, ext = name.rpartition(".")
        target = target_dir / name
        n = 1
        while target.exists():
            n += 1
            target = target_dir / f"{stem}_{n}.{ext}"
        tmp = target.with_name(target.name + ".part")
        try:
            shutil.copyfile(src, tmp)
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                tmp.unlink()
        return f"{sub}/{target.name}"

    def missing_media(self, iset: InstructionSet) -> list[str]:
        missing = []
        for s in iset.steps:
            if s.reference_image:
                try:
                    if not safe_join(self.media_dir, s.reference_image).is_file():
                        missing.append(f"step {s.index + 1}: reference image missing: {s.reference_image}")
                except UnsafePathError as exc:
                    missing.append(f"step {s.index + 1}: {exc}")
            for m in s.media:
                try:
                    if not safe_join(self.media_dir, m.path).is_file():
                        missing.append(f"step {s.index + 1}: media file missing: {m.path}")
                except UnsafePathError as exc:
                    missing.append(f"step {s.index + 1}: {exc}")
        return missing

    def media_file(self, rel: str) -> Optional[Path]:
        """Absolute path of a media file, or None if unsafe / missing."""
        try:
            p = safe_join(self.media_dir, rel)
        except UnsafePathError:
            log.warning("rejected unsafe media path %r", rel)
            return None
        return p if p.is_file() else None

    # --- catalog / rules / surveys ----------------------------------------------------------
    def catalog(self) -> MediaCatalog:
        try:
            return self._catalog.get(self.catalog_path)
        except (OSError, ValueError, ValidationError) as exc:
            log.warning("media catalog unreadable (%s) - using an empty catalog", exc)
            return MediaCatalog()

    def save_catalog(self, catalog: MediaCatalog) -> None:
        write_json_atomic(self.catalog_path, catalog.model_dump(mode="json"))
        self._catalog.invalidate(self.catalog_path)

    def rules(self) -> RuleSet:
        try:
            return self._rules.get(self.rules_path)
        except (OSError, ValueError, ValidationError) as exc:
            log.warning("rules unreadable (%s) - no rules used", exc)
            return RuleSet(rules=[])

    def save_rules(self, rules: RuleSet) -> None:
        write_json_atomic(self.rules_path, rules.model_dump(mode="json"))
        self._rules.invalidate(self.rules_path)

    def survey(self, survey_id: str) -> Optional[Survey]:
        try:
            return self._surveys.get(safe_join(self.surveys_dir, f"{survey_id}.json"))
        except (OSError, ValueError, ValidationError, UnsafePathError) as exc:
            log.warning("survey %r unreadable: %s", survey_id, exc)
            return None
