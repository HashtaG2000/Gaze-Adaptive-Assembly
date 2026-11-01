"""User-visible text, loaded from data/locale/<lang>.json (nested keys, dotted access).

    from app.i18n import t
    t("player.step_of", n=3, total=8)   ->  "Step 3 of 8"

Missing keys return the key itself (visible in the UI, never a crash); missing format fields are
left as-is. The file is cached and reloaded only when it changes on disk.
"""
from __future__ import annotations

import string
from pathlib import Path
from typing import Any, Optional

from app.config import PROJECT_ROOT          # frozen-aware app root
from app.core.files import FileCache, read_json

_DEFAULT_DIR = PROJECT_ROOT / "data" / "locale"

_cache: FileCache[dict] = FileCache(read_json, max_entries=8)
_state: dict[str, Any] = {"dir": _DEFAULT_DIR, "lang": "en"}


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def configure(locale_dir: Optional[Path] = None, lang: Optional[str] = None) -> None:
    if locale_dir is not None:
        _state["dir"] = Path(locale_dir)
    if lang is not None:
        if not lang.isalnum() or len(lang) > 10:
            raise ValueError(f"invalid language code {lang!r}")
        _state["lang"] = lang


def _strings() -> dict:
    path = Path(_state["dir"]) / f"{_state['lang']}.json"
    try:
        data = _cache.get(path)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def t(key: str, **fmt: Any) -> str:
    node: Any = _strings()
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return key
        node = node[part]
    if not isinstance(node, str):
        return key
    if not fmt:
        return node
    try:
        return string.Formatter().vformat(node, (), _SafeDict(fmt))
    except (ValueError, IndexError):
        return node


def items(key: str) -> list[str]:
    """A list of strings (e.g. table column headers); [] if missing."""
    node: Any = _strings()
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return []
        node = node[part]
    return [str(x) for x in node] if isinstance(node, list) else []


def section(key: str) -> dict:
    """A whole sub-dictionary (e.g. option labels)."""
    node: Any = _strings()
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return {}
        node = node[part]
    return node if isinstance(node, dict) else {}
