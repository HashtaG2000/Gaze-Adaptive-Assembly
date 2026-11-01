"""RunSummary construction, run history, and condition-specific views for the LLM."""
from __future__ import annotations

from typing import Any, Optional

from app.models import (
    GAZE_METRIC_FIELDS, GazeSample, InstructionSet, RunSummary, RunTotals, StepEvent,
)
from app.pipeline.aoi import AoiMap
from app.pipeline.preprocess import compute_step_metrics


def build_run_summary(
    run: int,
    repetition: Optional[int],
    iset: InstructionSet,
    samples: list[GazeSample],
    events: list[StepEvent],
    aoi_map: AoiMap,
    min_visit_ms: int = 0,
    observations: Optional[list[tuple[int, str]]] = None,
) -> RunSummary:
    """repetition=None marks a trial run."""
    steps = compute_step_metrics(iset, samples, events, aoi_map, min_visit_ms, observations)
    totals = RunTotals(
        total_duration_s=round(sum(s.duration_s for s in steps), 3),
        total_expected_s=round(sum(s.expected_time_s for s in iset.steps), 3),
        total_back_presses=sum(s.back_presses for s in steps),
        total_observer_errors=sum(s.observer_errors for s in steps),
    )
    return RunSummary(
        run=run, repetition=repetition, is_trial=repetition is None, instruction_set_id=iset.id,
        version_level=iset.version_level, settings=iset.settings, steps=steps, totals=totals,
    )


def apply_verdicts(summary: RunSummary, verdicts: list[tuple[int, str]]) -> RunSummary:
    """Recompute the per-step verification counts (one final verdict per step) and the totals."""
    final = dict(verdicts)
    steps = [m.model_copy(update={"observer_errors": int(final.get(m.step_index) == "error"),
                                  "observer_correct": int(final.get(m.step_index) == "correct")})
             for m in summary.steps]
    totals = summary.totals.model_copy(update={"total_observer_errors": sum(m.observer_errors for m in steps)})
    return summary.model_copy(update={"steps": steps, "totals": totals})


def summary_for_condition(summary: RunSummary, condition: str) -> dict[str, Any]:
    """Plain dict as sent to the LLM. In `time_only` every gaze field is removed."""
    data = summary.model_dump(mode="json")
    if condition == "time_only":
        for step in data["steps"]:
            for f in GAZE_METRIC_FIELDS:
                step.pop(f, None)
    elif condition != "gaze":
        raise ValueError(f"unknown condition {condition!r}")
    return data


class SummaryHistory:
    """All RunSummaries of a session, in run order (index by run number is O(1))."""

    def __init__(self) -> None:
        self._runs: list[RunSummary] = []
        self._by_run: dict[int, RunSummary] = {}

    def add(self, s: RunSummary) -> None:
        if self._runs and s.run <= self._runs[-1].run:
            raise ValueError("runs must be added in increasing order")
        self._runs.append(s)
        self._by_run[s.run] = s

    def replace(self, s: RunSummary) -> None:
        """Swap in an updated summary of an existing run (e.g. a late camera verdict)."""
        if s.run not in self._by_run:
            raise KeyError(f"no run {s.run}")
        self._runs = [s if r.run == s.run else r for r in self._runs]
        self._by_run[s.run] = s

    def all(self) -> list[RunSummary]:
        return list(self._runs)

    def last(self, k: int) -> list[RunSummary]:
        return self._runs[-k:] if k > 0 else []

    def get(self, run: int) -> Optional[RunSummary]:
        return self._by_run.get(run)

    def regular(self) -> list[RunSummary]:
        return [s for s in self._runs if not s.is_trial]

    def __len__(self) -> int:
        return len(self._runs)
