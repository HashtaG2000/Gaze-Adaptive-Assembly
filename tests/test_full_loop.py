"""Full session loop end to end (headless): intake, eye-tracker check, synthetic gaze, saved /
assessment screens, scripted or mock LLM, trial intro, trial run, keep / revert, observer
verification, drift check, post survey, logging, cockpit, archive and export."""
import json

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtTest import QTest

from app import clock
from app.config import PROJECT_ROOT, load_config
from app.gaze.source import GazeSource
from app.llm.mock_client import MockLLMClient, mock_generation
from app.loop.controller import SessionController, State
from app.models import GAZE_METRIC_FIELDS, GazeSample, InstructionSet
from app.pipeline.archive import list_sessions, load_session, participant_exists
from app.pipeline.export import export_session
from app.ui.participant_window import ParticipantWindow
from app.ui.setup_screen import SetupChoice

BASE_PATH = PROJECT_ROOT / "data/instruction_sets/duplo_tower_base.json"
BASE = InstructionSet.model_validate_json(BASE_PATH.read_text(encoding="utf-8"))
POINTS = [(960 / 1920, 200 / 1080), (500 / 1920, 600 / 1080), (1400 / 1920, 600 / 1080), (0.5, 1.3)]
CONFIG = {"condition": "gaze", "gaze_source_name": "mouse", "llm_client": "mock", "gamification": True,
          "calibration": False}


class ScriptedSource(GazeSource):
    name = "scripted"

    def start(self):
        self._set_connected(True)

    def stop(self):
        self._set_connected(False)

    def push(self, i):
        x, y = POINTS[i % len(POINTS)]
        self._emit_sample(GazeSample(t=clock.now(), x_norm=x, y_norm=y, valid=True))

    def push_xy(self, x, y):
        self._emit_sample(GazeSample(t=clock.now(), x_norm=x, y_norm=y, valid=True))


class ScriptedLLM:
    """analysis: next scripted target level (int) or 'invalid'; generation: mock steps or garbage."""
    name = "scripted"

    def __init__(self, levels=(), generation_ok=True, keep=()):
        self.levels = list(levels)
        self.generation_ok = generation_ok
        self.keep = list(keep)
        self.calls = []

    def complete_json(self, call, prompt, schema, payload):
        self.calls.append((call, prompt, payload))
        if call == "analysis":
            level = self.levels.pop(0)
            if level == "invalid":
                return "this is not json"
            return json.dumps({"progress_assessment": "a", "plateau": True, "target_merge_level": level,
                               "keep_detailed_steps": self.keep, "participant_message": "New version.",
                               "researcher_reasoning": "r"})
        if not self.generation_ok:
            return '{"steps": "broken"}'
        return json.dumps(mock_generation(payload))


def wait_until(cond, timeout_ms=8000):
    loop = QEventLoop()
    t = QTimer()
    t.setInterval(10)
    t.timeout.connect(lambda: loop.quit() if cond() else None)
    QTimer.singleShot(timeout_ms, loop.quit)
    t.start()
    if not cond():
        loop.exec()
    t.stop()
    assert cond(), "timed out"


def make_cfg(tmp_path, every_n=1):
    cfg = load_config()
    cfg.input.debounce_ms = 0
    cfg.paths.sessions = tmp_path
    cfg.loop.analysing_min_display_s = 0
    cfg.loop.notice_display_s = 0
    cfg.loop.feedback_every_n_repetitions = every_n
    cfg.calibration.settle_s = 0.02
    cfg.calibration.sample_s = 0.12
    cfg.calibration.min_samples = 2
    return cfg


@pytest.fixture
def session(qapp, tmp_path, monkeypatch):
    import app.ui.calibration_screen as cal_screen
    monkeypatch.setattr(cal_screen, "GET_READY_S", 0.0)
    made = []

    def make(llm, condition="gaze", every_n=1, via_form=False, calibration=False, gamification=True, pid="E2E0001",
             camera=None, checker=None, cfg=None):
        cfg = cfg or make_cfg(tmp_path, every_n)
        win = ParticipantWindow(cfg, [(BASE_PATH, BASE)], lambda p: participant_exists(tmp_path, p))
        win.resize(1920, 1080)
        win.show()
        ctl = SessionController(cfg, win, lambda name: llm)
        src = ScriptedSource()
        src.start()
        made.append((ctl, win))
        if camera is not None:                       # board camera + step checker (camera check tests)
            ctl.checker = checker
            ctl._set_camera(camera)
        if via_form:
            ctl.begin({**CONFIG, "condition": condition, "calibration": calibration}, src)
        else:
            ctl.start_session(SetupChoice(pid, BASE_PATH, BASE, condition, "mouse", "mock",
                                          gamification=gamification, calibration=calibration), src)
        return ctl, win, src

    yield make
    for ctl, win in made:
        ctl.force_end()
        ctl.wait_for_tasks(5000)
        win.close()


