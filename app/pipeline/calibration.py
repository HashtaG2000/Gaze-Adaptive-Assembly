"""Eye-tracker check: angular error between gaze and known targets.

This VALIDATES the tracker's calibration (done in Gazepoint Control); it does not calibrate.
Per target the median of the valid gaze samples in the sampling window is compared with the
target; the distance on the screen (cm) is converted to visual angle using the viewing distance.
"""
from __future__ import annotations

import math
from statistics import median
from typing import Optional

from app.config import CalibrationConfig, ScreenConfig
from app.models import CalibrationPoint


def angular_error_deg(dx_norm: float, dy_norm: float, cal: CalibrationConfig, screen: ScreenConfig) -> float:
    width_cm = cal.screen_width_cm
    height_cm = width_cm * screen.height / screen.width
    dist_cm = math.hypot(dx_norm * width_cm, dy_norm * height_cm)
    return math.degrees(math.atan2(dist_cm, cal.viewing_distance_cm))


def evaluate_point(target: tuple[float, float], samples: list[tuple[float, float]],
                   cal: CalibrationConfig, screen: ScreenConfig) -> CalibrationPoint:
    pts = [(x, y) for x, y in samples if x is not None and y is not None and math.isfinite(x) and math.isfinite(y)]
    if len(pts) < cal.min_samples:
        return CalibrationPoint(target=target, n_samples=len(pts))
    mx, my = median(p[0] for p in pts), median(p[1] for p in pts)
    err = angular_error_deg(mx - target[0], my - target[1], cal, screen)
    return CalibrationPoint(target=target, mean_gaze=(round(mx, 4), round(my, 4)), error_deg=round(err, 3),
                            n_samples=len(pts))


def summarise(points: list[CalibrationPoint], cal: CalibrationConfig) -> tuple[Optional[float], Optional[float], bool]:
    """(mean error, max error, passed). Every point needs data to pass."""
    errs = [p.error_deg for p in points if p.error_deg is not None]
    if not errs or len(errs) != len(points):
        return (round(sum(errs) / len(errs), 3) if errs else None, max(errs) if errs else None, False)
    mean = round(sum(errs) / len(errs), 3)
    return mean, round(max(errs), 3), mean <= cal.max_error_deg
