"""Instruction set, step and media models."""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.files import is_safe_rel_path
from app.models.aoi import CONTENT_ELEMENTS, HideableElement

StepType = Literal["pick", "place", "pick_place", "pick_place_multi"]
MediaType = Literal["image", "video"]
MediaRole = Literal["part", "position", "method"]

SLUG_RE = r"^[a-z0-9][a-z0-9_-]{0,63}$"
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MAX_STEP_TEXT = 600


def clean_text(v: str) -> str:
    """Strip control characters (except tab/newline) and surrounding whitespace."""
    return _CONTROL_RE.sub("", v).strip()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Media(_Strict):
    type: MediaType
    path: str  # relative to data/media, never absolute, never ".."
    role: MediaRole

    @field_validator("path")
    @classmethod
    def _safe_path(cls, v: str) -> str:
        v = v.replace("\\", "/")
        if not is_safe_rel_path(v):
            raise ValueError(f"unsafe media path {v!r} (must be relative inside the media folder)")
        return v


class Step(_Strict):
    index: int = Field(ge=0)  # 0-based; UI shows index + 1
    type: StepType
    text: str = Field(min_length=1, max_length=MAX_STEP_TEXT)  # participant-facing instruction text
    media: list[Media] = Field(default_factory=list, max_length=12)
    interaction: str = Field(default="button", max_length=40)
    expected_time_s: float = Field(gt=0, le=3600)  # MTM-based reference, soft only
    part_id: str = Field(min_length=1, max_length=64)
    quantity: int = Field(default=1, ge=1, le=100)
    # Target position(s) of placed parts. Empty for pure "pick" steps.
    position_ids: list[str] = Field(default_factory=list)
    # Base-set step indices this step combines (adapted versions). Empty in a base set (= [index]).
    covers: list[int] = Field(default_factory=list)
    # Photo of the correct board state AFTER this step (camera check). Relative to data/media.
    reference_image: Optional[str] = None

    @field_validator("reference_image")
    @classmethod
    def _safe_reference(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        return Media(type="image", path=v, role="position").path

    @field_validator("text")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = clean_text(v)
        if not v:
            raise ValueError("step text must not be empty")
        return v

    @model_validator(mode="after")
    def _check_type_consistency(self) -> "Step":
        if self.type == "pick" and self.position_ids:
            raise ValueError(f"step {self.index}: pick step must not have position_ids")
        if self.type != "pick" and not self.position_ids:
            raise ValueError(f"step {self.index}: {self.type} step needs position_ids")
        if self.type == "pick_place_multi" and self.quantity < 2:
            raise ValueError(f"step {self.index}: pick_place_multi needs quantity >= 2")
        if self.covers and self.covers != list(range(self.covers[0], self.covers[0] + len(self.covers))):
            raise ValueError(f"step {self.index}: covers must be consecutive base steps")
        return self

    def media_of(self, mtype: MediaType) -> list[Media]:
        return [m for m in self.media if m.type == mtype]

    def covered(self) -> list[int]:
        return self.covers or [self.index]


class InstructionSettings(_Strict):
    merge_level: Literal[0, 1, 2] = 0
    hidden_elements: list[HideableElement] = Field(default_factory=list)

    @field_validator("hidden_elements")
    @classmethod
    def _unique_and_not_all_media(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("hidden_elements contains duplicates")
        if set(CONTENT_ELEMENTS) <= set(v):
            raise ValueError("cannot hide text, image and video at once (nothing would be left)")
        return sorted(v)


class InstructionSet(_Strict):
    id: str = Field(pattern=SLUG_RE)
    title: str = Field(min_length=1, max_length=120)
    version_level: int = Field(default=0, ge=0)  # 0 = most extensive base version
    settings: InstructionSettings = Field(default_factory=InstructionSettings)
    steps: list[Step] = Field(min_length=1, max_length=500)

    @field_validator("title")
    @classmethod
    def _clean_title(cls, v: str) -> str:
        v = clean_text(v)
        if not v:
            raise ValueError("title must not be empty")
        return v

    @model_validator(mode="after")
    def _check_indices(self) -> "InstructionSet":
        idx = [s.index for s in self.steps]
        if idx != list(range(len(self.steps))):
            raise ValueError(f"step indices must be 0..{len(self.steps) - 1} in order, got {idx}")
        return self

    @property
    def n_steps(self) -> int:
        return len(self.steps)

    def is_base(self) -> bool:
        return self.version_level == 0 and self.settings.merge_level == 0 and not any(s.covers for s in self.steps)


def base_structure_errors(iset: InstructionSet) -> list[str]:
    """A base set must alternate pick / place of the same part so the LLM can merge it."""
    errors = []
    steps = iset.steps
    if len(steps) % 2:
        errors.append("a base set needs an even number of steps (pick, place, pick, place, ...)")
    for i in range(0, len(steps) - 1, 2):
        pick, place = steps[i], steps[i + 1]
        if pick.type != "pick":
            errors.append(f"step {i + 1} must be a 'pick' step")
        if place.type != "place":
            errors.append(f"step {i + 2} must be a 'place' step")
        if pick.part_id != place.part_id:
            errors.append(f"steps {i + 1}/{i + 2}: pick and place must use the same part")
    positions = [p for s in steps for p in s.position_ids]
    if len(positions) != len(set(positions)):
        errors.append("every position id may be used only once")
    return errors
