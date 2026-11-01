"""Session loop state machine.

    SETUP (intake P0) -> CALIBRATION (P1, experimenter accepts) -> PLAYING (repetition N)
      -> SAVED (P3) -> FEEDBACK (P4-A difficulty + compaction, P4-B elements)
           no change requested            -> PLAYING (repetition N+1)
           change requested -> ANALYSING (LLM call 1: plan) -> GENERATING (LLM call 2: new steps)
             -> TRIAL_INTRO (new layout explained) -- keep current -> PLAYING
                                                   -- start trial  -> TRIAL (labelled "Trial run")
             -> TRIAL_FEEDBACK -- keep -> PLAYING with new version | revert -> PLAYING previous version
    End (researcher, or max repetitions): SURVEY (post questionnaire) -> COMPLETE -> ENDED

Numbering: `run` counts every run (regular and trial); `repetition` counts regular runs only,
so a trial run never shifts the repetition shown to the participant.
Drift check (cockpit): runs the eye-tracker check before the next run starts.
Observer verification (cockpit): correct / error per step; feeds the streak and the metrics.
LLM calls run in a QThread while the waiting screen is shown. Every transition, decision,
observation, check and LLM call is logged.
"""
from __future__ import annotations

import itertools
import logging
import time
import traceback
from enum import Enum
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from app import clock
from app.config import DEFAULT_CONFIG_PATH, AppConfig
from app.core.repository import ContentRepository
from app.gaze.source import GazeSource
from app.i18n import t
from app.llm.client import LLMClient, LLMError
from app.llm.prompts import PromptTemplates, build_analysis_prompt, build_generation_prompt
from app.llm.validate import (
    check_request, fallback_plan, structure_key, target_settings, validate_analysis, validate_generation,
)
from app.models import (
    AnalysisResult, CalibrationRecord, DecisionRecord, GazeSample, InstructionSet, InstructionSettings,
    LLMCallRecord, ObservationRecord, ParticipantRequest, RunSummary, SessionMeta, StepEvent, SurveyRecord,
    ValidationOutcome, VerificationRecord,
)
from app.pipeline.aoi import AoiMap
from app.pipeline.calibration import evaluate_point, summarise
from app.pipeline.logger import SessionLogger, wall_now
from app.pipeline.preprocess import PreprocessError
from app.pipeline.summary import SummaryHistory, apply_verdicts, build_run_summary
from app.pipeline.verdicts import VerdictBook
from app.camera.source import CameraSource, NoCamera, make_camera
from app.ui.changes import describe_change
from app.vision.clients import make_vision_client
from app.vision.service import VerificationService
from app.vision.verify import CheckJob, verification_job
from app.ui.participant_window import ParticipantWindow
from app.ui.setup_screen import SetupChoice

log = logging.getLogger(__name__)


class State(str, Enum):
    SETUP = "SETUP"
    CALIBRATION = "CALIBRATION"
    PLAYING = "PLAYING"
    SAVED = "SAVED"
    FEEDBACK = "FEEDBACK"
    ANALYSING = "ANALYSING"
    GENERATING = "GENERATING"
    TRIAL_INTRO = "TRIAL_INTRO"
    TRIAL = "TRIAL"
    TRIAL_FEEDBACK = "TRIAL_FEEDBACK"
    SURVEY = "SURVEY"
    COMPLETE = "COMPLETE"
    ENDED = "ENDED"


RUNNING_STATES = (State.PLAYING, State.TRIAL)
LLMFactory = Callable[[str], LLMClient]


class _Task(QThread):
    """Runs fn() in a background thread; reports (task_id, result, error) via a queued signal."""
    done = Signal(int, object, object)

    def __init__(self, task_id: int, fn: Callable[[], Any], parent: QObject) -> None:
        super().__init__(parent)
        self.task_id, self.fn = task_id, fn

    def run(self) -> None:
        try:
            self.done.emit(self.task_id, self.fn(), None)
        except Exception:  # never let a worker exception vanish
            self.done.emit(self.task_id, None, traceback.format_exc())


# --- jobs (run in the worker thread; pure: no controller state is modified) ---------------------

def _call_llm(client: LLMClient, call: str, prompt: str, schema: dict, payload: dict
              ) -> tuple[Optional[str], Optional[str], float]:
    t0 = time.perf_counter()
    try:
        raw = client.complete_json(call, prompt, schema, payload)  # type: ignore[arg-type]
        return raw, None, time.perf_counter() - t0
    except LLMError as exc:
        return None, str(exc), time.perf_counter() - t0
    except Exception as exc:  # provider SDK bugs etc.
        return None, f"{exc.__class__.__name__}: {exc}", time.perf_counter() - t0


