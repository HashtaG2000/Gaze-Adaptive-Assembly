"""Background queue for camera checks: one worker thread, jobs processed in order.

Sequential on purpose: steps finish every few seconds, vision calls take 1-5 s, and a single
worker never exceeds API rate limits or overloads a local model. The participant never waits;
results arrive via finished(VerificationRecord) in the GUI thread.
"""
from __future__ import annotations

import queue
import time
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from app.llm.prompts import PromptTemplates
from app.vision.clients import VisionClient
from app.vision.verify import CheckJob, verification_job

_STOP = object()


class _Worker(QThread):
    done = Signal(object)

    def __init__(self, service: "VerificationService") -> None:
        super().__init__()
        self.service = service

    def run(self) -> None:
        s = self.service
        while True:
            job = s._queue.get()
            if job is _STOP:
                return
            try:
                rec = verification_job(s.client, s.templates, job, s.threshold, s.max_px)
            except Exception as exc:  # noqa: BLE001 - last line of defence, the worker must survive
                rec = None
                s.crashed.emit(job.id, f"{exc.__class__.__name__}: {exc}")
            self.done.emit(rec)        # also None: the job counts as finished


class VerificationService(QObject):
    finished = Signal(object)      # VerificationRecord
    crashed = Signal(int, str)     # job id, message

    def __init__(self, client: Optional[VisionClient], templates: PromptTemplates, threshold: float,
                 max_px: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.client, self.templates, self.threshold, self.max_px = client, templates, threshold, max_px
        self._queue: "queue.Queue[object]" = queue.Queue()
        self._submitted = 0
        self._done = 0
        self._worker = _Worker(self)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    @property
    def pending(self) -> int:
        """Jobs submitted but not finished yet."""
        return self._submitted - self._done

    def submit(self, job: CheckJob) -> None:
        self._submitted += 1
        self._queue.put(job)

    def _on_done(self, rec) -> None:
        self._done += 1
        if rec is not None:
            self.finished.emit(rec)

    def wait_idle(self, timeout_s: float) -> bool:
        """Block until all jobs are done (used only at application exit)."""
        from PySide6.QtCore import QCoreApplication
        deadline = time.monotonic() + timeout_s
        while self.pending > 0 and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.02)
        return self.pending == 0

    def shutdown(self, timeout_ms: int = 3000) -> None:
        self._queue.put(_STOP)
        self._worker.wait(timeout_ms)