def play(ctl, win, src):
    for i in range(ctl.current_set.n_steps):
        for k in range(4):
            src.push(i + k)
        win.player.go_next()


def assess(win, direction="keep", toggles=(), difficulty=0):
    """Saved screen -> P4-A (difficulty, compaction) -> P4-B (elements) -> submit."""
    if win.currentWidget() is win.saved:
        win.saved.btn.click()
    a = win.assess
    assert win.currentWidget() is a
    a.diff_group.button(difficulty).click()
    a.dir_cards[direction].click()
    assert a.btn_next.isEnabled()
    a.btn_next.click()
    for el in toggles:
        a.el_cards[el].click()
    a.btn_submit.click()


def finish_survey(win):
    s = win.survey
    while win.currentWidget() is s:
        for _, item in s.items:
            item.group.button(3).click()    # "Agree" everywhere
        s.btn_next.click()


# --- the reported bug: trial runs must not count as repetitions ------------------------------------

def test_trial_run_does_not_shift_repetition_numbers(session):
    llm = ScriptedLLM([1])
    ctl, win, src = session(llm)
    assert win.player.run_chip.text() == "Repetition 1"
    play(ctl, win, src)
    assert ctl.state == State.SAVED and win.saved.title.text() == "Repetition 1 completed!"
    assess(win, "compress")
    wait_until(lambda: ctl.state == State.TRIAL_INTRO)
    intro = win.trial_intro
    assert "does not count as a repetition" in intro.note.text()
    assert intro.message.text() == "New version."
    intro.btn_accept.click()
    assert ctl.state == State.TRIAL and win.player.run_chip.text() == "Trial run"
    assert ctl.current_set.settings.merge_level == 1 and ctl.current_set.n_steps < BASE.n_steps
    play(ctl, win, src)
    assert ctl.state == State.TRIAL_FEEDBACK
    win.feedback.btn_keep.click()
    assert ctl.state == State.PLAYING and win.player.run_chip.text() == "Repetition 2"
    play(ctl, win, src)
    assert win.saved.title.text() == "Repetition 2 completed!"         # not "Repetition 3"
    runs = ctl.history.all()
    assert [(s.run, s.repetition, s.is_trial) for s in runs] == [(1, 1, False), (2, None, True), (3, 2, False)]
    assert [c for c, _, _ in llm.calls] == ["analysis", "generation"]
    on_disk = json.loads((ctl.logger.dir / "summaries.json").read_text(encoding="utf-8"))
    assert [s["repetition"] for s in on_disk] == [1, None, 2]


def test_llm_shapes_groups_and_texts(session):
    """The LLM decides which steps stay detailed and writes the texts; the app derives structure."""
    llm = ScriptedLLM([1], keep=[2, 3])
    ctl, win, src = session(llm)
    play(ctl, win, src)
    assess(win, "compress")
    wait_until(lambda: ctl.state == State.TRIAL_INTRO)
    new = ctl.candidate
    covers = [s.covered() for s in new.steps]
    assert [2] in covers and [3] in covers and [0, 1] in covers
    assert any(" and " in s.text for s in new.steps if len(s.covered()) == 2)
    gen_prompt = next(p for c, p, _ in llm.calls if c == "generation")
    assert "keep" in gen_prompt.lower()
    assert any("rewritten" in w.text.text() for w in win.trial_intro.findChildren(type(win.trial_intro.items_box.itemAt(0).widget())))


