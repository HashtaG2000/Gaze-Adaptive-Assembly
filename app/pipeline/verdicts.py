"""Final step verdict per (run, step), merged from three sources.

    review    the observer corrected / confirmed a camera check      (highest priority)
    observer  the observer's live "Correct" / "Error" button
    camera    a confident automatic camera check                     (lowest priority)

A source never overwrites a higher one; the same source overwrites itself (e.g. the step was
done again after "Back" and photographed again). Metrics use exactly one verdict per step.
"""
from __future__ import annotations

from typing import Optional

PRIORITY = {"camera": 1, "observer": 2, "review": 3}


class VerdictBook:
    def __init__(self) -> None:
        self._data: dict[tuple[int, int], tuple[str, str]] = {}   # (run, step) -> (verdict, source)

    def set(self, run: int, step: int, verdict: str, source: str) -> bool:
        """Store a verdict; returns True if the final verdict or its source changed."""
        if verdict not in ("correct", "error") or source not in PRIORITY:
            raise ValueError(f"invalid verdict {verdict!r} / source {source!r}")
        cur = self._data.get((run, step))
        if cur is not None and PRIORITY[cur[1]] > PRIORITY[source]:
            return False
        if cur == (verdict, source):
            return False
        self._data[(run, step)] = (verdict, source)
        return True

    def get(self, run: int, step: int) -> Optional[tuple[str, str]]:
        return self._data.get((run, step))

    def for_run(self, run: int) -> list[tuple[int, str]]:
        """(step_index, verdict) pairs of one run, as used by the step metrics."""
        return sorted((s, v) for (r, s), (v, _) in self._data.items() if r == run)
