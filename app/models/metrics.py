"""Preprocessing outputs: per-step metrics and per-run summaries.

Numbering: every run has a unique `run` number (1, 2, 3, ...). Regular runs also carry the
participant-facing `repetition` number; trial runs (first run with a newly generated version)
have `repetition = None` and `is_trial = True`, so trials never shift the repetition count.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.instruction import InstructionSettings


class StepMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_index: int = Field(ge=0)
    duration_s: float = Field(ge=0)
    duration_vs_expected_ratio: float = Field(ge=0)
    back_presses: int = Field(default=0, ge=0)
    observer_errors: int = Field(default=0, ge=0)     # steps the observer marked "error"
    observer_correct: int = Field(default=0, ge=0)    # steps the observer marked "correct"
    # --- gaze fields (stripped in the time_only condition) ---
    dwell_pct: dict[str, float] = Field(default_factory=dict)  # AOI -> percent, incl. off_screen/invalid
    first_aoi: Optional[str] = None
    transitions: list[str] = Field(default_factory=list)       # AOI visit sequence, duplicates collapsed
    revisit_count: int = Field(default=0, ge=0)
    n_valid_samples: int = Field(default=0, ge=0)


# StepMetrics fields removed before sending to the LLM in the time_only condition.
GAZE_METRIC_FIELDS: tuple[str, ...] = (
    "dwell_pct", "first_aoi", "transitions", "revisit_count", "n_valid_samples",
)


class RunTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_duration_s: float = Field(ge=0)
    total_expected_s: float = Field(ge=0)
    total_back_presses: int = Field(ge=0)
    total_observer_errors: int = Field(default=0, ge=0)


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: int = Field(ge=1)
    repetition: Optional[int] = Field(default=None, ge=1)   # None for trial runs
    is_trial: bool = False
    instruction_set_id: str
    version_level: int = Field(ge=0)
    settings: InstructionSettings
    steps: list[StepMetrics]
    totals: RunTotals

    @model_validator(mode="after")
    def _numbering(self) -> "RunSummary":
        if self.is_trial and self.repetition is not None:
            raise ValueError("a trial run has no repetition number")
        if not self.is_trial and self.repetition is None:
            raise ValueError("a regular run needs a repetition number")
        return self

    @property
    def label(self) -> str:
        return f"trial (run {self.run})" if self.is_trial else f"repetition {self.repetition}"
