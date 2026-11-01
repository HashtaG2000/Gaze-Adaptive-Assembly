"""Authorable content stored as JSON: media catalog, LLM rules, surveys."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.instruction import Media, clean_text

ID_RE = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MediaItem(_Strict):
    id: str = Field(pattern=ID_RE)
    type: Literal["image", "video"]
    path: str
    role: Literal["part", "position", "method"]
    description: str = Field(default="", max_length=400)   # given to the LLM
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("path")
    @classmethod
    def _safe(cls, v: str) -> str:
        return Media(type="image", path=v, role="part").path   # reuse the path validation

    @field_validator("description")
    @classmethod
    def _clean(cls, v: str) -> str:
        return clean_text(v)

    def as_media(self) -> Media:
        return Media(type=self.type, path=self.path, role=self.role)


class MediaCatalog(_Strict):
    version: int = 1
    items: list[MediaItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "MediaCatalog":
        ids = [i.id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("media ids must be unique")
        return self

    def by_path(self) -> dict[str, MediaItem]:
        return {i.path: i for i in self.items}


class Rule(_Strict):
    id: str = Field(pattern=ID_RE)
    category: str = Field(default="General", max_length=40)
    text: str = Field(min_length=1, max_length=2000)
    enabled: bool = True

    @field_validator("text", "category")
    @classmethod
    def _clean(cls, v: str) -> str:
        return clean_text(v)


class RuleSet(_Strict):
    version: int = 2
    draft: bool = True
    note: str = Field(default="", max_length=1000)
    rules: list[Rule] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def _unique(self) -> "RuleSet":
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule ids must be unique")
        return self

    def render(self) -> str:
        """Enabled rules as text for the LLM prompt."""
        lines = [f"{i}. [{r.category}] {r.text}" for i, r in enumerate((r for r in self.rules if r.enabled), 1)]
        return "\n".join(lines) if lines else "(no rules enabled)"


class SurveyScale(_Strict):
    min: int = 1
    max: int = 5
    labels: dict[str, str] = Field(default_factory=dict)


class SurveyItem(_Strict):
    id: str = Field(pattern=ID_RE)
    text: str = Field(min_length=1, max_length=400)


class Survey(_Strict):
    id: str = Field(pattern=ID_RE)
    title: str
    intro: str = ""
    scale: SurveyScale = Field(default_factory=SurveyScale)
    items_per_page: int = Field(default=5, ge=1, le=20)
    scoring: Optional[Literal["sus", "mean"]] = None
    items: list[SurveyItem] = Field(min_length=1, max_length=100)

    def score(self, answers: dict[str, int]) -> Optional[float]:
        values = [answers.get(i.id) for i in self.items]
        if any(v is None for v in values):
            return None
        if self.scoring == "sus":
            if len(values) != 10 or (self.scale.min, self.scale.max) != (1, 5):
                return None
            # odd items (1,3,5,...): v-1 ; even items: 5-v ; sum * 2.5  -> 0..100
            total = sum((v - 1) if i % 2 == 0 else (5 - v) for i, v in enumerate(values))
            return round(total * 2.5, 1)
        if self.scoring == "mean":
            return round(sum(values) / len(values), 2)
        return None
