"""Controller builds a RunSummary per repetition from live player events + gaze (headless)."""
import json
from pathlib import Path

from app import clock
from app.config import PROJECT_ROOT, load_config
from app.gaze.source import GazeSource
from app.loop.controller import SessionController, State
from app.models import GazeSample, InstructionSet
from app.llm.mock_client import MockLLMClient
from app.ui.participant_window import ParticipantWindow
from app.ui.setup_screen import SetupChoice

BASE_SET = PROJECT_ROOT / "data/instruction_sets/duplo_tower_base.json"
TEXT = (960 / 1920, 200 / 1080)
IMAGE = (500 / 1920, 600 / 1080)


class ScriptedSource(GazeSource):
    name = "scripted"

    def start(self):
        self._set_connected(True)

    def stop(self):
        self._set_connected(False)

    def push(self, xy, valid=True):
        self._emit_sample(GazeSample(t=clock.now(), x_norm=xy[0], y_norm=xy[1], valid=valid))


def test_two_repetitions_produce_summaries(qapp, tmp_path):
    cfg = load_config()
    cfg.input.debounce_ms = 0
    cfg.paths.sessions = tmp_path
    cfg.loop.feedback_every_n_repetitions = 100   # this test covers plain repetitions only
    cfg.post_survey.enabled = False
    iset = InstructionSet.model_validate_json(BASE_SET.read_text(encoding="utf-8"))
    window = ParticipantWindow(cfg, [(BASE_SET, iset)], lambda pid: False)
    window.resize(1920, 1080)
    window.show()
    player = window.player
    ctl = SessionController(cfg, window, lambda name: MockLLMClient())
    src = ScriptedSource()
    src.start()
    choice = SetupChoice("T000001", BASE_SET, iset, "gaze", "mouse", "mock", calibration=False)
    ctl.start_session(choice, src)

    for rep in (1, 2):
        for i in range(iset.n_steps):
            src.push(TEXT); src.push(TEXT); src.push(IMAGE)
            if rep == 2 and i == 3:
                player.go_back()                 # one Back on step 4 in repetition 2
                src.push(IMAGE)
                player.go_next()                 # finish step 3 again
            player.go_next()
    ctl.end_session()
    window.close()

    assert ctl.state == State.ENDED
    assert [s.repetition for s in ctl.history.all()] == [1, 2]
    assert [s.run for s in ctl.history.all()] == [1, 2]
    r1, r2 = ctl.history.all()
    for s in (r1, r2):
        assert len(s.steps) == iset.n_steps
    assert r1.steps[0].transitions == ["text", "image"]
    assert round(r1.steps[0].dwell_pct["text"]) == 67
    assert r1.totals.total_back_presses == 0
    assert r2.steps[3].back_presses == 1 and r2.totals.total_back_presses == 1
    assert r2.steps[2].transitions[-1] == "image"   # step 3 revisited, extra image sample

    on_disk = json.loads((ctl.logger.dir / "summaries.json").read_text(encoding="utf-8"))
    assert [s["repetition"] for s in on_disk] == [1, 2]
    states = [json.loads(l) for l in (ctl.logger.dir / "events.jsonl").read_text().splitlines()]
    states = [e for e in states if e["kind"] == "state"]
    assert [(e["from_state"], e["to_state"]) for e in states] == [
        ("SETUP", "PLAYING"), ("PLAYING", "PLAYING"), ("PLAYING", "PLAYING"), ("PLAYING", "ENDED")]
    assert states[1]["detail"]["repetition"] == 2