def test_keep_hide_video_reject_then_hide_again_accept(session):
    llm = ScriptedLLM([0, 0])
    ctl, win, src = session(llm)
    play(ctl, win, src)
    assess(win, "keep", ["video"])
    wait_until(lambda: ctl.state == State.TRIAL_INTRO)
    assert [c for c, _, _ in llm.calls] == ["analysis"]          # no generation for display-only changes
    win.trial_intro.btn_reject.click()
    assert ctl.state == State.PLAYING and ctl.current_set.version_level == 0
    assert win.player.run_chip.text() == "Repetition 2"
    play(ctl, win, src)
    assess(win, "keep", ["video"])
    wait_until(lambda: ctl.state == State.TRIAL_INTRO)
    win.trial_intro.btn_accept.click()
    assert "video" in ctl.current_set.settings.hidden_elements
    assert win.player.placeholders["video"].isVisible() and not win.player.video.isVisible()
    play(ctl, win, src)
    win.feedback.btn_revert.click()
    assert ctl.current_set.version_level == 0 and not ctl.current_set.settings.hidden_elements
    assert win.player.run_chip.text() == "Repetition 3"
    decisions = [d.decision for d in ctl.decisions]
    assert decisions == ["request", "reject", "request", "accept", "revert"]


def test_no_change_continues(session):
    ctl, win, src = session(ScriptedLLM([]))
    play(ctl, win, src)
    assess(win, "keep")
    assert ctl.state == State.PLAYING and ctl.repetition == 2
    assert ctl.decisions[-1].decision == "continue"
    assert ctl.decisions[-1].detail["difficulty"] == "easy"


def test_invalid_analysis_uses_fallback_and_failed_generation_keeps_version(session):
    llm = ScriptedLLM(["invalid"], generation_ok=False)
    ctl, win, src = session(llm)
    play(ctl, win, src)
    assess(win, "compress")
    wait_until(lambda: ctl.state == State.PLAYING)
    assert ctl.current_set.version_level == 0 and ctl.repetition == 2
    calls = ctl.llm_calls
    assert calls[0].call == "analysis" and not calls[0].validation.ok
    gens = [c for c in calls if c.call == "generation"]
    assert len(gens) == 2 and not any(c.validation.ok for c in gens)   # one retry, then give up


def test_feedback_every_n_and_time_only(session):
    llm = ScriptedLLM([1])
    ctl, win, src = session(llm, condition="time_only", every_n=2)
    play(ctl, win, src)
    assert ctl.state == State.PLAYING and ctl.repetition == 2       # no feedback after repetition 1
    play(ctl, win, src)
    assess(win, "compress")
    wait_until(lambda: ctl.state == State.TRIAL_INTRO)
    payload = llm.calls[0][2]
    dumped = json.dumps(payload["run_summaries"])
    for f in GAZE_METRIC_FIELDS:
        assert f'"{f}"' not in dumped
    assert "eye-tracking" not in win.analysing.subtitle.text()


def test_identical_version_is_reused(session):
    llm = ScriptedLLM([1, 0, 1])
    ctl, win, src = session(llm)
    play(ctl, win, src)
    assess(win, "compress")
    wait_until(lambda: ctl.state == State.TRIAL_INTRO)
    win.trial_intro.btn_accept.click()
    play(ctl, win, src)
    win.feedback.btn_keep.click()
    v1 = ctl.current_set
    play(ctl, win, src)
    assess(win, "detail")
    wait_until(lambda: ctl.state == State.TRIAL_INTRO)
    win.trial_intro.btn_accept.click()
    play(ctl, win, src)
    win.feedback.btn_keep.click()
    assert ctl.current_set.settings.merge_level == 0
    play(ctl, win, src)
    assess(win, "compress")
    wait_until(lambda: ctl.state == State.TRIAL_INTRO)
    assert ctl.candidate is v1                                   # same structure -> same version object
    assert len(ctl.versions) == 2                            # going back to level 0 reused v0 as well


# --- observer, streak, calibration, drift ---------------------------------------------------------

def test_observer_verdicts_feed_streak_and_metrics(session):
    ctl, win, src = session(ScriptedLLM([]))
    assert ctl.observe("correct") is not None
    win.player.go_next()
    ctl.observe("correct")
    assert ctl.streak == 2 and win.player.streak_chip.text() == "Streak: 2"
    ctl.observe("error")
    assert ctl.streak == 0
    assert ctl.observe("maybe") is None
    for _ in range(BASE.n_steps - 1):
        win.player.go_next()
    s = ctl.history.all()[-1]
    assert s.steps[1].observer_errors == 1 and s.steps[1].observer_correct == 0   # one final verdict per step
    assert s.totals.total_observer_errors == 1
    assert ctl.observe("correct") is None                        # not during the saved screen
    lines = (ctl.logger.dir / "observations.jsonl").read_text().splitlines()
    assert len(lines) == 3


