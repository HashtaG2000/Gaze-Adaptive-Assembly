"""Raw gaze samples and step events (what gets logged during play)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class GazeSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t: float                     # session clock, seconds (monotonic)
    x_norm: Optional[float]      # 0..1 relative to screen width; outside range = off-screen
    y_norm: Optional[float]      # 0..1 relative to screen height
    valid: bool
    fixation_id: Optional[int] = None
    tracker_time: Optional[float] = None  # tracker's own timestamp, if provided


class StepEvent(BaseModel):
    """run = sequence number of the run in the session (regular repetitions AND trial runs)."""
    model_config = ConfigDict(extra="forbid")

    kind: Literal["step"] = "step"  # distinguishes step events from state records in events.jsonl
    t: float
    run: int = Field(ge=1)
    step_index: int = Field(ge=0)
    event: Literal["start", "end", "back"]
