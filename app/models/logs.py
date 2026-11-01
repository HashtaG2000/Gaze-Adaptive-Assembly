"""Session log records (one JSON object per line in the respective .jsonl file)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.llm import ValidationOutcome


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LLMCallRecord(_Record):
    """llm_calls.jsonl"""
    t: float
    wall_time: datetime
    call: Literal["analysis", "generation"]
    client: str
    attempt: int = 1
    prompt: str
    raw_response: Optional[str]
    validation: ValidationOutcome
    error: Optional[str] = None
    latency_s: Optional[float] = None
    run: Optional[int] = None


class DecisionRecord(_Record):
    """decisions.jsonl

    continue = feedback without layout change; request = participant asked for changes
    accept / reject = trial intro ("Start trial run" / "Keep my current layout")
    keep / revert = trial feedback
    """
    t: float
    wall_time: datetime
    run: int
    decision: Literal["continue", "request", "accept", "reject", "keep", "revert"]
    from_version: int
    to_version: Optional[int] = None
    detail: Optional[dict[str, Any]] = None


class ObservationRecord(_Record):
    """observations.jsonl - observer verification of a step (cockpit "Correct" / "Error")."""
    t: float
    wall_time: datetime
    run: int
    step_index: int
    verdict: Literal["correct", "error"]
    # observer = live button, camera = confident automatic check, review = observer corrected a check
    source: Literal["observer", "camera", "review"] = "observer"
    verification_id: Optional[int] = None


class CalibrationPoint(_Record):
    target: tuple[float, float]
    mean_gaze: Optional[tuple[float, float]] = None
    error_deg: Optional[float] = None
    n_samples: int = 0


class CalibrationRecord(_Record):
    """calibration.jsonl - one eye-tracker check (session start or drift check)."""
    t: float
    wall_time: datetime
    reason: Literal["start", "drift"]
    points: list[CalibrationPoint]
    mean_error_deg: Optional[float] = None
    max_error_deg: Optional[float] = None
    passed: bool
    decision: Optional[Literal["accepted", "repeated", "skipped"]] = None


class SurveyRecord(_Record):
    """survey.json - post-session questionnaire."""
    survey_id: str
    wall_time: datetime
    answers: dict[str, int]
    score: Optional[float] = None


class StateTransitionRecord(_Record):
    """Controller state transitions (logged to events.jsonl alongside step events)."""
    t: float
    wall_time: datetime
    kind: Literal["state"] = "state"
    from_state: str
    to_state: str
    detail: Optional[dict[str, Any]] = None


class SessionMeta(_Record):
    """session.json, written once at session start."""
    participant_id: str
    condition: Literal["gaze", "time_only"]
    gaze_source: Literal["gazepoint", "mouse", "mock"]
    llm_client: Literal["gemini", "ollama", "mock"]
    instruction_set_file: str
    questionnaire: dict[str, Any] = Field(default_factory=dict)
    gamification_at_start: bool = True
    calibration_at_start: bool = True
    app_version: str = ""
    started_at: datetime


class VerificationRecord(_Record):
    """verifications.jsonl - one automatic camera check (photo vs. reference image) of a step."""
    id: int
    t: float
    wall_time: datetime
    run: int
    step_index: int
    covers: list[int] = Field(default_factory=list)       # base steps of this step
    photo: str                                            # relative to the session folder
    reference: Optional[str] = None                       # relative to data/media
    client: str
    verdict: Optional[Literal["correct", "error", "unclear"]] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    reason: str = ""
    needs_review: bool = False
    raw_response: Optional[str] = None
    error: Optional[str] = None
    latency_s: Optional[float] = None
    reviewed: Optional[Literal["correct", "error"]] = None   # observer's correction (written as a new line)