def _feed_calibration(ctl, win, src):
    """Push gaze exactly on the active target until the check finishes."""
    timer = QTimer()
    timer.setInterval(10)

    def tick():
        cal = win.calibration
        if 0 <= cal.index < len(cal.points):
            src.push_xy(*cal.points[cal.index])

    timer.timeout.connect(tick)
    timer.start()
    wait_until(lambda: ctl.pending_calibration is not None)
    timer.stop()


def test_calibration_gate_and_drift_check(session):
    ctl, win, src = session(ScriptedLLM([]), calibration=True)
    assert ctl.state == State.CALIBRATION and win.currentWidget() is win.calibration
    ctl.calibration_decision("accepted")                     # ignored: no result yet
    assert ctl.state == State.CALIBRATION
    _feed_calibration(ctl, win, src)
    rec = ctl.pending_calibration
    assert rec.passed and rec.mean_error_deg is not None and rec.mean_error_deg < 0.1
    ctl.calibration_decision("repeated")
    assert ctl.state == State.CALIBRATION and len(ctl.calibrations) == 1
    _feed_calibration(ctl, win, src)
    ctl.calibration_decision("accepted")
    assert ctl.state == State.PLAYING and ctl.repetition == 1
    ctl.request_drift_check()
    play(ctl, win, src)
    assess(win, "keep")
    assert ctl.state == State.CALIBRATION                    # drift check before repetition 2
    ctl.calibration_decision("skipped")
    assert ctl.state == State.PLAYING and ctl.repetition == 2
    reasons = [(c.reason, c.decision) for c in ctl.calibrations]
    assert reasons == [("start", "repeated"), ("start", "accepted"), ("drift", "skipped")]
    assert len((ctl.logger.dir / "calibration.jsonl").read_text().splitlines()) == 3


def test_calibration_without_gaze_fails(session):
    ctl, win, src = session(ScriptedLLM([]), calibration=True)
    wait_until(lambda: ctl.pending_calibration is not None)
    assert not ctl.pending_calibration.passed and ctl.pending_calibration.mean_error_deg is None
    ctl.calibration_decision("accepted")                     # the experimenter may still continue
    assert ctl.state == State.PLAYING


# --- intake, survey, end ----------------------------------------------------------------------------

def fill_intake(win, pid):
    form = win.intake
    form.pid.setText(pid)
    for q in form.questions:
        if q.group is not None:
            q.group.button(0).click()
        else:
            q.edit.setText("30")


def test_intake_form_validation_and_duplicate_id(session, tmp_path):
    ctl, win, src = session(ScriptedLLM([]), via_form=True)
    form = win.intake
    assert win.currentWidget() is form and ctl.state == State.SETUP
    QTest.keyClicks(form.pid, "ab12")
    assert form.pid.text() == "AB12" and not form.btn_start.isEnabled()      # upper-cased, too short
    form.pid.clear()
    QTest.keyClicks(form.pid, "abc/../123x9")
    assert form.pid.text() == "ABC123X"                                       # unsafe characters / overflow refused
    form.pid.setText("../../x")                                              # pasted past the validator:
    fill_intake(win, "../../x")
    assert not form.btn_start.isEnabled()                                    # the pattern check still rejects it
    form._submit()
    assert ctl.state == State.SETUP
    (tmp_path / "TAKEN01_20260101_120000").mkdir()
    fill_intake(win, "TAKEN01")
    assert form.btn_start.isEnabled()
    form.btn_start.click()
    assert ctl.state == State.SETUP and "already exists" in form.error.text()
    fill_intake(win, "NEW0001")
    form.btn_start.click()
    assert ctl.state == State.PLAYING and ctl.choice.participant_id == "NEW0001"
    meta = json.loads((ctl.logger.dir / "session.json").read_text(encoding="utf-8"))
    assert meta["questionnaire"]["age"] == 30 and meta["questionnaire"]["gender"] == "Female"