def analysis_job(client: LLMClient, templates: PromptTemplates, rules: str, condition: str, base: InstructionSet,
                 current: InstructionSet, summaries: list[RunSummary], request: ParticipantRequest,
                 previous_requests: list[dict[str, Any]], run: int) -> dict[str, Any]:
    prompt, schema, payload = build_analysis_prompt(templates, rules, condition, base, current, summaries,
                                                    request, previous_requests)
    raw, err, latency = _call_llm(client, "analysis", prompt, schema, payload)
    if err is None:
        result, outcome = validate_analysis(raw, current.settings, request, base.n_steps)
    else:
        result, outcome = None, ValidationOutcome(ok=False, errors=[f"LLM call failed: {err}"])
    fallback = result is None
    if fallback:
        result = fallback_plan(current.settings, request, "; ".join(outcome.errors))
        outcome = outcome.model_copy(update={"warnings": outcome.warnings + ["fallback plan used"]})
    record = LLMCallRecord(t=clock.now(), wall_time=wall_now(), call="analysis", client=client.name,
                           prompt=prompt, raw_response=raw, validation=outcome, error=err,
                           latency_s=round(latency, 3), run=run)
    return {"record": record, "result": result, "fallback": fallback}


def generation_job(client: LLMClient, templates: PromptTemplates, base: InstructionSet, target: InstructionSettings,
                   keep_detailed: list[int], request: ParticipantRequest, version_level: int, max_retries: int,
                   max_chars: int, descriptions: dict[str, str], run: int) -> dict[str, Any]:
    records: list[LLMCallRecord] = []
    prompt, schema, payload = build_generation_prompt(templates, base, target, keep_detailed, request, max_chars,
                                                      descriptions)
    for attempt in range(1, max_retries + 2):
        raw, err, latency = _call_llm(client, "generation", prompt, schema, payload)
        if err is None:
            new, outcome = validate_generation(raw, base, target, keep_detailed, version_level, max_chars)
        else:
            new, outcome = None, ValidationOutcome(ok=False, errors=[f"LLM call failed: {err}"])
        records.append(LLMCallRecord(t=clock.now(), wall_time=wall_now(), call="generation",
                                     client=client.name, attempt=attempt, prompt=prompt, raw_response=raw,
                                     validation=outcome, error=err, latency_s=round(latency, 3), run=run))
        if new is not None:
            return {"set": new, "records": records}
    return {"set": None, "records": records}


