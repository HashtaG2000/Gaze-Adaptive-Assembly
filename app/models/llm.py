"""Participant feedback request and LLM input/output models."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.aoi import HideableElement

Direction = Literal["compress", "keep", "detail"]


class ParticipantRequest(BaseModel):
    """What the participant answered on the feedback screens (P4-A / P4-B) after a repetition.

    difficulty: rating of the layout (options from config.feedback.difficulty_options)
    direction:  compress = fewer, combined steps; keep = same steps; detail = more, smaller steps
    hide / show: elements to remove / to show again. Applied exactly as requested.
    """
    model_config = ConfigDict(extra="forbid")

    difficulty: Optional[str] = Field(default=None, max_length=40)
    direction: Direction
    hide: list[HideableElement] = Field(default_factory=list)
    show: list[HideableElement] = Field(default_factory=list)

    @model_validator(mode="after")
    def _disjoint(self) -> "ParticipantRequest":
        if set(self.hide) & set(self.show):
            raise ValueError("an element cannot be hidden and shown at the same time")
        self.hide, self.show = sorted(set(self.hide)), sorted(set(self.show))
        return self

    @property
    def is_empty(self) -> bool:
        """No layout change requested (the difficulty rating alone changes nothing)."""
        return self.direction == "keep" and not self.hide and not self.show


class AnalysisResult(BaseModel):
    """Strict output schema of LLM call 1 (plan for the participant's request).

    The participant decides the direction and the elements; the LLM decides how far to go
    (target_merge_level, an upper bound), which base steps stay detailed, and explains it.
    """
    model_config = ConfigDict(extra="forbid")

    progress_assessment: str = Field(max_length=2000)
    plateau: bool
    target_merge_level: Literal[0, 1, 2]
    keep_detailed_steps: list[int] = Field(default_factory=list, max_length=500)
    participant_message: str = Field(max_length=600)
    researcher_reasoning: str = Field(max_length=4000)


class GeneratedStep(BaseModel):
    """One step of an LLM-generated version: which base steps it combines, its text, its media."""
    model_config = ConfigDict(extra="forbid")

    covers: list[int] = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=2000)
    media: list[str] = Field(default_factory=list, max_length=8)   # media paths taken from the base steps


class GeneratedVersion(BaseModel):
    """Strict output schema of LLM call 2 (generation). Structural fields are derived by the app."""
    model_config = ConfigDict(extra="forbid")

    steps: list[GeneratedStep] = Field(min_length=1, max_length=500)


class ValidationOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)  # accepted but noteworthy (e.g. normalised output)