def test_end_session_shows_survey_then_complete(session):
    ctl, win, src = session(ScriptedLLM([]))
    play(ctl, win, src)
    ctl.end_session()
    assert ctl.state == State.SURVEY and win.currentWidget() is win.survey
    assert not win.survey.btn_next.isEnabled()
    finish_survey(win)
    assert ctl.state == State.ENDED and win.currentWidget() is win.complete
    survey = json.loads((ctl.logger.dir / "survey.json").read_text(encoding="utf-8"))
    assert len(survey["answers"]) == 10 and survey["score"] == 50.0
    assert ctl.logger.closed


def test_force_end_mid_run(session):
    ctl, win, src = session(ScriptedLLM([]))
    win.player.go_next()
    ctl.force_end()
    assert ctl.state == State.ENDED
    ctl.force_end()                                          # idempotent
    assert ctl.observe("correct") is None
    win.player.go_next()                                     # no crash, nothing logged
    events = [json.loads(l) for l in (ctl.logger.dir / "events.jsonl").read_text().splitlines()]
    assert events[-1]["to_state"] == "ENDED" and events[-1]["detail"]["repetitions_completed"] == 0


# --- workspace, recordings, export ----------------------------------------------------------------

def make_workspace(cfg):
    from app.core.repository import ContentRepository
    from app.ui.workspace import Workspace
    return Workspace(cfg, ContentRepository(cfg))


def test_workspace_live_recordings_and_export(session, tmp_path):
    ws = make_workspace(make_cfg(tmp_path))
    llm = ScriptedLLM([1])
    ctl, win, src = session(llm)
    ws.attach(ctl)
    ws.show()
    assert ws.windowTitle() == "Workspace"
    assert [ws.nav.item(r).text() for r in range(ws.nav.count())] == [
        "Live", "Recordings", "Enrichments", "Visualizations", "Downloads"]
    ctl.observe("correct")
    assert ws.live.obs_counts.text().startswith("This run: 1 correct")
    src.push(0)
    ws.live.mirror.grab()
    play(ctl, win, src)
    assess(win, "compress", ["video"])
    wait_until(lambda: ctl.state == State.TRIAL_INTRO)
    assert ws.live.fb_values["fb_removed"].text() == "video"
    tabs = ws.visualizations.tabs
    assert tabs.llm_list.count() == 2 and tabs.run_combo.count() == 1
    ws.live.overlay_check.setChecked(True)
    assert win.overlay.isVisible()
    ws.live.overlay_check.setChecked(False)
    win.trial_intro.btn_accept.click()
    play(ctl, win, src)
    win.feedback.btn_keep.click()
    ctl.force_end()
    ws._refresh_status()
    assert not ws.end_btn.isEnabled()

    ws.show_page("recordings")
    rec = ws.recordings
    assert [s.participant_id for s in rec.sessions] == ["E2E0001"] and rec.sessions[0].runs == 2
    rec.table.selectRow(0)
    assert "E2E0001" in rec.details.text()
    rec.btn_open.click()                                       # -> Visualizations, read-only
    assert ws.stack.currentWidget() is ws.visualizations and not ws.visualizations.showing_live
    assert tabs.run_combo.count() == 2 and tabs.llm_list.count() == 2
    rec.btn_open.click()                                       # opening again reuses the entry
    keys = [ws.visualizations.source.itemData(i) for i in range(ws.visualizations.source.count())]
    assert len(keys) == len(set(keys)) == 2                    # live + this recording
    for i in range(tabs.count()):
        tabs.setCurrentIndex(i)
        tabs.grab()
    ws.show_page("downloads")
    top = ws.downloads.tree.topLevelItem(0)
    assert top.childCount() == 1                               # the one session folder
    ws.downloads._expand(top.child(0))
    names = {top.child(0).child(i).text(0) for i in range(top.child(0).childCount())}
    assert {"session.json", "summaries.json", "versions"} <= names
    out = export_session(rec.sessions[0].folder)
    for name in ("runs.csv", "step_metrics.csv", "gaze.csv", "session_data.json", "verifications.csv"):
        assert (out / name).exists(), name
    ws.close()


