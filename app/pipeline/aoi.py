"""AOI rectangles and point -> AOI mapping.

AOI rectangles come from config.yaml in pixels on the configured canvas (1920x1080) and are
converted to normalised screen coordinates (0..1), the same space the gaze samples use.

Mapping rules (in this order):
    sample.valid is False, or x/y missing/NaN      -> "invalid"
    x or y outside [0, 1]                          -> "off_screen"
    inside an element rectangle                    -> element name, e.g. "text"
    on screen but in no rectangle                  -> "other"

Rectangles are half-open: left/top edges belong to the AOI, right/bottom edges do not, so a
point on a shared border is never counted twice. Screen bounds are closed: x = 1.0 is on screen.
Mapping is purely geometric: a hidden element's rectangle still maps to that element's name
(the RunSummary's settings record which elements were hidden).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from app.config import AppConfig
from app.models.aoi import ELEMENT_AOIS


@dataclass(frozen=True)
class NormRect:
    name: str
    x0: float
    y0: float
    x1: float
    y1: float

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x < self.x1 and self.y0 <= y < self.y1


class AoiMap:
    def __init__(self, rects: list[NormRect]) -> None:
        self.rects = rects

    @classmethod
    def from_config(cls, cfg: AppConfig) -> "AoiMap":
        W, H = cfg.screen.width, cfg.screen.height
        rects = []
        for name in ELEMENT_AOIS:
            r = cfg.aoi[name]
            rects.append(NormRect(name, r.x / W, r.y / H, (r.x + r.w) / W, (r.y + r.h) / H))
        return cls(rects)

    def map_point(self, x: Optional[float], y: Optional[float], valid: bool = True) -> str:
        if not valid or x is None or y is None or math.isnan(x) or math.isnan(y):
            return "invalid"
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            return "off_screen"
        for r in self.rects:
            if r.contains(x, y):
                return r.name
        return "other"

    def map_arrays(self, x: np.ndarray, y: np.ndarray, valid: np.ndarray) -> np.ndarray:
        """Vectorised map_point. x/y may contain NaN for missing values."""
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        valid = np.asarray(valid, dtype=bool)
        out = np.full(x.shape, "other", dtype=object)
        on_screen = (x >= 0) & (x <= 1) & (y >= 0) & (y <= 1)
        for r in self.rects:
            m = on_screen & (x >= r.x0) & (x < r.x1) & (y >= r.y0) & (y < r.y1)
            out[m] = r.name
        out[~on_screen] = "off_screen"           # NaN comparisons are False -> off_screen ...
        invalid = ~valid | np.isnan(x) | np.isnan(y)
        out[invalid] = "invalid"                 # ... then overridden here
        return out
