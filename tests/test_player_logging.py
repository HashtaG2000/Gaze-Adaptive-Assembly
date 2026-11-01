"""Phase 2: player step flow, key handling, hidden elements and session logging (headless)."""
import json
from datetime import datetime

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from app.config import DEFAULT_CONFIG_PATH, PROJECT_ROOT, load_config
from app.models import GazeSample, InstructionSet, SessionMeta, StepEvent
from app.pipeline.logger import SessionLogger
from app.ui.player_screen import PlayerScreen

BASE_SET = PROJECT_ROOT / "data/instruction_sets/duplo_tower_base.json"


@pytest.fixture
def cfg():
    c = load_config()
    c.input.debounce_ms = 0
    return c


@pytest.fixture
def iset():
    return InstructionSet.model_validate_json(BASE_SET.read_text(encoding="utf-8"))


@pytest.fixture
def player(qapp, cfg):
    p = PlayerScreen(cfg, cfg.resolve(cfg.paths.media))
    p.resize(1920, 1080)
    p.show()
    events: list[StepEvent] = []
    finished: list[int] = []
    p.step_event.connect(events.append)
    p.repetition_finished.connect(finished.append)
    yield p, events, finished
    p.stop()
    p.close()


def seq(events):
    return [(e.event, e.step_index) for e in events]


def test_full_repetition_with_click_and_key(player, iset):
    p, events, finished = player
    p.load(iset, 1, run_label="Repetition 1")
    QTest.mouseClick(p.btn_next, Qt.MouseButton.LeftButton)       # step 0 -> 1 by click
    for _ in range(iset.n_steps - 1):
        QTest.keyClick(p, Qt.Key.Key_Space)                       # rest by keyboard
    assert finished == [1]
    expected = []
    for i in range(iset.n_steps):
        expected += [("start", i), ("end", i)]
    assert seq(events) == expected
    assert all(e.run == 1 for e in events)
    ts = [e.t for e in events]
    assert ts == sorted(ts)
    # presses after the repetition finished are ignored
    QTest.keyClick(p, Qt.Key.Key_Space)
    assert len(events) == len(expected)


def test_back_logs_back_and_restarts_previous_step(player, iset):
    p, events, _ = player
    p.load(iset, 2)
    QTest.mouseClick(p.btn_back, Qt.MouseButton.LeftButton)   # disabled on step 1: nothing
    assert seq(events) == [("start", 0)]
    p.go_next()
    p.go_back()
    assert seq(events) == [("start", 0), ("end", 0), ("start", 1), ("back", 1), ("start", 0)]
    assert p.progress_label.text() == f"Step 1 of {iset.n_steps}"


def test_debounce_blocks_double_press(qapp, iset):
    c = load_config()
    c.input.debounce_ms = 300
    p = PlayerScreen(c, c.resolve(c.paths.media)); p.resize(1920, 1080); p.show()
    events = []
    p.step_event.connect(events.append)
    p.load(iset, 1)
    p.go_next(); p.go_next()   # second press within 300 ms is ignored
    assert seq(events) == [("start", 0), ("end", 0), ("start", 1)]
    p.stop(); p.close()


def test_hidden_elements_keep_positions(player, iset):
    p, _, _ = player
    p.load(iset, 1)
    before = {k: r.getRect() for k, r in p.aoi_rects_px().items()}
    hidden = iset.model_copy(update={"settings": iset.settings.model_copy(update={"hidden_elements": ["text", "video"]})})
    p.load(hidden, 1)
    assert not p.text.isVisible() and not p.video.isVisible()
    assert p.image.isVisible()
    # removed elements leave a "removed per participant request" placeholder in their own AOI
    assert p.placeholders["text"].isVisible() and p.placeholders["video"].isVisible()
    assert not p.placeholders["image"].isVisible()
    assert p.placeholders["video"].geometry().getRect() == before["video"]
    assert {k: w.geometry().getRect() for k, w in p.elements.items()} == before


def test_aoi_rects_scale_with_window(player, cfg):
    p, _, _ = player
    p.resize(960, 540)
    r = p.aoi_rects_px()["btn_next"]
    a = cfg.aoi["btn_next"]
    assert r.getRect() == (a.x // 2, a.y // 2, a.w // 2, a.h // 2)


def test_logger_writes_session_folder(tmp_path, iset):
    meta = SessionMeta(participant_id="P000099", condition="gaze", gaze_source="mouse", llm_client="mock",
                       instruction_set_file=BASE_SET.name, started_at=datetime(2026, 10, 18, 10, 0, 0).astimezone())
    log = SessionLogger(tmp_path, meta, DEFAULT_CONFIG_PATH)
    log.save_version(iset)
    log.log_gaze(GazeSample(t=1.0, x_norm=0.5, y_norm=0.5, valid=True))
    log.log_step_event(StepEvent(t=1.1, run=1, step_index=0, event="start"))
    log.log_state("SETUP", "PLAYING", {"repetition": 1})
    log.close()
    d = tmp_path / "P000099_20261018_100000"
    for name in ("session.json", "config.yaml", "gaze.jsonl", "events.jsonl", "decisions.jsonl",
                 "llm_calls.jsonl", "versions/v00.json"):
        assert (d / name).exists(), name
    gaze = [json.loads(l) for l in (d / "gaze.jsonl").read_text().splitlines()]
    assert gaze == [GazeSample(t=1.0, x_norm=0.5, y_norm=0.5, valid=True).model_dump()]
    ev = [json.loads(l) for l in (d / "events.jsonl").read_text().splitlines()]
    assert [e["kind"] for e in ev] == ["step", "state"]
    StepEvent.model_validate(ev[0])
    with pytest.raises(FileExistsError):
        SessionLogger(tmp_path, meta)  # never overwrite an existing session


def test_logger_rejects_unsafe_participant_id(tmp_path):
    meta = SessionMeta(participant_id="../x", condition="gaze", gaze_source="mouse", llm_client="mock",
                       instruction_set_file="x.json", started_at=datetime.now().astimezone())
    with pytest.raises(ValueError):
        SessionLogger(tmp_path, meta)


def test_trial_run_label_and_chips(player, iset):
    p, events, finished = player
    p.load(iset, 4, run_label="Trial run", trial=True, streak=3)
    assert p.run_chip.text() == "Trial run" and p.run_chip.kind == "emerald"
    assert p.streak_chip.text() == "Streak: 3" and p.badges.isVisible()
    assert p.text.gamified
    p.set_streak(5)
    assert p.streak_chip.text() == "Streak: 5"
    for _ in range(iset.n_steps):
        p.go_next()
    assert finished == [4] and all(e.run == 4 for e in events)


def test_gamification_off_hides_badges(player, iset):
    p, _, _ = player
    off = iset.model_copy(update={"settings": iset.settings.model_copy(update={"hidden_elements": ["gamification"]})})
    p.load(off, 1, run_label="Repetition 1")
    assert not p.badges.isVisible() and not p.text.gamified
    assert p.run_chip.isVisible()              # the run label is always shown


def test_media_outside_media_folder_is_never_loaded(player, iset, tmp_path):
    p, _, _ = player
    from app.models import Media
    step = iset.steps[0]
    # bypass validation to simulate a tampered file: an absolute / parent path must be skipped
    bad = Media.model_construct(type="image", path="../../secret.png", role="part")
    tampered = step.model_copy(update={"media": [bad]})
    assert p._media(tampered, "image") == []