def test_mock_llm_full_cycle(session):
    ctl, win, src = session(MockLLMClient())
    play(ctl, win, src)
    assess(win, "compress", ["gamification"])
    wait_until(lambda: ctl.state == State.TRIAL_INTRO)
    win.trial_intro.btn_accept.click()
    assert not win.player.badges.isVisible() and not win.player.text.gamified
    play(ctl, win, src)
    assert win.feedback.header.chip.isHidden()               # no star score without gamification
    win.feedback.btn_keep.click()
    assert ctl.current_set.settings.merge_level >= 1


def test_assessment_none_card_is_exclusive(qapp):
    from app.models import InstructionSettings
    from app.ui.assessment_screen import AssessmentScreen
    a = AssessmentScreen()
    a.start(InstructionSettings(hidden_elements=["image"]), ["easy", "hard"])
    assert a.none_card.isChecked()
    a.el_cards["video"].click()
    assert not a.none_card.isChecked()
    assert not a.el_cards["text"].isEnabled()                 # text is now the last visible content
    assert a.el_cards["image"].title.text() == "Show images again"
    a.el_cards["video"].click()
    assert a.none_card.isChecked() and a.el_cards["text"].isEnabled()
    a.el_cards["gamification"].click()
    a.none_card.click()
    assert not a.el_cards["gamification"].isChecked()


def test_analytics_shows_errors(session, tmp_path):
    ctl, win, src = session(ScriptedLLM([]))
    ctl.observe("error")
    win.player.go_next()
    win.player.go_back()
    play(ctl, win, src)
    ws = make_workspace(make_cfg(tmp_path))
    ws.attach(ctl)
    tabs = ws.visualizations.tabs
    text = tabs.analytics_text.toPlainText()
    assert "step 1 (1× error)" in text and "(1× back)" in text
    assert tabs.error_chart.hover.texts and "observer errors 1" in tabs.error_chart.hover.texts[0]
    ctl.force_end()
    ws.close()


def test_intake_age_is_a_typed_number(session):
    ctl, win, src = session(ScriptedLLM([]), via_form=True)
    age = next(q for q in win.intake.questions if q.q.id == "age")
    assert isinstance(age.edit, __import__("PySide6.QtWidgets", fromlist=["QLineEdit"]).QLineEdit)
    QTest.keyClicks(age.edit, "2x5")
    assert age.edit.text() == "25" and age.value() == 25
    age.edit.setText("7")
    assert age.value() is None                       # below the allowed minimum -> unanswered


# --- gamification ------------------------------------------------------------------------------------

def test_points_streak_cheers_and_personal_best(session):
    ctl, win, src = session(ScriptedLLM([]))
    g = ctl.cfg.gamification
    win.player.go_next()
    win.player.go_back()
    win.player.go_next()                                   # step 1 ended twice: points only once
    assert ctl.points == g.points_per_step
    ctl.observe("correct")                                 # step 2 verified correct: bonus
    assert ctl.points == g.points_per_step + g.points_correct_bonus
    assert win.player.points_chip.text().startswith(f"{ctl.points} pts")
    for _ in range(BASE.n_steps - 1):
        win.player.go_next()
    assert ctl.points == BASE.n_steps * g.points_per_step + g.points_correct_bonus
    assert not win.saved.badge_best.isVisible() or win.saved.badge_best.isHidden()   # first run: no best yet
    assert win.saved.badge_points.text() == f"+{ctl.run_points} points"
    assess(win, "keep")
    play(ctl, win, src)                                    # instant run: faster than the first one
    assert ctl.run_badges(ctl.history.all()[-1])["personal_best"]
    assert not win.saved.badge_best.isHidden()
    # streak milestones make the guide cheer (tag on the speech bubble)
    assess(win, "keep")
    for i in range(3):
        ctl.observe("correct")
        win.player.go_next()
    assert ctl.streak >= 3 and "in a row" in win.player.text._cheer


def test_guide_speaks_on_new_step(session):
    ctl, win, src = session(ScriptedLLM([]))
    from app import clock
    text = win.player.text
    assert text.gamified and text._speak_until > clock.now()        # talking right after the step appears
    text._speak_until = 0
    win.player.go_next()
    assert text._speak_until > clock.now()
    text.grab()                                                       # paints the speaking guide
    text._speak_until = 0
    text.grab()                                                       # and the smiling guide
