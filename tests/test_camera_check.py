"""Camera check: photo after every step, comparison with the reference image, automatic verdicts,
observer review, metrics updates, logging/archive/export, clients and validation."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app.camera.source import CameraSource, MockCamera, NoCamera
from app.config import load_config
from app.llm.client import LLMError
from app.loop.controller import State
from app.pipeline.archive import list_sessions, load_session
from app.pipeline.export import export_session
from app.pipeline.verdicts import VerdictBook
from app.vision.clients import MockVision, OllamaVision, block_similarity
from app.vision.verify import load_jpeg, validate_verification
from tests.test_full_loop import BASE, ScriptedLLM, assess, make_cfg, play, session, wait_until  # noqa: F401

REF_DIR = Path(__file__).resolve().parent.parent / "data/media/references/duplo_tower"


def camera_session(session, tmp_path, error_every=0, threshold=0.8, checker="mock", **kw):
    cfg = make_cfg(tmp_path)
    cfg.camera.settle_ms = 0
    cfg.camera.mock_error_every_n = error_every
    cfg.verification.threshold = threshold
    vision = MockVision(cfg) if checker == "mock" else checker
    return session(ScriptedLLM([]), camera=MockCamera(cfg.camera), checker=vision, cfg=cfg, **kw)


def all_checked(ctl, n):
    return lambda: len(ctl.verifications) >= n and ctl.checks_pending == 0


# --- end to end -------------------------------------------------------------------------------------

def test_every_step_is_photographed_and_checked(session, tmp_path):
    ctl, win, src = camera_session(session, tmp_path, error_every=3)
    play(ctl, win, src)
    wait_until(all_checked(ctl, BASE.n_steps), 15000)
    recs = sorted(ctl.verifications.values(), key=lambda r: r.id)
    assert [r.step_index for r in recs] == list(range(BASE.n_steps))
    for r in recs:
        assert (ctl.logger.dir / r.photo).is_file() and r.reference and r.client == "mock"
    errors = [r.step_index for r in recs if r.verdict == "error"]
    assert errors == [2, 5]                                   # every 3rd photo shows a misplaced brick
    assert all(not r.needs_review for r in recs)              # all confident -> applied automatically
    summary = ctl.history.get(1)                              # metrics updated after the run ended
    assert summary.totals.total_observer_errors == 2
    assert [m.observer_errors for m in summary.steps] == [0, 0, 1, 0, 0, 1, 0, 0]
    on_disk = json.loads((ctl.logger.dir / "summaries.json").read_text(encoding="utf-8"))
    assert on_disk[0]["totals"]["total_observer_errors"] == 2
    sources = {json.loads(line)["source"] for line in (ctl.logger.dir / "observations.jsonl").read_text().splitlines()}
    assert sources == {"camera"}


def test_low_confidence_goes_to_review_and_observer_decides(session, tmp_path):
    ctl, win, src = camera_session(session, tmp_path, threshold=1.0)   # nothing is confident enough
    play(ctl, win, src)
    wait_until(all_checked(ctl, BASE.n_steps), 15000)
    queue = ctl.review_queue
    assert len(queue) == BASE.n_steps and ctl.history.get(1).totals.total_observer_errors == 0
    reviewed = ctl.review(queue[3].id, "error")
    assert reviewed.reviewed == "error" and not reviewed.needs_review
    assert len(ctl.review_queue) == BASE.n_steps - 1
    assert ctl.history.get(1).steps[3].observer_errors == 1
    assert ctl.review(queue[3].id, "correct").reviewed == "correct"    # the observer may change their mind
    assert ctl.history.get(1).steps[3].observer_errors == 0
    assert ctl.review(9999, "error") is None and ctl.review(queue[0].id, "maybe") is None


def test_observer_and_review_outrank_the_camera(session, tmp_path):
    ctl, win, src = camera_session(session, tmp_path, error_every=1)   # every photo "wrong"
    ctl.observe("correct")                                             # observer: step 1 correct
    play(ctl, win, src)
    wait_until(all_checked(ctl, BASE.n_steps), 15000)
    s = ctl.history.get(1)
    assert s.steps[0].observer_errors == 0 and s.steps[0].observer_correct == 1   # observer kept
    assert all(m.observer_errors == 1 for m in s.steps[1:])                         # camera applied
    second = sorted(ctl.verifications.values(), key=lambda r: r.id)[1]
    ctl.review(second.id, "correct")
    assert ctl.history.get(1).steps[1].observer_errors == 0


def test_missing_reference_or_checker_off_needs_review(session, tmp_path):
    ctl, win, src = camera_session(session, tmp_path, checker=None)    # camera on, checker off
    play(ctl, win, src)
    wait_until(all_checked(ctl, BASE.n_steps), 15000)
    assert all(r.needs_review and r.client == "off" for r in ctl.verifications.values())
    ctl.force_end()

    no_ref = BASE.model_copy(update={"steps": [s.model_copy(update={"reference_image": None}) for s in BASE.steps]})
    ctl2, win2, src2 = camera_session(session, tmp_path)
    ctl2.current_set = no_ref
    play(ctl2, win2, src2)
    wait_until(all_checked(ctl2, BASE.n_steps), 15000)
    assert all(r.needs_review and "no reference image" in (r.error or "") for r in ctl2.verifications.values())


class _BrokenCamera(CameraSource):
    name = "broken"

    def capture(self, request_id, path, hint=None):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.failed.emit(request_id, "USB unplugged"))


def test_camera_failure_is_recorded_for_review(session, tmp_path):
    cfg = make_cfg(tmp_path)
    ctl, win, src = session(ScriptedLLM([]), camera=_BrokenCamera(), checker=MockVision(cfg), cfg=cfg)
    win.player.go_next()
    wait_until(lambda: len(ctl.verifications) == 1)
    rec = next(iter(ctl.verifications.values()))
    assert rec.needs_review and rec.photo == "" and "USB unplugged" in rec.error


def test_session_end_waits_for_running_checks_then_closes(session, tmp_path):
    ctl, win, src = camera_session(session, tmp_path)
    play(ctl, win, src)
    ctl.force_end()                                           # checks may still be running
    assert ctl.state == State.ENDED
    wait_until(lambda: ctl.logger.closed, 20000)
    lines = (ctl.logger.dir / "verifications.jsonl").read_text().splitlines()
    assert len(lines) == BASE.n_steps                         # nothing lost at the end


def test_camera_off_takes_no_photos(session, tmp_path):
    ctl, win, src = session(ScriptedLLM([]))
    assert isinstance(ctl.camera, NoCamera)
    play(ctl, win, src)
    assert not ctl.verifications and not (ctl.logger.dir / "photos").exists()


# --- archive, export, workspace ---------------------------------------------------------------------

def test_reviews_survive_archive_and_export(session, tmp_path):
    ctl, win, src = camera_session(session, tmp_path, threshold=1.0)
    play(ctl, win, src)
    wait_until(all_checked(ctl, BASE.n_steps), 15000)
    first = ctl.review_queue[0]
    ctl.review(first.id, "error")
    ctl.force_end()
    wait_until(lambda: ctl.logger.closed, 20000)
    archived = load_session(list_sessions(tmp_path)[0].folder)
    assert len(archived.verifications) == BASE.n_steps          # the review line replaced the original
    assert next(v for v in archived.verifications if v.id == first.id).reviewed == "error"
    assert list_sessions(tmp_path)[0].last_photo is not None    # thumbnail for Recordings
    out = export_session(archived.dir)
    import pandas as pd
    df = pd.read_csv(out / "verifications.csv")
    assert len(df) == BASE.n_steps and (df["reviewed"] == "error").sum() == 1


def test_workspace_shows_checks_and_reviews_from_the_live_view(session, tmp_path):
    from app.core.repository import ContentRepository
    from app.ui.workspace import Workspace
    cfg = make_cfg(tmp_path)
    ctl, win, src = camera_session(session, tmp_path, threshold=1.0)
    ws = Workspace(cfg, ContentRepository(cfg))
    ws.attach(ctl)
    play(ctl, win, src)
    wait_until(all_checked(ctl, BASE.n_steps), 15000)
    assert ws.live.queue.count() == BASE.n_steps
    ws.live.show_check(ctl.review_queue[0].id, follow=False)
    assert ws.live.pair.photo.pixmap is not None and ws.live.pair.reference.pixmap is not None
    ws.live.btn_check_err.click()                              # observer decides from the live view
    assert ws.live.queue.count() == BASE.n_steps - 1
    tabs = ws.visualizations.tabs
    assert tabs.check_table.rowCount() == BASE.n_steps
    stats = tabs.check_stats.text()
    assert "agreed with the observer in 0 of 1 reviewed" in stats and "7 waiting for review" in stats
    ws._refresh_status()
    assert ws.check_chip.text() == f"{BASE.n_steps - 1} to review"
    ctl.force_end()            # closing a Workspace with a running session asks first (modal dialog)
    ws.close()


# --- pieces ---------------------------------------------------------------------------------------

def test_verdict_book_priorities():
    b = VerdictBook()
    assert b.set(1, 0, "error", "camera")
    assert b.set(1, 0, "correct", "observer")
    assert not b.set(1, 0, "error", "camera")                 # camera never overrides the observer
    assert b.set(1, 0, "error", "review")
    assert not b.set(1, 0, "correct", "observer")
    assert b.for_run(1) == [(0, "error")] and b.for_run(2) == []
    with pytest.raises(ValueError):
        b.set(1, 1, "maybe", "camera")


@pytest.mark.parametrize("raw,ok", [
    ('{"verdict": "correct", "confidence": 0.9, "reason": "fine"}', True),
    ('```json\n{"verdict": "error", "confidence": 0.95, "reason": "brick missing"}\n```', True),
    ('{"verdict": "correct", "confidence": "0.9", "reason": "x"}', False),      # strict: no string numbers
    ('{"verdict": "maybe", "confidence": 0.9, "reason": "x"}', False),
    ('{"verdict": "correct", "confidence": 1.5, "reason": "x"}', False),
    ('{"verdict": "correct", "confidence": 0.9}', False),
    ('{"verdict": "correct", "confidence": 0.9, "reason": "x", "extra": 1}', False),
    ("not json", False), ("", False), (None, False),
])
def test_validate_verification(raw, ok):
    res, err = validate_verification(raw)
    assert (res is not None) == ok and (err is None) == ok


def test_block_similarity_and_image_loading(tmp_path):
    a = load_jpeg(REF_DIR / "step_04.png", 512)
    b = load_jpeg(REF_DIR / "step_08.png", 512)
    assert block_similarity(a, a) > 0.99 and block_similarity(a, b) < 0.9
    with pytest.raises(LLMError):
        load_jpeg(tmp_path / "missing.png", 512)
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"not an image")
    with pytest.raises(LLMError):
        load_jpeg(junk, 512)


class _FakeOllama(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):  # noqa: N802
        self.seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        body = json.dumps({"message": {"content": '{"verdict": "correct", "confidence": 0.9, "reason": "ok"}'}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_ollama_vision_sends_both_images_and_schema():
    srv = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = load_config()
        cfg.llm.ollama.port = srv.server_port
        client = OllamaVision(cfg)
        raw = client.compare("PROMPT", {"type": "object"}, b"ref-bytes", b"photo-bytes")
        assert validate_verification(raw)[0].verdict == "correct"
        sent = _FakeOllama.seen[-1]
        assert sent["model"] == cfg.verification.ollama_model and sent["format"] == {"type": "object"}
        assert len(sent["messages"][0]["images"]) == 2 and "PROMPT" in sent["messages"][0]["content"]
    finally:
        srv.shutdown()
        srv.server_close()
