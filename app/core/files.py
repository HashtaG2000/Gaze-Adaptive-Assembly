"""Safe file handling used everywhere in the app.

- read_json / read_text: size-limited reads (protects against huge or corrupted files)
- write_json_atomic / write_text_atomic: write to a temp file, then replace (never leaves half files)
- safe_join: resolve a relative path inside a root directory, rejecting traversal (".."),
  absolute paths and drive letters
- FileCache: memoises parsed files and re-reads them only when size or mtime changes
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Generic, Optional, TypeVar

MAX_JSON_BYTES = 25 * 1024 * 1024        # 25 MB per JSON/text file
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_REL_PATH_RE = re.compile(r"^(?!/)(?![A-Za-z]:)[A-Za-z0-9._\-/ ]{1,255}$")

T = TypeVar("T")


class UnsafePathError(ValueError):
    pass


def read_text(path: Path, max_bytes: int = MAX_JSON_BYTES) -> str:
    path = Path(path)
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"{path.name} is too large ({size} bytes, limit {max_bytes})")
    return path.read_text(encoding="utf-8")


def read_json(path: Path, max_bytes: int = MAX_JSON_BYTES) -> Any:
    return json.loads(read_text(path, max_bytes))


def write_text_atomic(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path: Path, data: Any) -> None:
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def is_safe_rel_path(rel: str) -> bool:
    if not rel or not SAFE_REL_PATH_RE.match(rel):
        return False
    parts = rel.replace("\\", "/").split("/")
    return all(p not in ("", ".", "..") for p in parts)


def safe_join(root: Path, rel: str) -> Path:
    """root / rel, guaranteed to stay inside root."""
    if not is_safe_rel_path(rel):
        raise UnsafePathError(f"unsafe relative path: {rel!r}")
    root = Path(root).resolve()
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise UnsafePathError(f"path escapes its root: {rel!r}")
    return target


def safe_name(name: str, fallback: str = "file") -> str:
    """Reduce an arbitrary file name to [A-Za-z0-9._-], keeping the extension."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")[:80] or fallback
    ext = re.sub(r"[^A-Za-z0-9]+", "", ext)[:10].lower()
    return f"{stem}.{ext}" if ext else stem


class FileCache(Generic[T]):
    """Parsed-file cache: re-parses only when the file's (size, mtime) changes. Thread-safe, LRU-bounded."""

    def __init__(self, parse: Callable[[Path], T], max_entries: int = 64) -> None:
        self._parse = parse
        self._max = max_entries
        self._data: "OrderedDict[str, tuple[tuple[int, int], T]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, path: Path) -> T:
        path = Path(path)
        st = path.stat()
        stamp = (st.st_size, st.st_mtime_ns)
        key = str(path.resolve())
        with self._lock:
            hit = self._data.get(key)
            if hit is not None and hit[0] == stamp:
                self._data.move_to_end(key)
                return hit[1]
        value = self._parse(path)
        with self._lock:
            self._data[key] = (stamp, value)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)
        return value

    def invalidate(self, path: Optional[Path] = None) -> None:
        with self._lock:
            if path is None:
                self._data.clear()
            else:
                self._data.pop(str(Path(path).resolve()), None)


class LRUCache(Generic[T]):
    """Small bounded key/value LRU cache (e.g. decoded pixmaps)."""

    def __init__(self, max_entries: int = 128) -> None:
        self._max = max_entries
        self._data: "OrderedDict[Any, T]" = OrderedDict()

    def get(self, key: Any) -> Optional[T]:
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return None

    def put(self, key: Any, value: T) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def __contains__(self, key: Any) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)
