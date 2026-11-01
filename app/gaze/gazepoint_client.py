"""GazePoint GP3 via the Open Gaze API (TCP, XML lines terminated by CRLF).

Protocol (Open Gaze API Ver. 2.0):
    client -> <SET ID="ENABLE_SEND_POG_FIX" STATE="1" />\\r\\n
    server -> <ACK ID="ENABLE_SEND_POG_FIX" STATE="1" />\\r\\n
    server -> <REC CNT="12" TIME="5.01366" FPOGX="0.48" FPOGY="0.50" FPOGID="1599" FPOGV="1" />\\r\\n

Threading: a QThread reads the socket and timestamps every record on arrival with the app
clock, so a busy GUI thread never distorts gaze timing. Records are then delivered to the
GUI thread via a queued signal.

Timestamps: if the tracker sends TIME, a sample's `t` is TIME + offset, where offset is the
minimum observed (arrival - TIME). This removes network/processing jitter. If TIME jumps
(the tracker resets it after calibration), the offset is re-estimated.
"""
from __future__ import annotations

import re
import socket
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from app import clock
from app.config import GazepointConfig
from app.gaze.source import GazeSource
from app.models import GazeSample

_MSG_RE = re.compile(r"<\s*(\w+)\s*(.*?)\s*/>", re.DOTALL)
_ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
OFFSET_RESET_S = 1.0  # TIME discontinuity larger than this -> re-estimate clock offset


# --- protocol helpers (also used by mock_server) -------------------------------------------
def format_msg(tag: str, msg_id: str, **attrs: object) -> bytes:
    parts = [f'<{tag.upper()} ID="{msg_id.upper()}"']
    parts += [f'{k.upper()}="{v}"' for k, v in attrs.items()]
    return (" ".join(parts) + " />\r\n").encode("ascii")


def parse_messages(buffer: str) -> tuple[list[tuple[str, dict[str, str]]], str]:
    """Parse all complete messages in `buffer`; return (messages, unparsed remainder)."""
    msgs: list[tuple[str, dict[str, str]]] = []
    last = 0
    for m in _MSG_RE.finditer(buffer):
        msgs.append((m.group(1).upper(), dict(_ATTR_RE.findall(m.group(2)))))
        last = m.end()
    rest = buffer[last:]
    if "<" not in rest:
        rest = ""
    else:
        rest = rest[rest.index("<"):]
    return msgs, rest


class ClockMapper:
    """Maps tracker TIME (s) onto the app clock (see module docstring)."""

    def __init__(self) -> None:
        self.offset: Optional[float] = None
        self._last_tracker_t: Optional[float] = None

    def map(self, tracker_t: Optional[float], arrival_t: float) -> float:
        if tracker_t is None:
            return arrival_t
        cand = arrival_t - tracker_t
        jumped = self._last_tracker_t is not None and (
            tracker_t < self._last_tracker_t or tracker_t - self._last_tracker_t > OFFSET_RESET_S)
        if self.offset is None or jumped or abs(cand - self.offset) > OFFSET_RESET_S:
            self.offset = cand
        else:
            self.offset = min(self.offset, cand)
        self._last_tracker_t = tracker_t
        return tracker_t + self.offset


class _Reader(QThread):
    record = Signal(object, float)        # attrs dict, arrival time
    connection = Signal(bool, str)        # connected, message

    def __init__(self, host: str, port: int, enable_commands: list[str], reconnect_s: float) -> None:
        super().__init__()
        self.host, self.port = host, port
        self.enable_commands = enable_commands
        self.reconnect_s = reconnect_s
        self._stop = False

    def stop(self) -> None:
        self._stop = True
        self.wait(3000)

    def run(self) -> None:
        while not self._stop:
            try:
                with socket.create_connection((self.host, self.port), timeout=2.0) as sock:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    for cmd in self.enable_commands:
                        sock.sendall(format_msg("SET", cmd, STATE=1))
                    self.connection.emit(True, f"Connected to {self.host}:{self.port}")
                    self._read_loop(sock)
            except OSError as exc:
                if self._stop:
                    break
                self.connection.emit(False, f"{self.host}:{self.port} – {exc.__class__.__name__}: {exc}")
            if self._stop:
                break
            # interruptible wait before reconnecting
            waited = 0.0
            while waited < self.reconnect_s and not self._stop:
                self.msleep(100)
                waited += 0.1
        self.connection.emit(False, "Stopped")

    def _read_loop(self, sock: socket.socket) -> None:
        sock.settimeout(0.25)
        buf = ""
        while not self._stop:
            try:
                data = sock.recv(65536)
            except socket.timeout:
                continue
            if not data:
                raise ConnectionError("server closed the connection")
            arrival = clock.now()
            buf += data.decode("utf-8", errors="replace")
            msgs, buf = parse_messages(buf)
            for tag, attrs in msgs:
                if tag == "REC":
                    self.record.emit(attrs, arrival)
            if len(buf) > 1 << 20:  # garbage protection
                buf = ""


def _float(v: Optional[str]) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


class GazepointClient(GazeSource):
    name = "gazepoint"

    def __init__(self, cfg: GazepointConfig, host: Optional[str] = None, port: Optional[int] = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.host = host or cfg.host
        self.port = port or cfg.port
        self.f = cfg.fields
        self._reader: Optional[_Reader] = None
        self._clock = ClockMapper()
        self._last_cnt: Optional[int] = None
        self.dropped_records = 0

    def start(self) -> None:
        if self._reader is not None:
            return
        self._reader = _Reader(self.host, self.port, self.cfg.enable_commands, self.cfg.reconnect_s)
        self._reader.record.connect(self._on_record)
        self._reader.connection.connect(self._on_connection)
        self._message = f"Connecting to {self.host}:{self.port} …"
        self._reader.start()

    def stop(self) -> None:
        if self._reader is not None:
            self._reader.stop()
            self._reader = None
        self._set_connected(False, "Stopped")

    def _on_connection(self, connected: bool, message: str) -> None:
        if connected:
            self._clock = ClockMapper()
            self._last_cnt = None
        self._set_connected(connected, message)

    def _on_record(self, attrs: dict[str, str], arrival: float) -> None:
        f = self.f
        if f.counter and f.counter in attrs:
            try:
                cnt = int(attrs[f.counter])
                if self._last_cnt is not None and cnt > self._last_cnt + 1:
                    self.dropped_records += cnt - self._last_cnt - 1
                    self._message = f"Connected to {self.host}:{self.port} – dropped records: {self.dropped_records}"
                self._last_cnt = cnt
            except ValueError:
                pass
        tracker_t = _float(attrs.get(f.time))
        x, y = _float(attrs.get(f.x)), _float(attrs.get(f.y))
        valid = attrs.get(f.valid) == "1" and x is not None and y is not None
        fid = attrs.get(f.fixation_id) if f.fixation_id else None
        self._emit_sample(GazeSample(
            t=self._clock.map(tracker_t, arrival),
            x_norm=x, y_norm=y, valid=valid,
            fixation_id=int(fid) if fid and fid.lstrip("-").isdigit() else None,
            tracker_time=tracker_t,
        ))
