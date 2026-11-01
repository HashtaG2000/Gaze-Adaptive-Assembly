"""Stored sessions: index (cached) and read-only loading for the cockpit.

The index reads only session.json and summaries.json of each folder and caches them by file
mtime, so reopening the session browser is instant even with many sessions. Loading tolerates
partially written or corrupted files and sessions written by older app versions.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from app.core.files import FileCache, read_json
from app.models import (
    CalibrationRecord, DecisionRecord, InstructionSet, LLMCallRecord, ObservationRecord, RunSummary, SurveyRecord,
    VerificationRecord,
)
from app.pipeline.export import read_jsonl
from app.pipeline.summary import SummaryHistory

log = logging.getLogger(__name__)
FOLDER_RE = re.compile(r"^(?P<pid>[A-Za-z0-9_-]+)_(?P<stamp>\d{8}_\d{6})$")

_json_cache: FileCache[Any] = FileCache(read_json, max_entries=1024)


def _cached_json(path: Path) -> Any:
    try:
        return _json_cache.get(path) if path.is_file() else None
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class SessionInfo:
    folder: Path
    participant_id: str
    started: Optional[datetime]
    condition: str
    runs: int
    status: str            # ok | incomplete | broken
    duration_s: float = 0.0              # summed run durations (time actually spent assembling)
    llm_client: str = ""
    last_photo: Optional[Path] = None    # newest board photo (thumbnail)


def _total_duration(summaries: Any) -> float:
    total = 0.0
    for s in summaries if isinstance(summaries, list) else []:
        try:
            total += float(s["totals"]["total_duration_s"])
        except (KeyError, TypeError, ValueError):
            continue
    return total


def _last_photo(folder: Path) -> Optional[Path]:
    photos = folder / "photos"
    if not photos.is_dir():
        return None
    try:
        return max(photos.glob("*.jpg"), key=lambda q: q.name, default=None)
    except OSError:
        return None


def list_sessions(sessions_root: Path) -> list[SessionInfo]:
    root = Path(sessions_root)
    if not root.is_dir():
        return []
    out = []
    for d in root.iterdir():
        m = FOLDER_RE.match(d.name)
        if not d.is_dir() or not m:
            continue
        meta = _cached_json(d / "session.json")
        summaries = _cached_json(d / "summaries.json")
        if not isinstance(meta, dict):
            out.append(SessionInfo(d, m["pid"], None, "?", 0, "broken"))
            continue
        try:
            started = datetime.fromisoformat(str(meta.get("started_at")))
        except ValueError:
            started = None
        runs = len(summaries) if isinstance(summaries, list) else 0
        out.append(SessionInfo(d, str(meta.get("participant_id", m["pid"])), started,
                               str(meta.get("condition", "?")), runs, "ok" if runs else "incomplete",
                               _total_duration(summaries), str(meta.get("llm_client", "")), _last_photo(d)))
    # folder stamp (YYYYmmdd_HHMMSS) sorts chronologically and never mixes naive/aware datetimes
    out.sort(key=lambda s: FOLDER_RE.match(s.folder.name)["stamp"], reverse=True)
    return out


def participant_exists(sessions_root: Path, pid: str) -> bool:
    root = Path(sessions_root)
    return root.is_dir() and any(d.is_dir() and d.name.startswith(f"{pid}_") for d in root.iterdir())


# --- legacy adapters (sessions written before run numbering existed) -------------------------------
def _upgrade_summary(d: dict) -> dict:
    if "run" not in d:
        d = dict(d)
        d["run"] = d.get("repetition") or 1
        if d.get("is_trial"):
            d["repetition"] = None
    return d


def _upgrade_run_field(d: dict) -> dict:
    if "run" not in d and "repetition" in d:
        d = dict(d)
        d["run"] = d.pop("repetition")
    return d


@dataclass
class ArchivedSession:
    """Read-only view of a stored session, shaped like the live controller for the cockpit."""
    dir: Path
    meta: dict[str, Any]
    history: SummaryHistory = field(default_factory=SummaryHistory)
    llm_calls: list[LLMCallRecord] = field(default_factory=list)
    decisions: list[DecisionRecord] = field(default_factory=list)
    observations: list[ObservationRecord] = field(default_factory=list)
    calibrations: list[CalibrationRecord] = field(default_factory=list)
    verifications: list[VerificationRecord] = field(default_factory=list)   # last line per check id
    versions: list[InstructionSet] = field(default_factory=list)
    survey: Optional[SurveyRecord] = None
    problems: list[str] = field(default_factory=list)

    @property
    def participant_id(self) -> str:
        return str(self.meta.get("participant_id", self.dir.name))


def _parse_list(records: list[dict], model, upgrade=None, problems: Optional[list[str]] = None, name: str = ""):
    out = []
    for r in records:
        try:
            out.append(model.model_validate(upgrade(r) if upgrade else r))
        except ValidationError:
            if problems is not None:
                problems.append(f"{name}: skipped an unreadable record")
    return out


def load_session(folder: Path) -> ArchivedSession:
    folder = Path(folder)
    meta = _cached_json(folder / "session.json")
    if not isinstance(meta, dict):
        raise ValueError(f"{folder.name}: session.json missing or unreadable")
    s = ArchivedSession(dir=folder, meta=meta)
    summaries = _cached_json(folder / "summaries.json") or []
    for d in sorted((x for x in summaries if isinstance(x, dict)), key=lambda x: x.get("run", x.get("repetition", 0))):
        try:
            s.history.add(RunSummary.model_validate(_upgrade_summary(d)))
        except (ValidationError, ValueError):
            s.problems.append("summaries: skipped an unreadable run")
    s.llm_calls = _parse_list(read_jsonl(folder / "llm_calls.jsonl"), LLMCallRecord, _upgrade_run_field, s.problems, "llm_calls")
    s.decisions = _parse_list(read_jsonl(folder / "decisions.jsonl"), DecisionRecord, _upgrade_run_field, s.problems, "decisions")
    s.observations = _parse_list(read_jsonl(folder / "observations.jsonl"), ObservationRecord, None, s.problems, "observations")
    s.calibrations = _parse_list(read_jsonl(folder / "calibration.jsonl"), CalibrationRecord, None, s.problems, "calibration")
    checks = _parse_list(read_jsonl(folder / "verifications.jsonl"), VerificationRecord, None, s.problems,
                         "verifications")
    s.verifications = list({v.id: v for v in checks}.values())   # a review rewrites its check
    vdir = folder / "versions"
    if vdir.is_dir():
        for p in sorted(vdir.glob("v*.json")):
            data = _cached_json(p)
            try:
                s.versions.append(InstructionSet.model_validate(data))
            except (ValidationError, TypeError):
                s.problems.append(f"versions: {p.name} unreadable")
    survey = _cached_json(folder / "survey.json")
    if isinstance(survey, dict):
        try:
            s.survey = SurveyRecord.model_validate(survey)
        except ValidationError:
            s.problems.append("survey.json unreadable")
    return s
