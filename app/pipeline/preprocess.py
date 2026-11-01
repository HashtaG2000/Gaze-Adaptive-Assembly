"""Per-step metrics from gaze samples and step events (code only, no LLM).

Definitions
-----------
Step intervals   A step is "open" from its `start` event until the next `end` or `back`
                 event. After "Back" a step can be open several times; all its intervals
                 count. A sample belongs to a step if start <= t < end.
duration_s       Sum of the step's interval lengths.
back_presses     Number of "Back" presses made ON this step.
dwell_pct        Share of the step's samples (all samples, valid or not) per AOI, in percent,
                 for every AOI in ALL_AOIS (elements + off_screen, other, invalid). Sums to 100.
                 Count-based, i.e. assumes a constant sample rate.
transitions      Time-ordered AOI visit sequence with consecutive duplicates collapsed.
                 Built from valid samples only; "other" (between AOIs) and "invalid" samples
                 are treated as gaps, not visits. Runs shorter than min_visit_ms are dropped
                 before collapsing.
first_aoi        First entry of `transitions` (None if there is none).
revisit_count    Visits to an AOI that had already been visited earlier in the same step
                 (= len(transitions) - number of distinct AOIs in it).
n_valid_samples  Samples in the step that are valid (includes off_screen and other).
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

import numpy as np

from app.models import ALL_AOIS, GazeSample, InstructionSet, StepEvent, StepMetrics
from app.pipeline.aoi import AoiMap

NON_VISIT_AOIS = frozenset({"other", "invalid"})


class PreprocessError(ValueError):
    pass


def step_intervals(events: list[StepEvent], n_steps: int) -> dict[int, list[tuple[float, float]]]:
    """Open/close intervals per step, from events in logged order."""
    intervals: dict[int, list[tuple[float, float]]] = {i: [] for i in range(n_steps)}
    open_step: int | None = None
    open_t = 0.0
    for e in events:
        if not 0 <= e.step_index < n_steps:
            raise PreprocessError(f"event for unknown step {e.step_index}")
        if e.event == "start":
            if open_step is not None:
                raise PreprocessError(f"step {e.step_index} started while step {open_step} still open")
            open_step, open_t = e.step_index, e.t
        else:  # end / back
            if open_step != e.step_index:
                raise PreprocessError(f"'{e.event}' for step {e.step_index} but open step is {open_step}")
            if e.t < open_t:
                raise PreprocessError(f"step {e.step_index}: event time goes backwards")
            intervals[e.step_index].append((open_t, e.t))
            open_step = None
    if open_step is not None:
        raise PreprocessError(f"step {open_step} was never closed")
    missing = [i for i, iv in intervals.items() if not iv]
    if missing:
        raise PreprocessError(f"steps never completed: {missing}")
    return intervals


def visit_sequence(t: np.ndarray, labels: np.ndarray, min_visit_s: float = 0.0) -> list[str]:
    """Collapsed AOI visit sequence (see module docstring)."""
    keep = np.array([lab not in NON_VISIT_AOIS for lab in labels], dtype=bool)
    t, labels = t[keep], labels[keep]
    if len(labels) == 0:
        return []
    # runs of identical consecutive labels
    change = np.flatnonzero(labels[1:] != labels[:-1]) + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change, [len(labels)]))  # exclusive
    # A run's duration spans its samples plus one sample interval (the last sample's share).
    dt = float(np.median(np.diff(t))) if len(t) > 1 else 0.0
    runs = []
    for a, b in zip(starts, ends):
        dur = t[b - 1] - t[a] + dt
        if min_visit_s <= 0 or dur >= min_visit_s - 1e-9:  # tolerance for float sample times
            runs.append(labels[a])
    seq: list[str] = []
    for lab in runs:
        if not seq or seq[-1] != lab:
            seq.append(str(lab))
    return seq


def compute_step_metrics(
    iset: InstructionSet,
    samples: list[GazeSample],
    events: list[StepEvent],
    aoi_map: AoiMap,
    min_visit_ms: int = 0,
    observations: Optional[list[tuple[int, str]]] = None,
) -> list[StepMetrics]:
    """Metrics for every step of ONE run. observations: (step_index, "correct"|"error") verdicts."""
    intervals = step_intervals(events, iset.n_steps)
    backs = Counter(e.step_index for e in events if e.event == "back")
    final = dict(observations or [])          # one final verdict per step (last one wins)
    obs_err = Counter(i for i, v in final.items() if v == "error")
    obs_ok = Counter(i for i, v in final.items() if v == "correct")

    if samples:
        t = np.array([s.t for s in samples], dtype=float)
        x = np.array([np.nan if s.x_norm is None else s.x_norm for s in samples], dtype=float)
        y = np.array([np.nan if s.y_norm is None else s.y_norm for s in samples], dtype=float)
        valid = np.array([s.valid for s in samples], dtype=bool)
        order = np.argsort(t, kind="stable")
        t, x, y, valid = t[order], x[order], y[order], valid[order]
        labels = aoi_map.map_arrays(x, y, valid)
    else:
        t = np.empty(0); labels = np.empty(0, dtype=object)

    out: list[StepMetrics] = []
    for step in iset.steps:
        ivs = intervals[step.index]
        duration = float(sum(b - a for a, b in ivs))
        mask = np.zeros(len(t), dtype=bool)
        for a, b in ivs:
            mask |= (t >= a) & (t < b)
        st, sl = t[mask], labels[mask]
        n = len(sl)
        counts = Counter(sl.tolist())
        dwell = {aoi: (round(100.0 * counts.get(aoi, 0) / n, 2) if n else 0.0) for aoi in ALL_AOIS}
        seq = visit_sequence(st, sl, min_visit_ms / 1000.0)
        out.append(StepMetrics(
            step_index=step.index,
            duration_s=round(duration, 3),
            duration_vs_expected_ratio=round(duration / step.expected_time_s, 3),
            back_presses=backs.get(step.index, 0),
            observer_errors=obs_err.get(step.index, 0),
            observer_correct=obs_ok.get(step.index, 0),
            dwell_pct=dwell,
            first_aoi=seq[0] if seq else None,
            transitions=seq,
            revisit_count=len(seq) - len(set(seq)),
            n_valid_samples=int(n - counts.get("invalid", 0)),
        ))
    return out
