"""Generate placeholder media for every media path referenced by an instruction set.

Usage (from the project root):
    .venv\\Scripts\\python -m tools.make_placeholders [data/instruction_sets/duplo_tower_base.json]

Images: PNG with role-coloured background and a label.
Videos: short looping MP4 (moving bar + label) written via QtMultimedia, so no ffmpeg is needed.
Existing files are never overwritten (real media stay safe).
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEventLoop, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter
from PySide6.QtMultimedia import (
    QMediaCaptureSession, QMediaFormat, QMediaRecorder, QVideoFrame, QVideoFrameInput,
)

from app.config import PROJECT_ROOT, load_config
from app.core.files import write_json_atomic
from app.models import InstructionSet

W, H = 880, 560  # matches the image/video AOI size in config.yaml
ROLE_COLOURS = {"part": "#DCE8F7", "position": "#E3F2E1", "method": "#F7EBDC"}
VIDEO_FPS = 25
VIDEO_SECONDS = 3


def _frame(label: str, role: str, progress: float | None) -> QImage:
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(QColor(ROLE_COLOURS.get(role, "#EEEEEE")))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor("#333333"))
    p.drawRect(0, 0, W - 1, H - 1)
    p.setFont(QFont("Segoe UI", 28, QFont.Weight.Bold))
    p.drawText(QRect(0, 0, W, H - 80), Qt.AlignmentFlag.AlignCenter, f"PLACEHOLDER\n{label}")
    p.setFont(QFont("Segoe UI", 18))
    p.drawText(QRect(0, H - 80, W, 60), Qt.AlignmentFlag.AlignCenter, f"Role: {role}")
    if progress is not None:
        bar_w = 120
        x = int((W - bar_w) * progress)
        p.fillRect(x, H - 20, bar_w, 12, QColor("#1565C0"))
    p.end()
    return img


def make_image(path: Path, label: str, role: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not _frame(label, role, None).save(str(path)):
        raise RuntimeError(f"could not write {path}")


def make_video(path: Path, label: str, role: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = QMediaFormat()
    fmt.setFileFormat(QMediaFormat.FileFormat.MPEG4)
    fmt.setVideoCodec(QMediaFormat.VideoCodec.H264)

    session = QMediaCaptureSession()
    frame_input = QVideoFrameInput()
    recorder = QMediaRecorder()
    session.setVideoFrameInput(frame_input)
    session.setRecorder(recorder)
    recorder.setMediaFormat(fmt)
    recorder.setVideoResolution(W, H)
    recorder.setVideoFrameRate(VIDEO_FPS)
    recorder.setQuality(QMediaRecorder.Quality.NormalQuality)
    recorder.setOutputLocation(QUrl.fromLocalFile(str(path)))

    # autoStop: recorder finalises the file itself after the end-of-stream (null) frame,
    # so no frames are lost by stopping too early.
    recorder.setAutoStop(True)

    n_frames = VIDEO_FPS * VIDEO_SECONDS
    state = {"i": 0, "recording": False, "eos_sent": False}
    loop = QEventLoop()
    errors: list[str] = []

    # Frames are sent paced in real time (one per tick): sending them in a burst makes the
    # encoder drop most of them.
    ticker = QTimer()
    ticker.setInterval(1000 // VIDEO_FPS)

    def push() -> None:
        if not state["recording"] or state["eos_sent"]:
            return
        if state["i"] < n_frames:
            i = state["i"]
            frame = QVideoFrame(_frame(label, role, i / (n_frames - 1)))
            frame.setStartTime(int(i * 1_000_000 / VIDEO_FPS))
            frame.setEndTime(int((i + 1) * 1_000_000 / VIDEO_FPS))
            if frame_input.sendVideoFrame(frame):
                state["i"] += 1
        elif frame_input.sendVideoFrame(QVideoFrame()):  # end of stream
            state["eos_sent"] = True
            ticker.stop()

    def on_state(s: QMediaRecorder.RecorderState) -> None:
        if s == QMediaRecorder.RecorderState.RecordingState:
            state["recording"] = True
            ticker.start()
        elif s == QMediaRecorder.RecorderState.StoppedState:
            loop.quit()

    def on_error(_err, msg: str) -> None:
        errors.append(msg)
        loop.quit()

    ticker.timeout.connect(push)
    recorder.recorderStateChanged.connect(on_state)
    recorder.errorOccurred.connect(on_error)
    recorder.record()
    QTimer.singleShot(60_000, loop.quit)  # safety timeout
    loop.exec()

    if errors:
        raise RuntimeError(f"video {path}: {errors}")
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"video {path} was not written")


def main(argv: list[str]) -> int:
    app = QGuiApplication.instance() or QGuiApplication(argv)  # noqa: F841
    cfg = load_config()
    media_root = cfg.resolve(cfg.paths.media)
    set_path = Path(argv[1]) if len(argv) > 1 else PROJECT_ROOT / "data/instruction_sets/duplo_tower_base.json"
    iset = InstructionSet.model_validate_json(set_path.read_text(encoding="utf-8"))

    for step in iset.steps:
        for m in step.media:
            out = media_root / m.path
            if out.exists():
                print(f"skip (exists): {m.path}")
                continue
            label = f"Step {step.index + 1}: {step.part_id}"
            if m.type == "image":
                make_image(out, label, m.role)
            else:
                make_video(out, label, m.role)
            print(f"wrote: {m.path}")

    # reference images for the camera check: the board after each step (drawn, top view)
    changed = False
    for step in iset.steps:
        rel = step.reference_image or f"references/{iset.id}/step_{step.index + 1:02d}.png"
        out = media_root / rel
        if not out.exists():
            make_reference(out, iset, step.index)
            print(f"wrote: {rel}")
        if step.reference_image is None:
            step.reference_image = rel
            changed = True
    if changed:   # link the references in the set file (existing links are never changed)
        write_json_atomic(set_path, iset.model_dump(mode="json", exclude_none=True))
        print(f"linked reference images in {set_path.name}")
    return 0


BRICK_COLOURS = {"red": "#d6342c", "yellow": "#f2c230", "blue": "#2f6fd1", "green": "#3a9d4a", "white": "#f4f4f4"}
# placeholder board positions (fraction of the board): x, y, w, h
POSITION_RECTS = {"P1": (0.30, 0.55, 0.40, 0.20), "P2": (0.30, 0.33, 0.19, 0.20), "P3": (0.51, 0.33, 0.19, 0.20),
                  "P4": (0.30, 0.11, 0.40, 0.20)}


def make_reference(path: Path, iset: InstructionSet, upto: int) -> None:
    """Top view of the board with every brick placed up to and including step `upto`."""
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(QColor("#cfc8b8"))                        # table
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    board = QRect(140, 40, W - 280, H - 80)
    p.fillRect(board, QColor("#5aa469"))               # base plate
    for s in iset.steps[: upto + 1]:
        for pos in s.position_ids:
            fx, fy, fw, fh = POSITION_RECTS.get(pos, (0.05, 0.05, 0.15, 0.15))
            r = QRect(int(board.x() + fx * board.width()), int(board.y() + fy * board.height()),
                      int(fw * board.width()), int(fh * board.height()))
            colour = BRICK_COLOURS.get(s.part_id.split("_")[0], "#888888")
            p.fillRect(r, QColor(colour))
            p.setPen(QColor("#222222"))
            p.drawRect(r)
    p.setPen(QColor("#222222"))
    p.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
    p.drawText(QRect(0, H - 36, W, 30), Qt.AlignmentFlag.AlignCenter,
               f"REFERENCE (placeholder) - board after step {upto + 1}")
    p.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
