"""Strict output schema of the camera check (vision model)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VerificationResult(BaseModel):
    """correct = the board matches the reference after this step; error = it does not;
    unclear = the photo does not allow a decision (blur, hands, glare, framing).
    confidence = how sure the model is of the verdict (0..1), shown as accuracy %."""
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["correct", "error", "unclear"]
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(max_length=400)
