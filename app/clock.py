"""Single monotonic clock shared by gaze sources, player and logger.

All timestamps `t` in logs are seconds on this clock, so gaze samples and step events
are directly comparable. session.json stores the wall time at session start.
"""
import time

_ORIGIN = time.perf_counter()


def now() -> float:
    return time.perf_counter() - _ORIGIN
