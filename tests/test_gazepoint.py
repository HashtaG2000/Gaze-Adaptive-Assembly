"""Open Gaze API protocol helpers, clock mapping, and GazepointClient against the mock server."""
import socket

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from app.config import load_config
from app.gaze.gazepoint_client import ClockMapper, GazepointClient, format_msg, parse_messages
from app.gaze.mock_server import GazeScript, MockGazeServer
from app.pipeline.aoi import AoiMap


def test_format_msg():
    assert format_msg("set", "enable_send_data", STATE=1) == b'<SET ID="ENABLE_SEND_DATA" STATE="1" />\r\n'


def test_parse_messages_handles_partial_lines():
    buf = '<ACK ID="ENABLE_SEND_DATA" STATE="1" />\r\n<REC CNT="5" FPOGX="0.5" FPOGY="0.25" FPOGV="1" />\r\n<REC CNT="6" FPO'
    msgs, rest = parse_messages(buf)
    assert msgs[0] == ("ACK", {"ID": "ENABLE_SEND_DATA", "STATE": "1"})
    assert msgs[1] == ("REC", {"CNT": "5", "FPOGX": "0.5", "FPOGY": "0.25", "FPOGV": "1"})
    assert rest == '<REC CNT="6" FPO'
    msgs, rest = parse_messages(rest + 'GX="0.1" />\r\n')
    assert msgs == [("REC", {"CNT": "6", "FPOGX": "0.1"})] and rest == ""


def test_clock_mapper_min_filter_and_reset():
    cm = ClockMapper()
    assert cm.map(10.0, 100.05) == pytest.approx(100.05)       # first: offset 90.05
    assert cm.map(10.1, 100.12) == pytest.approx(100.12)       # smaller delay -> offset 90.02
    assert cm.map(10.2, 100.30) == pytest.approx(100.22)       # jittery arrival smoothed
    assert cm.map(0.5, 101.0) == pytest.approx(101.0)          # TIME reset (calibration)
    assert cm.map(None, 55.0) == 55.0


def test_gaze_script_visits_aois():
    cfg = load_config()
    amap = AoiMap.from_config(cfg)
    script = GazeScript(cfg)
    seen = {amap.map_point(g.x, g.y, g.valid) for g in (script.at(i / 60) for i in range(int(script.period * 60)))}
    assert {"text", "image", "video", "btn_next", "off_screen", "invalid"} <= seen


def _wait(cond, timeout_ms=4000):
    loop = QEventLoop()
    t = QTimer(); t.setInterval(20)
    t.timeout.connect(lambda: loop.quit() if cond() else None)
    QTimer.singleShot(timeout_ms, loop.quit)
    t.start()
    loop.exec()
    t.stop()
    return cond()


def test_client_receives_samples_from_mock_server(qapp):
    cfg = load_config()
    srv = MockGazeServer(port=0, rate_hz=60, cfg=cfg).start()
    try:
        client = GazepointClient(cfg.gaze.gazepoint, host=srv.host, port=srv.port)
        samples = []
        client.sample.connect(samples.append)
        client.start()
        assert _wait(lambda: len(samples) >= 90), f"only {len(samples)} samples"
        st = client.status()
        assert st.connected and st.receiving
        assert 40 <= st.rate_hz <= 75
        valid = [s for s in samples if s.valid]
        assert len(valid) > 0.8 * len(samples)
        assert all(s.tracker_time is not None and s.fixation_id is not None for s in samples)
        ts = [s.t for s in samples]
        assert ts == sorted(ts)
        assert client.dropped_records == 0
        client.stop()
        assert not client.status().connected
    finally:
        srv.shutdown()


def test_client_reports_disconnected_without_server(qapp):
    cfg = load_config()
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()  # free port
    client = GazepointClient(cfg.gaze.gazepoint, host="127.0.0.1", port=port)
    msgs = []
    client.status_changed.connect(lambda st: msgs.append(st))
    client.start()
    assert _wait(lambda: len(msgs) >= 1)
    assert not client.status().connected and not client.status().receiving
    client.stop()