class SessionController(QObject):
    ended = Signal()
    started = Signal()                 # intake submitted, session folder exists
    state_changed = Signal(str)
    summary_added = Signal(object)     # RunSummary
    llm_call_added = Signal(object)    # LLMCallRecord
    decision_added = Signal(object)    # DecisionRecord
    version_added = Signal(object)     # InstructionSet
    observation_added = Signal(object)  # ObservationRecord
    calibration_added = Signal(object)  # CalibrationRecord (result, before the experimenter decides)
    feedback_updated = Signal(object)   # dict: participant feedback + status
    streak_changed = Signal(int)
    points_changed = Signal(int, int)      # total, gained
    summary_updated = Signal(object)       # RunSummary changed after the run (late camera verdict)
    verification_added = Signal(object)    # VerificationRecord (camera check finished)
    verification_updated = Signal(object)  # VerificationRecord after the observer's review

    def __init__(self, cfg: AppConfig, window: ParticipantWindow, llm_factory: LLMFactory,
                 repo: Optional[ContentRepository] = None, parent: QObject | None = None,
                 vision_factory: Optional[Callable[[str], Any]] = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.repo = repo or ContentRepository(cfg)
        self.templates = PromptTemplates(cfg.resolve(cfg.paths.prompts))
        self.window = window
        self.player = window.player
        self.llm_factory = llm_factory
        self.state = State.SETUP
        self.logger: Optional[SessionLogger] = None
        self.source: Optional[GazeSource] = None
        self.llm: Optional[LLMClient] = None
        self.config: dict[str, Any] = {}
        self.choice: Optional[SetupChoice] = None
        self.base_set: Optional[InstructionSet] = None
        self.current_set: Optional[InstructionSet] = None
        self.previous_set: Optional[InstructionSet] = None
        self.candidate: Optional[InstructionSet] = None
        self.run = 0
        self.repetition = 0
        self.is_trial_run = False
        self.runs_since_feedback = 0
        self.streak = 0
        self.aoi_map = AoiMap.from_config(cfg)
        self.history = SummaryHistory()
        self.versions: list[InstructionSet] = []
        self.llm_calls: list[LLMCallRecord] = []
        self.decisions: list[DecisionRecord] = []
        self.observations: list[ObservationRecord] = []
        self.calibrations: list[CalibrationRecord] = []
        self.previous_requests: list[dict[str, Any]] = []
        self.request: Optional[ParticipantRequest] = None
        self.plan: Optional[AnalysisResult] = None
        self.target: Optional[InstructionSettings] = None
        self.feedback: dict[str, Any] = {}
        self.survey_done = False
        self.survey: Optional[SurveyRecord] = None
        self.drift_requested = False
        self.pending_calibration: Optional[CalibrationRecord] = None
        self._calibration_reason = "start"
        self._after_calibration: Optional[Callable[[], None]] = None
        self._run_samples: list[GazeSample] = []
        self._run_events: list[StepEvent] = []
        # camera check
        self.vision_factory = vision_factory or (lambda name: make_vision_client(name, cfg))
        self.camera: CameraSource = NoCamera()
        self.checker = None                       # vision client (None = off)
        self.checker_service: Optional[VerificationService] = None
        self.verdicts = VerdictBook()
        self.verifications: dict[int, VerificationRecord] = {}
        self._photo_jobs: dict[int, CheckJob] = {}
        self._photo_ids = itertools.count(1)
        # gamification points (awarded once per step and run, so Back + Next cannot farm points)
        self.points = 0
        self.run_points = 0
        self._awarded: set[tuple[int, int, str]] = set()
        self._tasks: dict[int, tuple[_Task, Callable[[Any, Optional[str]], None], float]] = {}
        self._task_ids = itertools.count(1)

        self.player.step_event.connect(self._on_step_event)
        self.player.repetition_finished.connect(self._on_run_finished)
        window.end_requested.connect(self.force_end)
        window.intake.submitted.connect(self._on_intake)
        window.calibration.finished.connect(self._on_calibration_finished)
        window.saved.continue_clicked.connect(self._on_saved_continue)
        window.assess.finished.connect(self._on_feedback)
        window.trial_intro.accepted.connect(self._on_accept)
        window.trial_intro.rejected.connect(self._on_reject)
        window.feedback.keep.connect(self._on_keep)
        window.feedback.revert.connect(self._on_revert)
        window.survey.finished.connect(self._on_survey)

    # --- lifecycle ---------------------------------------------------------------------------
    @property
    def condition(self) -> str:
        return self.choice.condition if self.choice else str(self.config.get("condition", "gaze"))

    @property
    def run_label(self) -> str:
        return t("player.trial") if self.is_trial_run else t("player.repetition", n=self.repetition)

    def begin(self, config: dict[str, Any], source: GazeSource, camera: Optional[CameraSource] = None) -> None:
        """Experimenter pressed "Start session": create the LLM clients, show the intake form."""
        self.llm = self.llm_factory(str(config["llm_client"]))   # may raise (e.g. missing API key)
        self.checker = self.vision_factory(str(config.get("checker", "off")))   # may raise as well
        if camera is None:
            cam_cfg = self.cfg.camera.model_copy(update={"device_index": int(config.get("camera_device",
                                                                                     self.cfg.camera.device_index))})
            camera = make_camera(str(config.get("camera", "off")), cam_cfg)
        self._set_camera(camera)
        self.config = dict(config)
        self.source = source
        source.setParent(self)
        source.sample.connect(self._on_sample)
        self.window.set_calibration_enabled(bool(config.get("calibration", True)))
        self.window.intake.reset()
        self.window.show_page(self.window.intake)

    def _on_intake(self, pid: str, answers: dict, path, iset: InstructionSet) -> None:
        if self.state != State.SETUP:
            return
        keys = ("condition", "gaze_source_name", "llm_client", "gamification", "calibration")
        choice = SetupChoice(participant_id=pid, instruction_set_path=path, instruction_set=iset,
                             questionnaire=answers, **{k: self.config[k] for k in keys if k in self.config})
        try:
            self.start_session(choice, self.source)
        except FileExistsError:
            self.window.intake.set_error(t("intake.err_id_exists"))
        except Exception as exc:  # noqa: BLE001 - shown to the participant, logged for the researcher
            log.exception("session start failed")
            self.window.intake.set_error(t("intake.err_start", error=exc))

    def start_session(self, choice: SetupChoice, source: GazeSource) -> None:
        if self.llm is None:
            self.llm = self.llm_factory(choice.llm_client)
        meta = SessionMeta(
            participant_id=choice.participant_id, condition=choice.condition,
            gaze_source=choice.gaze_source_name, llm_client=choice.llm_client,
            instruction_set_file=choice.instruction_set_path.name, questionnaire=choice.questionnaire,
            gamification_at_start=choice.gamification, calibration_at_start=choice.calibration,
            app_version=self.cfg.app.version, started_at=wall_now(),
        )
        self.logger = SessionLogger(self.cfg.resolve(self.cfg.paths.sessions), meta, DEFAULT_CONFIG_PATH)
        self.choice = choice
        if self.source is not source:
            self.source = source
            source.setParent(self)
            source.sample.connect(self._on_sample)
        source.sample.connect(self.logger.log_gaze)
        base = choice.instruction_set
        if not choice.gamification:  # start without guide / badges (experimenter setting)
            base = base.model_copy(update={"settings": InstructionSettings(hidden_elements=["gamification"])})
        self.base_set = base
        self._set_version(base)
        if not isinstance(self.camera, NoCamera):
            self.checker_service = VerificationService(self.checker, self.templates, self.cfg.verification.threshold,
                                                       self.cfg.verification.max_image_px, self)
            self.checker_service.finished.connect(self._on_verification)
            self.checker_service.crashed.connect(lambda jid, msg: self._log_error("verification", f"{jid}: {msg}"))
        self.started.emit()
        if choice.calibration and self.cfg.calibration.enabled:
            self._run_calibration("start", lambda: self._start_run(trial=False))
        else:
            self._start_run(trial=False)

    def end_session(self) -> None:
        """Experimenter ends the session: final questionnaire first (if enabled), then finish."""
        if self.state in (State.ENDED,):
            return
        if self.state in (State.SURVEY, State.COMPLETE) or self.choice is None:
            self._finish()
            return
        survey = self.repo.survey(self.cfg.post_survey.survey) if self.cfg.post_survey.enabled else None
        if survey is None or self.survey_done:
            self._finish()
            return
        self.player.stop()
        self._transition(State.SURVEY, {"survey": survey.id})
        self.window.survey.start(survey)
        self.window.show_page(self.window.survey)

    def force_end(self) -> None:
        """Immediate end (Ctrl+Shift+Q or closing a window): no questionnaire."""
        self._finish()

    def _finish(self) -> None:
        if self.state == State.ENDED:
            return
        completed = self.repetition - (1 if self.player.is_active and not self.is_trial_run else 0)
        self.player.stop()
        self.window.calibration.stop()
        self._transition(State.ENDED, {"repetitions_completed": max(0, completed), "runs": self.run})
        if self.source is not None:
            self.source.stop()
        self.ended.emit()
        self._close_when_checks_done(time.monotonic() + self.cfg.verification.drain_timeout_s)

    def _close_when_checks_done(self, deadline: float) -> None:
        """Camera checks still running keep the log open (bounded); then everything is closed."""
        if self.checks_pending > 0 and time.monotonic() < deadline:
            QTimer.singleShot(200, lambda: self._close_when_checks_done(deadline))
            return
        self.camera.stop()
        if self.checker_service is not None:
            self.checker_service.shutdown()
        if self.logger is not None and not self.logger.closed:
            if self.checks_pending:
                self._log_error("verification", f"{self.checks_pending} camera check(s) unfinished at close")
            self.logger.write_summaries(self.history.all())
            self.logger.close()

    def wait_for_tasks(self, timeout_ms: int) -> None:
        """Called on application exit: let running LLM calls finish (bounded)."""
        for task, _, _ in list(self._tasks.values()):
            task.wait(timeout_ms)
        if self.checker_service is not None:
            self.checker_service.wait_idle(timeout_ms / 1000)

    # --- eye-tracker check ---------------------------------------------------------------------
    def request_drift_check(self) -> None:
        """Queue an eye-tracker check before the next run (or run it now between runs)."""
        if self.state in (State.ENDED, State.SURVEY, State.COMPLETE, State.CALIBRATION) or self.choice is None:
            return
        self.drift_requested = True
        self._log_event("drift check requested")

    def _run_calibration(self, reason: str, then: Callable[[], None]) -> None:
        self._calibration_reason = reason
        self._after_calibration = then
        self.pending_calibration = None
        self._transition(State.CALIBRATION, {"reason": reason})
        self.window.calibration.start(self.cfg.calibration.points)
        self.window.show_page(self.window.calibration)

    def _on_calibration_finished(self, samples_per_point: list) -> None:
        if self.state != State.CALIBRATION:
            return
        cal = self.cfg.calibration
        points = [evaluate_point(tuple(target), samples, cal, self.cfg.screen)
                  for target, samples in zip(cal.points, samples_per_point)]
        mean, mx, passed = summarise(points, cal)
        rec = CalibrationRecord(t=clock.now(), wall_time=wall_now(), reason=self._calibration_reason,
                                points=points, mean_error_deg=mean, max_error_deg=mx, passed=passed)
        self.pending_calibration = rec
        self.window.calibration.show_result(rec, cal.max_error_deg)
        self.calibration_added.emit(rec)

    def calibration_decision(self, decision: str) -> None:
        """Experimenter: 'accepted' | 'repeated' | 'skipped'."""
        if self.state != State.CALIBRATION or decision not in ("accepted", "repeated", "skipped"):
            return
        rec = self.pending_calibration
        if rec is None:  # skipped while the check was still running
            if decision != "skipped":
                return
            rec = CalibrationRecord(t=clock.now(), wall_time=wall_now(), reason=self._calibration_reason,
                                    points=[], passed=False)
        rec = rec.model_copy(update={"decision": decision})
        self.calibrations.append(rec)
        if self.logger is not None:
            self.logger.log_calibration(rec)
        self.window.calibration.stop()
        if decision == "repeated":
            self._run_calibration(self._calibration_reason, self._after_calibration or (lambda: None))
            return
        then, self._after_calibration = self._after_calibration, None
        self.pending_calibration = None
        if then is not None:
            then()

    # --- runs ---------------------------------------------------------------------------------
    def _start_run(self, trial: bool, detail: Optional[dict] = None) -> None:
        if self.state in (State.ENDED, State.SURVEY, State.COMPLETE):
            return
        if self.drift_requested:
            self.drift_requested = False
            self._run_calibration("drift", lambda: self._start_run(trial, detail))
            return
        max_rep = self.cfg.loop.max_repetitions
        if not trial and max_rep is not None and self.repetition >= max_rep:
            self.end_session()
            return
        self.run += 1
        self.run_points = 0
        self.is_trial_run = trial
        if not trial:
            self.repetition += 1
        self._run_samples.clear()
        self._run_events.clear()
        if self.cfg.gamification.streak_mode == "repetitions":
            self._set_streak(self._back_free_streak())
        self._transition(State.TRIAL if trial else State.PLAYING,
                         {"run": self.run, "repetition": None if trial else self.repetition,
                          "version_level": self.current_set.version_level,
                          "settings": self.current_set.settings.model_dump(mode="json"), **(detail or {})})
        self.window.show_page(self.player)
        self.player.load(self.current_set, self.run, run_label=self.run_label, trial=trial, streak=self.streak,
                         points=self.points)

    def _on_sample(self, s: GazeSample) -> None:
        if self.state in RUNNING_STATES:
            self._run_samples.append(s)
        elif self.state == State.CALIBRATION:
            self.window.calibration.feed(s)
        self.window.overlay.on_sample(s)

    def _on_step_event(self, e: StepEvent) -> None:
        if self.logger is not None:
            self.logger.log_step_event(e)
        self._run_events.append(e)
        if e.event == "end" and self.state in RUNNING_STATES:
            self._award(e.run, e.step_index, "step", self.cfg.gamification.points_per_step)
            self._take_photo(e.run, e.step_index)

    def _on_run_finished(self, run: int) -> None:
        if self.state not in RUNNING_STATES:
            return
        trial = self.state == State.TRIAL
        summary = self._add_summary(run, trial)
        if trial:
            self._transition(State.TRIAL_FEEDBACK, {"run": run, "version_level": self.current_set.version_level})
            self._set_feedback_status("trial done")
            score = self.trial_score(summary) if self._gamified(self.current_set) else None
            self.window.feedback.reset(score)
            self.window.show_page(self.window.feedback)
            return
        self.runs_since_feedback += 1
        if self.runs_since_feedback < self.cfg.loop.feedback_every_n_repetitions:
            self._start_run(trial=False)
            return
        self.runs_since_feedback = 0
        self._transition(State.SAVED, {"run": run, "repetition": self.repetition})
        show_time = self.cfg.gamification.show_completion_time and self._gamified(self.current_set)
        duration = summary.totals.total_duration_s if (summary and show_time) else None
        badges = self.run_badges(summary) if self._gamified(self.current_set) else {}
        self.window.saved.show_saved(self.repetition, self.current_set.n_steps, duration, badges)
        self.window.show_page(self.window.saved)

    def _add_summary(self, run: int, trial: bool) -> Optional[RunSummary]:
        try:
            summary = build_run_summary(
                run, None if trial else self.repetition, self.current_set, list(self._run_samples),
                list(self._run_events), self.aoi_map, self.cfg.preprocess.min_visit_ms, self.verdicts.for_run(run))
        except PreprocessError as exc:
            self._log_error("summary", f"run {run}: {exc}")
            return None
        self.history.add(summary)
        if self.logger is not None:
            self.logger.write_summaries(self.history.all())
        self.summary_added.emit(summary)
        return summary

    # --- observer verification ---------------------------------------------------------------
    def observe(self, verdict: str) -> Optional[ObservationRecord]:
        """Experimenter verification of the current step ('correct' | 'error')."""
        if self.state not in RUNNING_STATES or verdict not in ("correct", "error") or not self.player.is_active:
            return None
        return self._apply_verdict(self.run, self.player.current, verdict, "observer")

    def _apply_verdict(self, run: int, step_index: int, verdict: str, source: str,
                       verification_id: Optional[int] = None) -> Optional[ObservationRecord]:
        """Record a step verdict (observer button, confident camera check or review) and update
        the streak and, if the run is already summarised, its metrics."""
        if not self.verdicts.set(run, step_index, verdict, source):
            return None
        rec = ObservationRecord(t=clock.now(), wall_time=wall_now(), run=run, step_index=step_index,
                                verdict=verdict, source=source, verification_id=verification_id)
        self.observations.append(rec)
        if self.logger is not None:
            self.logger.log_observation(rec)
        if self.cfg.gamification.streak_mode == "observer" and self.state != State.ENDED:
            self._set_streak(self.streak + 1 if verdict == "correct" else 0)
        if verdict == "correct":
            self._award(run, step_index, "correct", self.cfg.gamification.points_correct_bonus)
        summary = self.history.get(run)
        if summary is not None:
            updated = apply_verdicts(summary, self.verdicts.for_run(run))
            self.history.replace(updated)
            if self.logger is not None:
                self.logger.write_summaries(self.history.all())
            self.summary_updated.emit(updated)
        self.observation_added.emit(rec)
        return rec

    # --- camera check ------------------------------------------------------------------------
    def _set_camera(self, camera: CameraSource) -> None:
        self.camera = camera
        camera.setParent(self)
        camera.captured.connect(self._on_photo)
        camera.failed.connect(self._on_photo_failed)
        camera.start()

    def _take_photo(self, run: int, step_index: int) -> None:
        """Called on every step end: photograph the board (asynchronous, never blocks the player)."""
        if isinstance(self.camera, NoCamera) or self.logger is None or self.current_set is None:
            return
        step = self.current_set.steps[step_index]
        jid = next(self._photo_ids)
        rel = f"photos/run{run:03d}_step{step_index + 1:02d}_{jid:04d}.jpg"
        ref = self.repo.media_file(step.reference_image) if step.reference_image else None
        self._photo_jobs[jid] = CheckJob(
            id=jid, run=run, step_index=step_index, covers=step.covered(), photo=self.logger.dir / rel,
            photo_rel=rel, reference=ref, reference_rel=step.reference_image if ref else None,
            step_text=step.text, part_id=step.part_id, quantity=step.quantity, positions=list(step.position_ids))
        self.camera.capture(jid, self.logger.dir / rel, ref)

    def _on_photo(self, jid: int, _path: str) -> None:
        job = self._photo_jobs.pop(jid, None)
        if job is None:
            return
        if self.checker_service is not None:
            self.checker_service.submit(job)
        else:
            self._on_verification(verification_job(None, self.templates, job, 1.0, 1))

    def _on_photo_failed(self, jid: int, message: str) -> None:
        job = self._photo_jobs.pop(jid, None)
        if job is None:
            return
        self._on_verification(VerificationRecord(
            id=jid, t=clock.now(), wall_time=wall_now(), run=job.run, step_index=job.step_index, covers=job.covers,
            photo="", reference=job.reference_rel, client=getattr(self.checker, "name", "off"),
            needs_review=True, error=f"camera: {message}"[:1000]))

    def _on_verification(self, rec: VerificationRecord) -> None:
        self.verifications[rec.id] = rec
        if self.logger is not None:
            self.logger.log_verification(rec)
        self.verification_added.emit(rec)
        if not rec.needs_review and rec.verdict in ("correct", "error"):
            self._apply_verdict(rec.run, rec.step_index, rec.verdict, "camera", rec.id)

    def review(self, verification_id: int, verdict: str) -> Optional[VerificationRecord]:
        """Observer decides (or corrects) a camera check: 'correct' | 'error'."""
        rec = self.verifications.get(verification_id)
        if rec is None or verdict not in ("correct", "error"):
            return None
        rec = rec.model_copy(update={"reviewed": verdict, "needs_review": False})
        self.verifications[verification_id] = rec
        if self.logger is not None:
            self.logger.log_verification(rec)
        self._apply_verdict(rec.run, rec.step_index, verdict, "review", verification_id)
        self.verification_updated.emit(rec)
        return rec

    @property
    def review_queue(self) -> list[VerificationRecord]:
        return [r for r in self.verifications.values() if r.needs_review and r.reviewed is None]

    @property
    def checks_pending(self) -> int:
        return len(self._photo_jobs) + (self.checker_service.pending if self.checker_service else 0)

    # --- feedback (P3 / P4) --------------------------------------------------------------------
    def _on_saved_continue(self) -> None:
        if self.state != State.SAVED:
            return
        self._transition(State.FEEDBACK, {"repetition": self.repetition})
        self.window.assess.start(self.current_set.settings, self.cfg.feedback.difficulty_options)
        self.window.show_page(self.window.assess)

    def _on_feedback(self, request: ParticipantRequest) -> None:
        if self.state != State.FEEDBACK:
            return
        detail = request.model_dump(mode="json")
        self.feedback = {**detail, "repetition": self.repetition, "status": "pending"}
        if request.is_empty:
            self._decision("continue", self.current_set.version_level, None, detail)
            self._set_feedback_status("no change")
            self._start_run(trial=False, detail={"after": "continue"})
            return
        self._decision("request", self.current_set.version_level, None, detail)
        problems = check_request(self.current_set.settings, request)
        if problems:  # the feedback UI prevents this; guard anyway
            self._log_error("request", "; ".join(problems))
            self._set_feedback_status("invalid")
            self._start_run(trial=False, detail={"after": "invalid_request"})
            return
        self.request = request
        self.previous_requests.append({"repetition": self.repetition, "request": detail,
                                       "target_settings": None, "decision": "pending"})
        self._set_feedback_status("processing")
        self._transition(State.ANALYSING, {"request": detail})
        self.window.analysing.show_analysing(self.condition)
        self.window.show_page(self.window.analysing)
        rules = self.repo.rules().render()
        summaries = self.history.last(self.cfg.loop.history_k)
        history = [dict(p) for p in self.previous_requests[:-1]]
        args = (self.llm, self.templates, rules, self.condition, self.base_set, self.current_set, summaries,
                request, history, self.run)
        self._run_task(lambda: analysis_job(*args), self._analysis_done, self.cfg.loop.analysing_min_display_s)

    def _analysis_done(self, res: Optional[dict], error: Optional[str]) -> None:
        if self.state != State.ANALYSING:
            return  # session ended meanwhile
        if error is not None or res is None:  # unexpected worker crash
            self._log_error("analysis", error or "no result")
            plan = fallback_plan(self.current_set.settings, self.request, "worker error")
        else:
            self._add_llm_record(res["record"])
            plan = res["result"]
        self.plan = plan
        self.target = target_settings(self.current_set.settings, self.request, plan.target_merge_level)
        self.previous_requests[-1]["target_settings"] = self.target.model_dump(mode="json")
        new_level = max(v.version_level for v in self.versions) + 1
        if self.target.merge_level == self.current_set.settings.merge_level:
            self._candidate_ready(self.current_set.model_copy(update={"settings": self.target,
                                                                       "version_level": new_level}), [])
        elif self.target.merge_level == 0:
            self._candidate_ready(self.base_set.model_copy(update={"settings": self.target,
                                                                    "version_level": new_level}), [])
        else:
            self._transition(State.GENERATING, {"target_settings": self.target.model_dump(mode="json"),
                                                "keep_detailed_steps": plan.keep_detailed_steps})
            self.window.analysing.show_generating()
            descriptions = {p: i.description for p, i in self.repo.catalog().by_path().items()}
            args = (self.llm, self.templates, self.base_set, self.target, plan.keep_detailed_steps, self.request,
                    new_level, self.cfg.llm.generation_max_retries, self.cfg.llm.max_text_chars, descriptions,
                    self.run)
            self._run_task(lambda: generation_job(*args), self._generation_done, 0.0)

    def _generation_done(self, res: Optional[dict], error: Optional[str]) -> None:
        if self.state != State.GENERATING:
            return
        for rec in (res or {}).get("records", []):
            self._add_llm_record(rec)
        new: Optional[InstructionSet] = (res or {}).get("set")
        if error is not None or new is None:
            self._log_error("generation", error or "no valid instruction set after retry")
            self.previous_requests[-1]["decision"] = "generation_failed"
            self._set_feedback_status("failed")
            self.window.analysing.show_notice(t("analysing.failed_title"), t("analysing.failed_text"))
            QTimer.singleShot(int(self.cfg.loop.notice_display_s * 1000),
                              lambda: self._start_run(trial=False, detail={"after": "generation_failed"})
                              if self.state == State.GENERATING else None)
            return
        self._candidate_ready(new, (res or {}).get("records", []))

    def _candidate_ready(self, new: InstructionSet, records: list) -> None:
        # reuse an identical earlier version (e.g. going back to a layout used before)
        key = structure_key(new)
        new = next((v for v in self.versions if structure_key(v) == key), new)
        self.candidate = new
        items = describe_change(self.current_set.settings, new.settings)
        texts_changed = (len(new.steps) != len(self.current_set.steps)
                         or any(s.text != b.text for s, b in zip(new.steps, self.current_set.steps)))
        if records and texts_changed:
            items.append(t("changes.texts"))
        level_up = None
        if self._gamified(self.current_set) and new.settings.merge_level > self.current_set.settings.merge_level:
            level_up = t("trial_intro.level_up", level=t(f"player.level.{new.settings.merge_level}"))
        self._transition(State.TRIAL_INTRO, {"candidate_version": new.version_level,
                                             "target_settings": new.settings.model_dump(mode="json")})
        self.window.trial_intro.show_intro(self.plan.participant_message, items, level_up)
        self.window.show_page(self.window.trial_intro)

    # --- trial intro / trial feedback --------------------------------------------------------
    def _on_reject(self) -> None:
        if self.state != State.TRIAL_INTRO:
            return
        self._decision("reject", self.current_set.version_level, None)
        self.previous_requests[-1]["decision"] = "reject"
        self._set_feedback_status("kept current layout")
        self.candidate = None
        self._start_run(trial=False, detail={"after": "reject"})

    def _on_accept(self) -> None:
        if self.state != State.TRIAL_INTRO or self.candidate is None:
            return
        new = self.candidate
        self._decision("accept", self.current_set.version_level, new.version_level)
        self.previous_requests[-1]["decision"] = "accept"
        self._set_feedback_status("trial running")
        self.previous_set = self.current_set
        self._set_version(new)
        self.candidate = None
        self._start_run(trial=True)

    def _on_keep(self) -> None:
        if self.state != State.TRIAL_FEEDBACK:
            return
        self._decision("keep", self.current_set.version_level, self.current_set.version_level)
        self.previous_requests[-1]["decision"] = "keep"
        self._set_feedback_status("applied")
        self.previous_set = None
        self.runs_since_feedback = 0
        self._start_run(trial=False, detail={"after": "keep"})

    def _on_revert(self) -> None:
        if self.state != State.TRIAL_FEEDBACK or self.previous_set is None:
            return
        prev = self.previous_set
        self._decision("revert", self.current_set.version_level, prev.version_level)
        self.previous_requests[-1]["decision"] = "revert"
        self._set_feedback_status("reverted")
        self.current_set, self.previous_set = prev, None
        self.runs_since_feedback = 0
        self._start_run(trial=False, detail={"after": "revert"})

    # --- post survey ---------------------------------------------------------------------------
    def _on_survey(self, answers: dict) -> None:
        if self.state != State.SURVEY:
            return
        survey = self.repo.survey(self.cfg.post_survey.survey)
        clean = {k: int(v) for k, v in answers.items() if isinstance(v, int)}
        rec = SurveyRecord(survey_id=survey.id if survey else self.cfg.post_survey.survey, wall_time=wall_now(),
                           answers=clean, score=survey.score(clean) if survey else None)
        self.survey_done = True
        self.survey = rec
        if self.logger is not None:
            self.logger.write_survey(rec)
        self._transition(State.COMPLETE, {"survey_score": rec.score})
        self.window.show_page(self.window.complete)
        self._finish()

    # --- gamification ---------------------------------------------------------------------------
    @staticmethod
    def _gamified(iset: Optional[InstructionSet]) -> bool:
        return iset is not None and "gamification" not in iset.settings.hidden_elements

    def _set_streak(self, n: int) -> None:
        old, self.streak = self.streak, max(0, n)
        self.player.set_streak(self.streak)
        self.streak_changed.emit(self.streak)
        if self.streak > old and self.streak in self.cfg.gamification.cheer_streaks:
            self.player.cheer(t("player.cheer", n=self.streak))

    def _award(self, run: int, step_index: int, kind: str, points: int) -> None:
        key = (run, step_index, kind)
        if points <= 0 or key in self._awarded or self.state == State.ENDED:
            return
        self._awarded.add(key)
        self.points += points
        if run == self.run:
            self.run_points += points
        self.player.set_points(self.points, points)
        self.points_changed.emit(self.points, points)

    def run_badges(self, summary: Optional[RunSummary]) -> dict[str, Any]:
        """Achievements of a finished regular run, shown on the "completed" screen."""
        if summary is None:
            return {}
        earlier = [s.totals.total_duration_s for s in self.history.regular() if s.run != summary.run]
        best = (self.cfg.gamification.show_personal_best and bool(earlier)
                and summary.totals.total_duration_s < min(earlier))
        correct = sum(m.observer_correct for m in summary.steps)
        return {"personal_best": best, "correct": correct, "points": self.run_points}

    def _back_free_streak(self) -> int:
        n = 0
        for s in reversed(self.history.regular()):
            if s.totals.total_back_presses or s.totals.total_observer_errors:
                break
            n += 1
        return n

    @staticmethod
    def trial_score(summary: Optional[RunSummary]) -> Optional[float]:
        """0.5-step star score from total time vs. the MTM reference (minus errors)."""
        if summary is None or summary.totals.total_expected_s <= 0:
            return None
        ratio = summary.totals.total_duration_s / summary.totals.total_expected_s
        score = 2.0
        for limit, value in ((1.0, 5.0), (1.25, 4.5), (1.5, 4.0), (2.0, 3.5), (2.5, 3.0), (3.0, 2.5)):
            if ratio <= limit:
                score = value
                break
        return max(1.0, score - 0.5 * summary.totals.total_observer_errors)

    # --- helpers ------------------------------------------------------------------------------
    def _set_feedback_status(self, status: str) -> None:
        if self.feedback:
            self.feedback["status"] = status
            self.feedback_updated.emit(dict(self.feedback))

    def _set_version(self, iset: InstructionSet) -> None:
        self.current_set = iset
        if all(v.version_level != iset.version_level for v in self.versions):
            self.versions.append(iset)
            if self.logger is not None:
                self.logger.save_version(iset)
            self.version_added.emit(iset)

    def _decision(self, decision: str, from_v: int, to_v: Optional[int], detail: Optional[dict] = None) -> None:
        rec = DecisionRecord(t=clock.now(), wall_time=wall_now(), run=self.run,
                             decision=decision, from_version=from_v, to_version=to_v, detail=detail)
        self.decisions.append(rec)
        if self.logger is not None:
            self.logger.log_decision(rec)
        self.decision_added.emit(rec)

    def _add_llm_record(self, rec: LLMCallRecord) -> None:
        self.llm_calls.append(rec)
        if self.logger is not None:
            self.logger.log_llm_call(rec)
        self.llm_call_added.emit(rec)

    def _log_event(self, message: str) -> None:
        if self.logger is not None:
            self.logger.log_state(self.state.value, self.state.value, {"event": message})

    def _log_error(self, where: str, message: str) -> None:
        log.warning("%s: %s", where, message)
        if self.logger is not None:
            self.logger.log_state(self.state.value, self.state.value, {"error": where, "message": message[-2000:]})

    def _transition(self, to: State, detail: Optional[dict] = None) -> None:
        if self.logger is not None:
            self.logger.log_state(self.state.value, to.value, detail)
        self.state = to
        self.state_changed.emit(to.value)

    def _run_task(self, fn: Callable[[], Any], on_done: Callable[[Any, Optional[str]], None],
                  min_display_s: float = 0.0) -> None:
        tid = next(self._task_ids)
        task = _Task(tid, fn, self)
        task.done.connect(self._task_done)
        task.finished.connect(task.deleteLater)  # 'finished' is delivered after 'done'
        self._tasks[tid] = (task, on_done, time.monotonic() + min_display_s)
        task.start()

    @Slot(int, object, object)
    def _task_done(self, tid: int, result: Any, error: Optional[str]) -> None:
        entry = self._tasks.pop(tid, None)
        if entry is None:
            return
        _, on_done, not_before = entry
        delay_ms = max(0, int((not_before - time.monotonic()) * 1000))
        QTimer.singleShot(delay_ms, lambda: on_done(result, error))
