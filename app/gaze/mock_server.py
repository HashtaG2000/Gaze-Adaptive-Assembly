"""Standalone fake GazePoint server speaking the Open Gaze API (TCP/XML).

Run:   .venv\\Scripts\\python -m app.gaze.mock_server [--port 4343] [--rate 60]

Behaviour
- Answers every <SET ID=".." STATE=".."/> with the matching <ACK .../> and every <GET .../>
  with the current value.
- While ENABLE_SEND_DATA=1, streams <REC .../> records at --rate Hz containing the fields the
  client enabled (CNT, TIME, FPOG*, BPOG*).
- Synthetic gaze cycles through a scripted AOI pattern (AOI centres from config.yaml) with
  small jitter, short blinks (invalid samples) and glances down to the table (off-screen).

The GUI uses MockGazeServer in-process (gaze source "mock"); the script form is for testing
the real client code path against an external process.
"""
from __future__ import annotations

import argparse
import random
import select
import socket
import socketserver
import threading
import time
from dataclasses import dataclass
from typing import Optional

from app.config import AppConfig, load_config
from app.gaze.gazepoint_client import format_msg, parse_messages

# (target, duration_s). "off_screen" = looking down at the bricks.
DEFAULT_PATTERN: list[tuple[str, float]] = [
    ("btn_next", 0.3), ("progress", 0.3), ("text", 1.2), ("image", 1.5), ("video", 2.0),
    ("text", 0.6), ("off_screen", 1.5), ("image", 0.8), ("interaction", 0.4), ("btn_next", 0.5),
]
BLINK_EVERY_S = 3.7
BLINK_DUR_S = 0.15
JITTER = 0.004

FIELD_GROUPS = {
    "ENABLE_SEND_COUNTER": ("CNT",),
    "ENABLE_SEND_TIME": ("TIME",),
    "ENABLE_SEND_POG_FIX": ("FPOGX", "FPOGY", "FPOGS", "FPOGD", "FPOGID", "FPOGV"),
    "ENABLE_SEND_POG_BEST": ("BPOGX", "BPOGY", "BPOGV"),
}


@dataclass
class GazePoint:
    x: float
    y: float
    valid: bool
    fix_id: int
    fix_start: float


class GazeScript:
    """Deterministic (seeded) gaze trajectory as a function of time."""

    def __init__(self, cfg: AppConfig, pattern: list[tuple[str, float]] = DEFAULT_PATTERN, seed: int = 1) -> None:
        W, H = cfg.screen.width, cfg.screen.height
        self.targets: dict[str, tuple[float, float]] = {
            name: ((r.x + r.w / 2) / W, (r.y + r.h / 2) / H) for name, r in cfg.aoi.items()
        }
        self.targets["off_screen"] = (0.5, 1.3)
        self.pattern = pattern
        self.period = sum(d for _, d in pattern)
        self.rng = random.Random(seed)

    def at(self, t: float) -> GazePoint:
        cycle, tc = divmod(t, self.period)
        acc = 0.0
        for i, (target, dur) in enumerate(self.pattern):
            if tc < acc + dur:
                break
            acc += dur
        fix_id = int(cycle) * len(self.pattern) + i + 1
        x, y = self.targets[target]
        blink = (t % BLINK_EVERY_S) < BLINK_DUR_S
        return GazePoint(
            x=x + self.rng.gauss(0, JITTER), y=y + self.rng.gauss(0, JITTER),
            valid=not blink, fix_id=fix_id, fix_start=cycle * self.period + acc,
        )


class _Handler(socketserver.BaseRequestHandler):
    server: "_TCPServer"

    def handle(self) -> None:
        sock: socket.socket = self.request
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        state: dict[str, str] = {}
        buf = ""
        cnt = 0
        interval = 1.0 / self.server.rate_hz
        next_t = time.perf_counter()
        t0 = self.server.t0
        try:
            while not self.server.stopping.is_set():
                timeout = max(0.0, next_t - time.perf_counter())
                readable, _, _ = select.select([sock], [], [], timeout)
                if readable:
                    data = sock.recv(4096)
                    if not data:
                        return
                    buf += data.decode("utf-8", errors="replace")
                    msgs, buf = parse_messages(buf)
                    for tag, attrs in msgs:
                        mid = attrs.get("ID", "").upper()
                        if tag == "SET":
                            state[mid] = attrs.get("STATE", attrs.get("VALUE", "1"))
                            sock.sendall(format_msg("ACK", mid, STATE=state[mid]))
                        elif tag == "GET":
                            sock.sendall(format_msg("ACK", mid, STATE=state.get(mid, "0")))
                    continue
                now = time.perf_counter()
                if now < next_t:
                    continue
                next_t += interval
                if next_t < now:            # fell behind: don't burst
                    next_t = now + interval
                if state.get("ENABLE_SEND_DATA") != "1":
                    continue
                cnt += 1
                t = now - t0
                g = self.server.script.at(t)
                sock.sendall(self._rec(state, cnt, t, g))
        except (ConnectionError, OSError):
            return

    @staticmethod
    def _rec(state: dict[str, str], cnt: int, t: float, g: GazePoint) -> bytes:
        enabled = {f for grp, fields in FIELD_GROUPS.items() if state.get(grp) == "1" for f in fields}
        vals = {
            "CNT": str(cnt), "TIME": f"{t:.5f}",
            "FPOGX": f"{g.x:.5f}", "FPOGY": f"{g.y:.5f}", "FPOGS": f"{g.fix_start:.5f}",
            "FPOGD": f"{max(0.0, t - g.fix_start):.5f}", "FPOGID": str(g.fix_id),
            "FPOGV": "1" if g.valid else "0",
            "BPOGX": f"{g.x:.5f}", "BPOGY": f"{g.y:.5f}", "BPOGV": "1" if g.valid else "0",
        }
        order = ("CNT", "TIME", "FPOGX", "FPOGY", "FPOGS", "FPOGD", "FPOGID", "FPOGV", "BPOGX", "BPOGY", "BPOGV")
        attrs = " ".join(f'{k}="{vals[k]}"' for k in order if k in enabled)
        return f"<REC {attrs} />\r\n".encode("ascii")


class _TCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, addr, rate_hz: int, script: GazeScript) -> None:
        self.rate_hz = rate_hz
        self.script = script
        self.t0 = time.perf_counter()
        self.stopping = threading.Event()
        super().__init__(addr, _Handler)


class MockGazeServer:
    """In-process mock server (background thread). port=0 picks a free port."""

    def __init__(self, host: str = "127.0.0.1", port: int = 4343, rate_hz: int = 60,
                 cfg: Optional[AppConfig] = None) -> None:
        self._server = _TCPServer((host, port), rate_hz, GazeScript(cfg or load_config()))
        self.host, self.port = self._server.server_address[:2]
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "MockGazeServer":
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.1},
                                        name="mock-gazepoint", daemon=True)
        self._thread.start()
        return self

    def shutdown(self) -> None:
        self._server.stopping.set()
        self._server.shutdown()
        self._server.server_close()


def main() -> None:
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=cfg.gaze.mock_server.host)
    ap.add_argument("--port", type=int, default=cfg.gaze.mock_server.port)
    ap.add_argument("--rate", type=int, default=cfg.gaze.mock_server.rate_hz)
    args = ap.parse_args()
    srv = MockGazeServer(args.host, args.port, args.rate, cfg).start()
    print(f"Mock GazePoint server on {srv.host}:{srv.port} at {args.rate} Hz. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
