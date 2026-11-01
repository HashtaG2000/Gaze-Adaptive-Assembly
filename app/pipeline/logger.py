"""Session logging: one folder per participant session, append-only JSONL files.

sessions/<participant_id>_<YYYYmmdd_HHMMSS>/
    session.json        session metadata (participant, intake answers, condition, sources, start)
    config.yaml         copy of the config used (AOI rectangles etc.)
    gaze.jsonl          every GazeSample
    events.jsonl        step events (kind=step) and controller state transitions (kind=state)
    decisions.jsonl     participant decisions
    observations.jsonl  observer verification per step (correct / error)
    calibration.jsonl   eye-tracker checks (start and drift checks)
    verifications.jsonl camera checks (a reviewed check is written again with `reviewed` set)
    photos/             board photos taken after every step
    llm_calls.jsonl     every LLM call: full prompt, raw response, validation
    summaries.json      all RunSummaries (rewritten after every run)
    survey.json         post-session questionnaire
    versions/           every instruction set version used
"""
from __future__ import annotations

import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from app import clock
from app.core.files import write_json_atomic, write_text_atomic
from app.models import (
    CalibrationRecord, DecisionRecord, GazeSample, InstructionSet, LLMCallRecord, ObservationRecord,
    RunSummary, SessionMeta, StateTransitionRecord, StepEvent, SurveyRecord, VerificationRecord,
)

_JSONL_FILES = ("gaze.jsonl", "events.jsonl", "decisions.jsonl", "llm_calls.jsonl", "observations.jsonl",
                "calibration.jsonl", "verifications.jsonl")
PARTICIPANT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
FLUSH_INTERVAL_S = 1.0


def wall_now() -> datetime:
    return datetime.now().astimezone()


class SessionLogger:
    def __init__(self, sessions_root: Path, meta: SessionMeta, config_path: Optional[Path] = None) -> None:
        if not PARTICIPANT_ID_RE.match(meta.participant_id):
            raise ValueError("participant_id may only contain letters, digits, '_' and '-'")
        stamp = meta.started_at.strftime("%Y%m%d_%H%M%S")
        self.dir = Path(sessions_root) / f"{meta.participant_id}_{stamp}"
        self.dir.mkdir(parents=True, exist_ok=False)
        (self.dir / "versions").mkdir()
        self.photos_dir = self.dir / "photos"
        write_text_atomic(self.dir / "session.json", meta.model_dump_json(indent=2))
        if config_path is not None and Path(config_path).is_file():
            shutil.copyfile(config_path, self.dir / "config.yaml")
        self._files = {n: open(self.dir / n, "a", encoding="utf-8", buffering=1 << 16) for n in _JSONL_FILES}
        self._last_flush = time.monotonic()
        self._closed = False

    # --- writers --------------------------------------------------------------------
    def log_gaze(self, s: GazeSample) -> None:
        self._write("gaze.jsonl", s)

    def log_step_event(self, e: StepEvent) -> None:
        self._write("events.jsonl", e)

    def log_state(self, from_state: str, to_state: str, detail: Optional[dict[str, Any]] = None) -> None:
        rec = StateTransitionRecord(t=clock.now(), wall_time=wall_now(),
                                    from_state=from_state, to_state=to_state, detail=detail)
        self._write("events.jsonl", rec, flush=True)  # state changes are rare and important

    def log_decision(self, rec: DecisionRecord) -> None:
        self._write("decisions.jsonl", rec, flush=True)

    def log_llm_call(self, rec: LLMCallRecord) -> None:
        self._write("llm_calls.jsonl", rec, flush=True)

    def log_observation(self, rec: ObservationRecord) -> None:
        self._write("observations.jsonl", rec, flush=True)

    def log_verification(self, rec: VerificationRecord) -> None:
        self._write("verifications.jsonl", rec, flush=True)

    def log_calibration(self, rec: CalibrationRecord) -> None:
        self._write("calibration.jsonl", rec, flush=True)

    def write_summaries(self, summaries: list[RunSummary]) -> None:
        """Rewrite summaries.json with the full run history (atomic replace)."""
        if self._closed:
            return
        write_json_atomic(self.dir / "summaries.json", [s.model_dump(mode="json") for s in summaries])

    def write_survey(self, rec: SurveyRecord) -> None:
        write_text_atomic(self.dir / "survey.json", rec.model_dump_json(indent=2))

    def save_version(self, iset: InstructionSet) -> Path:
        """Save an instruction set version once (versions/v<level>.json)."""
        path = self.dir / "versions" / f"v{iset.version_level:02d}.json"
        if not path.exists():
            write_text_atomic(path, iset.model_dump_json(indent=2))
        return path

    # --- housekeeping -----------------------------------------------------------------
    @property
    def closed(self) -> bool:
        return self._closed

    def flush(self) -> None:
        if self._closed:
            return
        for f in self._files.values():
            f.flush()
        self._last_flush = time.monotonic()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for f in self._files.values():
            try:
                f.close()
            except OSError:
                pass

    def _write(self, name: str, model: BaseModel, flush: bool = False) -> None:
        if self._closed:
            return
        self._files[name].write(model.model_dump_json() + "\n")
        if flush or time.monotonic() - self._last_flush > FLUSH_INTERVAL_S:
            self.flush()


def json_dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
