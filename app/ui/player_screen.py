"""P2 / P5 - Instruction Player (participant-facing).

Every element has a FIXED rectangle from config.yaml (the AOIs). Rectangles are defined on a
1920x1080 canvas and scaled to the actual widget size, so normalised gaze coordinates map to
the same AOIs regardless of DPI scaling. Hidden elements leave their rectangle empty (or a
subtle "removed per participant request" placeholder); nothing else moves.

Progress row: "Step X of N | <title>", run chip ("Repetition N" or "Trial run"), and with
gamification the streak and level chips. Text AOI: with gamification a guide avatar with a
speech bubble, otherwise a plain card with the step number.

Signals:
    step_event(StepEvent)       every start / end / back
    repetition_finished(int)    run number, after "Next" on the last step
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEventLoop, QPointF, QRect, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QKeyEvent, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QStackedLayout, QVBoxLayout, QWidget

from app import clock
from app.config import AppConfig
from app.core.files import LRUCache, UnsafePathError, safe_join
from app.i18n import section, t
from app.models import InstructionSet, Step, StepEvent
from app.ui.icons import Chip, pixmap, scale_button_icons, set_button_icon
from app.ui.style import (
    ACCENT, ACCENT_DARK, AMBER_100, AMBER_300, AMBER_400, AMBER_500, AMBER_800, AMBER_900, BLUE_100, BLUE_300,
    BLUE_700, BLUE_900, C, EMERALD_100, EMERALD_300, EMERALD_400, EMERALD_600, EMERALD_700, EMERALD_800,
    FONT_FAMILY, FONT_NAME, MONO_FAMILY, SLATE_100, SLATE_200, SLATE_300, SLATE_400, SLATE_50, SLATE_500,
    SLATE_700, SLATE_800, SLATE_900,
)

PLAYER_BG = "#ffffff"
FONT_PX = {"progress": 28, "text": 40, "button": 32, "placeholder": 22, "chip": 21}


def _key_from_name(name: Optional[str]) -> Optional[int]:
    if not name:
        return None
    seq = QKeySequence(name)
    if seq.count() == 0:
        raise ValueError(f"unknown key name in config: {name!r}")
    return seq[0].key().value if hasattr(seq[0].key(), "value") else int(seq[0].key())


def _font(px: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(FONT_NAME)
    f.setPixelSize(max(8, px))
    f.setWeight(weight)
    return f


def _fit_font(text: str, rect: QRectF, max_px: int, weight: QFont.Weight, min_px: int = 14) -> QFont:
    """Largest font (<= max_px) at which the word-wrapped text fits into rect."""
    flags = int(Qt.TextFlag.TextWordWrap)
    px = max_px
    while px > min_px:
        f = _font(px, weight)
        if QFontMetrics(f).boundingRect(rect.toRect(), flags, text).height() <= rect.height():
            return f
        px -= 2
    return _font(min_px, weight)


# --- text AOI ------------------------------------------------------------------------------------

class TextArea(QWidget):
    """Instruction text. Plain card, or (gamified) the speaking guide with a speech bubble."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = ""
        self._step_no = 1
        self._gamified = False
        self._scale = 1.0
        self._phase = 0.0
        self._helmet: dict[tuple, QPixmap] = {}     # icon pixmaps (cheer star)
        self._speak_until = 0.0
        self._speak_s = 1.6
        self._cheer = ""
        self._cheer_until = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._tick)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        self._text = text
        self.update()

    def text(self) -> str:
        return self._text

    def set_step(self, step_no: int) -> None:
        self._step_no = step_no
        self._speak_until = clock.now() + self._speak_s     # the guide "says" the new step
        self.update()

    def set_speak_duration(self, seconds: float) -> None:
        self._speak_s = max(0.0, seconds)

    def cheer(self, text: str, seconds: float = 2.5) -> None:
        """Short praise tag on the bubble (e.g. streak milestones)."""
        self._cheer, self._cheer_until = text[:40], clock.now() + seconds
        self.update()

    def set_gamified(self, on: bool) -> None:
        self._gamified = on
        self._sync_timer()
        self.update()

    @property
    def gamified(self) -> bool:
        return self._gamified

    def set_scale(self, scale: float) -> None:
        self._scale = scale

    def showEvent(self, e) -> None:
        self._sync_timer()
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)

    def _sync_timer(self) -> None:
        if self._gamified and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()

    def _tick(self) -> None:
        self._phase += 0.4
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self._gamified:
            self._paint_guide(p, r, self._scale)
        else:
            self._paint_plain(p, r, self._scale)
        p.end()

    def _number_circle(self, p: QPainter, c: QPointF, d: float, s: float, filled: bool) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(ACCENT if filled else BLUE_100))
        p.drawEllipse(c, d / 2, d / 2)
        p.setPen(QColor("#ffffff" if filled else BLUE_700))
        p.setFont(_font(round(d * 0.45), QFont.Weight.Bold))
        p.drawText(QRectF(c.x() - d / 2, c.y() - d / 2, d, d), Qt.AlignmentFlag.AlignCenter, str(self._step_no))

    def _paint_plain(self, p: QPainter, r: QRectF, s: float) -> None:
        p.setPen(QPen(QColor(SLATE_200), 2))
        p.setBrush(QColor(SLATE_50))
        p.drawRoundedRect(r, 16 * s, 16 * s)
        d = 64 * s
        c = QPointF(r.left() + 36 * s + d / 2, r.center().y())
        self._number_circle(p, c, d, s, filled=False)
        text_rect = QRectF(c.x() + d / 2 + 28 * s, r.top() + 14 * s, r.right() - c.x() - d / 2 - 56 * s, r.height() - 28 * s)
        p.setPen(QColor(SLATE_800))
        p.setFont(_fit_font(self._text, text_rect, round(40 * s), QFont.Weight.DemiBold))
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                   self._text)

    def _paint_guide(self, p: QPainter, r: QRectF, s: float) -> None:
        """The guide: a builder with a Duplo-yellow hard hat who SAYS the step (speech bubble)."""
        caption_h = 26 * s
        d = min(r.height() - caption_h - 6 * s, 124 * s)
        av = QRectF(r.left() + 8 * s, r.top() + (r.height() - d - caption_h) / 2, d, d)
        self._paint_builder(p, av)
        p.setPen(QColor(SLATE_500))
        p.setFont(_font(round(18 * s), QFont.Weight.Bold))
        p.drawText(QRectF(av.left() - 20 * s, av.bottom() + 2 * s, d + 40 * s, caption_h),
                   Qt.AlignmentFlag.AlignCenter, t("player.guide"))

        # speech bubble; its tail points at the guide's mouth
        bx = av.right() + 30 * s
        bubble = QRectF(bx, r.top() + 6 * s, r.right() - bx - 4 * s, r.height() - 12 * s)
        mouth_y = av.top() + av.height() * 0.66
        path = QPainterPath()
        path.addRoundedRect(bubble, 22 * s, 22 * s)
        tail = QPainterPath()
        tail.moveTo(bubble.left() + 2, mouth_y - 18 * s)
        tail.quadTo(bubble.left() - 10 * s, mouth_y - 2 * s, av.right() - 4 * s, mouth_y + 6 * s)
        tail.quadTo(bubble.left() - 4 * s, mouth_y + 8 * s, bubble.left() + 2, mouth_y + 14 * s)
        tail.closeSubpath()
        path = path.united(tail)
        p.setPen(QPen(QColor(SLATE_300), 2.5 * s))
        p.setBrush(QColor("#ffffff"))
        p.drawPath(path)

        nd = 54 * s
        nc = QPointF(bubble.left() + 30 * s + nd / 2, bubble.center().y())
        self._number_circle(p, nc, nd, s, filled=True)
        text_rect = QRectF(nc.x() + nd / 2 + 22 * s, bubble.top() + 10 * s,
                           bubble.right() - nc.x() - nd / 2 - 50 * s, bubble.height() - 20 * s)
        p.setPen(QColor(SLATE_800))
        p.setFont(_fit_font(self._text, text_rect, round(36 * s), QFont.Weight.DemiBold))
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                   self._text)
        if self._cheer and clock.now() < self._cheer_until:
            self._paint_cheer(p, bubble, s)

    def _paint_cheer(self, p: QPainter, bubble: QRectF, s: float) -> None:
        """Small tag on the bubble's top edge (streak milestones) - inside the text AOI."""
        f = _font(round(19 * s), QFont.Weight.Bold)
        p.setFont(f)
        w = QFontMetrics(f).horizontalAdvance(self._cheer) + 56 * s
        tag = QRectF(bubble.right() - w - 28 * s, bubble.top() - 4 * s, w, 34 * s)
        p.setPen(QPen(QColor(EMERALD_300), 2 * s))
        p.setBrush(QColor(EMERALD_100))
        p.drawRoundedRect(tag, 17 * s, 17 * s)
        px = int(20 * s)
        p.drawPixmap(int(tag.left() + 12 * s), int(tag.center().y() - px / 2), self._icon("star", AMBER_500, px))
        p.setPen(QColor(EMERALD_800))
        p.drawText(tag.adjusted(36 * s, 0, -8 * s, 0), Qt.AlignmentFlag.AlignCenter, self._cheer)

    def _icon(self, key: str, colour: str, px: int) -> QPixmap:
        k = (key, colour, px)
        if k not in self._helmet:
            self._helmet[k] = pixmap(key, colour, px)
        return self._helmet[k]

    def _paint_builder(self, p: QPainter, box: QRectF) -> None:
        """Friendly builder (Duplo-yellow hard hat with a stud). Eyes look at the participant and
        blink; while speaking the mouth moves, otherwise it smiles."""
        u = box.width() / 100.0
        x0, y0 = box.left(), box.top()
        clip = QPainterPath()
        clip.addEllipse(box)
        p.save()
        p.setClipPath(clip)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(BLUE_100))
        p.drawEllipse(box)
        # body / shirt with strap
        p.setBrush(QColor(ACCENT))
        p.drawRoundedRect(QRectF(x0 + 18 * u, y0 + 76 * u, 64 * u, 40 * u), 20 * u, 20 * u)
        p.setBrush(QColor(ACCENT_DARK))
        p.drawRect(QRectF(x0 + 46 * u, y0 + 78 * u, 8 * u, 30 * u))
        # neck + head
        skin = QColor("#f6c9a0")
        p.setBrush(skin)
        p.drawRect(QRectF(x0 + 43 * u, y0 + 64 * u, 14 * u, 14 * u))
        p.drawEllipse(QRectF(x0 + 27 * u, y0 + 28 * u, 46 * u, 44 * u))
        # hard hat with brim and stud
        p.setBrush(QColor(AMBER_400))
        hat = QPainterPath()
        hat.moveTo(x0 + 26 * u, y0 + 43 * u)
        hat.cubicTo(x0 + 26 * u, y0 + 17 * u, x0 + 74 * u, y0 + 17 * u, x0 + 74 * u, y0 + 43 * u)
        hat.closeSubpath()
        p.drawPath(hat)
        p.setBrush(QColor(AMBER_500))
        p.drawRoundedRect(QRectF(x0 + 20 * u, y0 + 40 * u, 60 * u, 7 * u), 3 * u, 3 * u)
        p.drawRoundedRect(QRectF(x0 + 43 * u, y0 + 15 * u, 14 * u, 8 * u), 2 * u, 2 * u)
        # cheeks
        cheek = QColor("#f19c8a")
        cheek.setAlpha(110)
        p.setBrush(cheek)
        for cx in (33, 61):
            p.drawEllipse(QRectF(x0 + cx * u, y0 + 58 * u, 7 * u, 5 * u))
        # eyes: looking toward the bubble (right), blinking now and then
        blink = (self._phase % (2 * math.pi * 3)) < 0.35
        p.setBrush(QColor("#ffffff"))
        for ex in (41, 58):
            p.drawEllipse(QRectF(x0 + (ex - 5) * u, y0 + 49 * u, 10 * u, 9 * u if not blink else 2 * u))
        if not blink:
            p.setBrush(QColor(SLATE_900))
            for ex in (41, 58):
                p.drawEllipse(QRectF(x0 + (ex - 0.5) * u, y0 + 51 * u, 5 * u, 5 * u))
        # mouth: talking (opening and closing) while speaking, a smile otherwise
        if clock.now() < self._speak_until:
            open_ = 1.5 + 4.5 * abs(math.sin(self._phase * 2.6))
            p.setBrush(QColor("#7a3b2a"))
            p.drawEllipse(QRectF(x0 + 44.5 * u, y0 + 63 * u - open_ * u / 2, 11 * u, open_ * u))
        else:
            p.setPen(QPen(QColor("#9a5b3c"), 2.4 * u, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(x0 + 42 * u, y0 + 57 * u, 16 * u, 10 * u), 200 * 16, 140 * 16)
        p.restore()
        p.setPen(QPen(QColor(C["blue_200"]), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(box)


# --- placeholders for hidden elements -------------------------------------------------------------

class HiddenPlaceholder(QWidget):
    """Dashed box "<element> removed per participant request" (config.ui.show_hidden_placeholder)."""

    def __init__(self, element: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.element = element
        self._scale = 1.0
        self.hide()

    def set_scale(self, s: float) -> None:
        self._scale = s

    def paintEvent(self, _e) -> None:
        s = self._scale
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setPen(QPen(QColor(SLATE_300), 2.5 * s, Qt.PenStyle.DashLine))
        p.setBrush(QColor(SLATE_50))
        p.drawRoundedRect(r, 18 * s, 18 * s)
        px = int(min(64 * s, r.height() * 0.3))
        key = "video_off" if self.element == "video" else "hidden"
        if px >= 12:
            p.drawPixmap(int(r.center().x() - px / 2), int(r.center().y() - px - 6 * s), pixmap(key, SLATE_300, px))
        p.setPen(QColor(SLATE_400))
        p.setFont(_font(round(22 * s)))
        p.drawText(QRectF(r.left(), r.center().y() + 4 * s, r.width(), 40 * s), Qt.AlignmentFlag.AlignCenter,
                   t(f"player.hidden.{self.element}"))
        p.end()


# --- image / video AOIs ----------------------------------------------------------------------------

class ImageArea(QWidget):
    """One or more images side by side in a rounded card, each with a small role label.

    Decoded pixmaps live in an LRU cache, so a step change never reads from disk.
    """

    def __init__(self, cache_size: int = 64, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cache: LRUCache[Optional[QPixmap]] = LRUCache(cache_size)
        self._items: list[tuple[str, str, Optional[QPixmap]]] = []   # (file name, role, pixmap)
        self._scale = 1.0

    def preload(self, paths: list[Path]) -> None:
        for p in paths:
            self._load(p)

    def set_images(self, items: list[tuple[Path, str]]) -> None:
        self._items = [(p.name, role, self._load(p)) for p, role in items]
        self.update()

    def set_scale(self, scale: float) -> None:
        self._scale = scale

    def _load(self, p: Path) -> Optional[QPixmap]:
        hit = self._cache.get(p)
        if hit is not None or p in self._cache:
            return hit
        pm = QPixmap(str(p)) if p.is_file() else None
        pm = pm if pm is not None and not pm.isNull() else None
        self._cache.put(p, pm)
        return pm

    def paintEvent(self, _event) -> None:
        s = self._scale
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        card = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = 22 * s
        clip = QPainterPath()
        clip.addRoundedRect(card, radius, radius)
        p.setClipPath(clip)
        p.fillRect(card, QColor(SLATE_100))
        n = len(self._items)
        if n:
            gap = 16 * s
            cell_w = (card.width() - gap * (n - 1)) / n
            for i, (name, role, pm) in enumerate(self._items):
                cell = QRectF(card.left() + i * (cell_w + gap), card.top(), cell_w, card.height())
                if pm is None:
                    p.setPen(QColor(SLATE_500))
                    p.setFont(_font(round(22 * s)))
                    p.drawText(cell, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                               t("player.image_missing", name=name))
                else:
                    inner = cell.adjusted(20 * s, 56 * s, -20 * s, -20 * s)
                    size = pm.size().scaled(inner.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
                    target = QRectF(0, 0, size.width(), size.height())
                    target.moveCenter(inner.center())
                    p.drawPixmap(target.toRect(), pm)
                self._chip(p, cell, t(f"player.roles.{role}"), s)
        p.setClipping(False)
        p.setPen(QPen(QColor(SLATE_200), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(card, radius, radius)
        p.end()

    @staticmethod
    def _chip(p: QPainter, cell: QRectF, text: str, s: float) -> None:
        f = _font(round(17 * s), QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2 * s)
        p.setFont(f)
        w = QFontMetrics(f).horizontalAdvance(text) + 24 * s
        chip = QRectF(cell.left() + 16 * s, cell.top() + 14 * s, w, 30 * s)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 220))
        p.drawRoundedRect(chip, 8 * s, 8 * s)
        p.setPen(QColor(SLATE_400))
        p.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)


class _LiveDot(QWidget):
    """Small pulsing dot (status indicator)."""

    def __init__(self, colour: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.colour = colour
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, e) -> None:
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)

    def _tick(self) -> None:
        self._phase += 0.35
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = min(self.width(), self.height()) / 2
        c = QPointF(self.width() / 2, self.height() / 2)
        pulse = 0.5 + 0.5 * math.sin(self._phase)
        halo = QColor(self.colour)
        halo.setAlphaF(0.35 * (1 - pulse))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(c, r * (0.6 + 0.4 * pulse), r * (0.6 + 0.4 * pulse))
        p.setBrush(QColor(self.colour))
        p.drawEllipse(c, r * 0.45, r * 0.45)
        p.end()


class VideoArea(QWidget):
    """Looping, muted video in a dark card with a small header. Several videos play in turn.

    Each video file gets its own QMediaPlayer + QVideoWidget, loaded once in preload().
    Opening a file blocks the GUI thread for ~50-150 ms, which would drop gaze samples
    exactly at step onset; a step change therefore only switches the visible widget.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("videocard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.header = QWidget()
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(0, 0, 0, 0)
        self.dot = _LiveDot(EMERALD_400)
        self.header_label = QLabel(t("player.video_label"))
        self.header_label.setObjectName("videochip")
        hl.addWidget(self.dot)
        hl.addWidget(self.header_label)
        hl.addStretch(1)
        self._stack_host = QWidget()
        self._stack = QStackedLayout(self._stack_host)
        self._msg = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self._msg.setTextFormat(Qt.TextFormat.PlainText)
        self._msg.setWordWrap(True)
        self._msg.setObjectName("videomsg")
        self._stack.addWidget(self._msg)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 18)
        lay.setSpacing(10)
        lay.addWidget(self.header)
        lay.addWidget(self._stack_host, 1)
        self._players: dict[Path, tuple[QMediaPlayer, QVideoWidget]] = {}
        self._playlist: list[Path] = []
        self._idx = 0

    def set_scale(self, s: float) -> None:
        m = round(18 * s)
        self.layout().setContentsMargins(m, round(14 * s), m, m)
        self.dot.setFixedSize(round(22 * s), round(22 * s))

    def preload(self, paths: list[Path]) -> None:
        for p in paths:
            if p in self._players or not p.is_file():
                continue
            widget = QVideoWidget()
            widget.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
            player = QMediaPlayer(self)  # no QAudioOutput attached -> silent
            player.setVideoOutput(widget)
            player.setSource(QUrl.fromLocalFile(str(p.resolve())))
            player.mediaStatusChanged.connect(lambda st, path=p: self._on_status(path, st))
            self._stack.addWidget(widget)
            self._players[p] = (player, widget)

    def wait_until_loaded(self, timeout_ms: int = 3000) -> None:
        """Block (with a local event loop) until every preloaded video has opened."""
        done = (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia,
                QMediaPlayer.MediaStatus.EndOfMedia, QMediaPlayer.MediaStatus.InvalidMedia)
        if all(pl.mediaStatus() in done for pl, _ in self._players.values()):
            return
        loop = QEventLoop()
        timer = QTimer()
        timer.setInterval(10)
        deadline = clock.now() + timeout_ms / 1000

        def check() -> None:
            if clock.now() > deadline or all(pl.mediaStatus() in done for pl, _ in self._players.values()):
                loop.quit()

        timer.timeout.connect(check)
        timer.start()
        loop.exec()

    def set_videos(self, paths: list[Path]) -> None:
        self.stop()
        self.preload(paths)  # no-op if already loaded
        self._playlist = [p for p in paths if p in self._players]
        if not self._playlist:
            self._msg.setText(t("player.video_missing", name="\n".join(p.name for p in paths)))
            self._stack.setCurrentWidget(self._msg)
            return
        self._idx = 0
        self._play_current()

    def stop(self) -> None:
        for p in self._playlist:
            self._players[p][0].pause()
        self._playlist = []

    def _play_current(self) -> None:
        player, widget = self._players[self._playlist[self._idx]]
        player.setLoops(QMediaPlayer.Loops.Infinite if len(self._playlist) == 1 else 1)
        self._stack.setCurrentWidget(widget)
        player.setPosition(0)
        player.play()

    def _on_status(self, path: Path, status: QMediaPlayer.MediaStatus) -> None:
        if (status == QMediaPlayer.MediaStatus.EndOfMedia and len(self._playlist) > 1
                and self._playlist[self._idx] == path):
            self._idx = (self._idx + 1) % len(self._playlist)
            self._play_current()


# --- interaction AOI -------------------------------------------------------------------------------

class InteractionHint(QWidget):
    """Pill in the bottom centre: pulsing dot + "Press [SPACEBAR] or tap the buzzer"."""

    def __init__(self, key_label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key_label = key_label
        self.prefix, self.suffix = t("player.press_prefix"), t("player.press_suffix")
        self._scale = 1.0
        self.dot = _LiveDot(ACCENT, self)

    def set_interaction(self, interaction: str) -> None:
        if interaction == "button":
            self.prefix, self.suffix = t("player.press_prefix"), t("player.press_suffix")
        else:
            self.prefix, self.suffix = interaction[:60], ""
        self.update()

    def set_accent(self, colour: str) -> None:
        self.dot.colour = colour

    def text(self) -> str:
        return f"{self.prefix} {self.key_label} {self.suffix}".strip()

    def set_scale(self, s: float) -> None:
        self._scale = s
        self.update()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.update()

    def paintEvent(self, _e) -> None:
        s = self._scale
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        def metrics(scale: float):
            f = _font(round(24 * scale), QFont.Weight.Medium)
            fk = QFont(MONO_FAMILY.split(",")[0].strip('" '))
            fk.setPixelSize(max(8, round(21 * scale)))
            fk.setBold(True)
            fm, fmk = QFontMetrics(f), QFontMetrics(fk)
            gap, dot_d, pad = 12 * scale, 22 * scale, 28 * scale
            key_w = fmk.horizontalAdvance(self.key_label) + 24 * scale
            content = dot_d + gap + fm.horizontalAdvance(self.prefix) + gap + key_w + gap + fm.horizontalAdvance(self.suffix)
            return f, fk, fm, gap, dot_d, pad, key_w, content

        f, fk, fm, gap, dot_d, pad, key_w, content_w = metrics(s)
        fit = min(1.0, (self.width() - 6) / (content_w + 2 * pad))
        if fit < 1.0:  # shrink everything so the pill fits its AOI rectangle
            s *= fit
            f, fk, fm, gap, dot_d, pad, key_w, content_w = metrics(s)
        pill_h = min(self.height() - 4, 64 * s)
        pill = QRectF((self.width() - content_w) / 2 - pad, (self.height() - pill_h) / 2, content_w + 2 * pad, pill_h)
        p.setPen(QPen(QColor(SLATE_200), 2))
        p.setBrush(QColor(SLATE_100))
        p.drawRoundedRect(pill, pill_h / 2, pill_h / 2)
        x = pill.left() + pad
        cy = pill.center().y()
        self.dot.setGeometry(int(x), int(cy - dot_d / 2), int(dot_d), int(dot_d))
        x += dot_d + gap
        p.setPen(QColor(SLATE_500))
        p.setFont(f)
        w = fm.horizontalAdvance(self.prefix)
        p.drawText(QRectF(x, pill.top(), w, pill_h), Qt.AlignmentFlag.AlignVCenter, self.prefix)
        x += w + gap
        key = QRectF(x, cy - 19 * s, key_w, 38 * s)
        p.setPen(QPen(QColor(SLATE_300), 2))
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(key, 7 * s, 7 * s)
        p.setPen(QColor(SLATE_800))
        p.setFont(fk)
        p.drawText(key, Qt.AlignmentFlag.AlignCenter, self.key_label)
        x += key_w + gap
        p.setPen(QColor(SLATE_500))
        p.setFont(f)
        p.drawText(QRectF(x, pill.top(), fm.horizontalAdvance(self.suffix) + 4, pill_h),
                   Qt.AlignmentFlag.AlignVCenter, self.suffix)
        p.end()


# --- progress track ---------------------------------------------------------------------------------

class StepTrack(QWidget):
    """Segmented progress bar: one segment per step (done / current / to come). Falls back to a
    continuous bar for very long sets. Same API as QProgressBar (setRange / setValue)."""
    MAX_SEGMENTS = 40

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.total = 1
        self.value = 0
        self.accent = ACCENT
        self._scale = 1.0

    def setRange(self, _lo: int, hi: int) -> None:  # noqa: N802 (QProgressBar API)
        self.total = max(1, hi)
        self.update()

    def setValue(self, v: int) -> None:  # noqa: N802
        self.value = max(0, min(v, self.total))
        self.update()

    def set_accent(self, colour: str) -> None:
        self.accent = colour
        self.update()

    def set_scale(self, s: float) -> None:
        self._scale = s
        self.setFixedHeight(max(6, round(14 * s)))

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        r = h / 2
        p.setPen(Qt.PenStyle.NoPen)
        if self.total > self.MAX_SEGMENTS:
            p.setBrush(QColor(SLATE_200))
            p.drawRoundedRect(QRectF(0, 0, self.width(), h), r, r)
            p.setBrush(QColor(self.accent))
            p.drawRoundedRect(QRectF(0, 0, self.width() * self.value / self.total, h), r, r)
            p.end()
            return
        gap = max(3.0, 6 * self._scale)
        w = (self.width() - gap * (self.total - 1)) / self.total
        current = QColor(self.accent)
        current.setAlpha(110)
        for i in range(self.total):
            seg = QRectF(i * (w + gap), 0, w, h)
            if i < self.value - 1:
                p.setBrush(QColor(self.accent))            # done
            elif i == self.value - 1:
                p.setBrush(current)                        # current step
            else:
                p.setBrush(QColor(SLATE_200))              # to come
            p.drawRoundedRect(seg, r, r)
        p.end()


# --- player -----------------------------------------------------------------------------------

class PlayerScreen(QWidget):
    step_event = Signal(object)
    repetition_finished = Signal(int)   # run number

    def __init__(self, cfg: AppConfig, media_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.media_root = Path(media_root)
        self.iset: Optional[InstructionSet] = None
        self.run = 0
        self.current = 0
        self.streak = 0
        self.points = 0
        self.trial = False
        self._active = False
        self._last_press_t = -1e9
        self._next_key = _key_from_name(cfg.input.next_key)
        self._back_key = _key_from_name(cfg.input.back_key)
        self._scale = 1.0

        self.setObjectName("player")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)

        # progress AOI: "Step X of N | title" + chips, bar below
        self.progress = QWidget(self)
        self.progress_label = QLabel()
        self.progress_label.setObjectName("progress_label")
        self.title_label = QLabel()
        self.title_label.setObjectName("progress_title")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.streak_chip = Chip("", "amber", ["streak"])
        self.points_chip = Chip("", "amber", ["trophy"])
        self.run_chip = Chip("", "neutral", ["repetition"])
        self.level_chip = Chip("", "blue", ["level"])
        self.badges = QWidget()     # gamification-only chips
        bl = QHBoxLayout(self.badges)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(12)
        bl.addWidget(self.streak_chip)
        bl.addWidget(self.points_chip)
        bl.addWidget(self.level_chip)
        self.progress_bar = StepTrack()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        row.addWidget(self.progress_label)
        sep = QLabel("|")
        sep.setObjectName("progress_sep")
        row.addWidget(sep)
        row.addWidget(self.title_label)
        row.addStretch(1)
        row.addWidget(self.run_chip)
        row.addWidget(self.badges)
        pl = QVBoxLayout(self.progress)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(8)
        pl.addLayout(row)
        pl.addWidget(self.progress_bar)

        self.text = TextArea(self)
        self.image = ImageArea(cfg.ui.pixmap_cache_size, self)
        self.video = VideoArea(self)
        self.placeholders = {el: HiddenPlaceholder(el, self) for el in ("text", "image", "video")}
        self.interaction = InteractionHint(self._key_label(cfg.input.next_key or "Space"), self)

        self.btn_back = QPushButton("  " + t("player.back"), self)
        self.btn_back.setObjectName("btn_back")
        self.btn_next = QPushButton(t("player.next") + "  ", self)
        self.btn_next.setObjectName("btn_next")
        for b in (self.btn_back, self.btn_next):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Space must never activate a focused button
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        set_button_icon(self.btn_back, "back", SLATE_700, disabled=SLATE_300)
        set_button_icon(self.btn_next, "next", "#ffffff", trailing=True)
        self.btn_back.clicked.connect(self.go_back)
        self.btn_next.clicked.connect(self.go_next)

        self.elements: dict[str, QWidget] = {
            "progress": self.progress, "text": self.text, "image": self.image, "video": self.video,
            "interaction": self.interaction, "btn_back": self.btn_back, "btn_next": self.btn_next,
        }
        self._apply_style(1.0)

    @staticmethod
    def _key_label(key: str) -> str:
        names = section("player.key_names")
        return str(names.get(key, key.upper())) if isinstance(names, dict) else key.upper()

    # --- public API ---------------------------------------------------------------------
    def load(self, iset: InstructionSet, run: int, run_label: str = "", trial: bool = False, streak: int = 0,
             points: int = 0) -> None:
        """Start run `run` of `iset` at step 1."""
        self.iset = iset
        self.run = run
        self.trial = trial
        self.streak = streak
        self.points = points
        self.run_chip.setText(run_label)
        self.run_chip.set_kind("emerald" if trial else "neutral")
        self.run_chip.set_icons(["trial" if trial else "repetition"])
        self.image.preload([p for s in iset.steps for p, _ in self._media(s, "image")])
        self.video.preload([p for s in iset.steps for p, _ in self._media(s, "video")])
        self.video.wait_until_loaded()
        self._apply_gamification()
        self._apply_style(self._scale)
        self._active = True
        self._show_step(0)
        self._emit("start", 0)
        self.setFocus()

    def set_streak(self, n: int) -> None:
        grew = n > self.streak
        self.streak = n
        self.streak_chip.setText(t("player.streak", n=n))
        if grew and self.gamified:
            self._flash(self.streak_chip, "amber")

    def set_points(self, total: int, gained: int = 0) -> None:
        """Points chip; a gain is shown for a moment ("120 pts  +10")."""
        self.points = total
        text = t("player.points", n=total)
        self.points_chip.setText(text + (f"  +{gained}" if gained > 0 and self.gamified else ""))
        if gained > 0 and self.gamified:
            self._flash(self.points_chip, "amber")
            QTimer.singleShot(self.cfg.gamification.flash_ms,
                              lambda: self.points_chip.setText(t("player.points", n=self.points)))

    def cheer(self, text: str) -> None:
        if self.gamified:
            self.text.cheer(text, self.cfg.gamification.cheer_s)

    def _flash(self, chip: Chip, kind: str) -> None:
        """Brief green highlight of a chip (positive feedback without extra elements)."""
        chip.set_kind("emerald")
        QTimer.singleShot(self.cfg.gamification.flash_ms, lambda: chip.set_kind(kind))

    @property
    def gamified(self) -> bool:
        return self.iset is not None and "gamification" not in self.iset.settings.hidden_elements

    @property
    def is_active(self) -> bool:
        """True while a run is in progress (not yet finished)."""
        return self._active

    def stop(self) -> None:
        self._active = False
        self.video.stop()

    def go_next(self) -> None:
        if not self._accept_press():
            return
        self._emit("end", self.current)
        if self.current == self.iset.n_steps - 1:
            self._active = False
            self.video.stop()
            self.repetition_finished.emit(self.run)
            return
        self._show_step(self.current + 1)
        self._emit("start", self.current)

    def go_back(self) -> None:
        if self.current == 0 or not self._accept_press():
            return
        self._emit("back", self.current)
        self._show_step(self.current - 1)
        self._emit("start", self.current)

    def aoi_rects_px(self) -> dict[str, QRect]:
        """Current on-widget pixel rectangles of all AOIs (after scaling)."""
        return {name: self._scaled(name) for name in self.elements}

    # --- internals ----------------------------------------------------------------------
    def _media(self, step: Step, mtype: str) -> list[tuple[Path, str]]:
        out = []
        for m in step.media_of(mtype):  # type: ignore[arg-type]
            try:
                out.append((safe_join(self.media_root, m.path), m.role))
            except UnsafePathError:
                continue  # never load files outside the media folder
        return out

    def _accept_press(self) -> bool:
        if not self._active or self.iset is None:
            return False
        t_now = clock.now()
        if (t_now - self._last_press_t) * 1000 < self.cfg.input.debounce_ms:
            return False
        self._last_press_t = t_now
        return True

    def _emit(self, event: str, step_index: int) -> None:
        self.step_event.emit(StepEvent(t=clock.now(), run=self.run, step_index=step_index, event=event))

    def _apply_gamification(self) -> None:
        on = self.gamified
        self.badges.setVisible(on)
        self.text.set_gamified(on)
        self.streak_chip.setText(t("player.streak", n=self.streak))
        self.points_chip.setText(t("player.points", n=self.points))
        self.points_chip.setVisible(self.cfg.gamification.show_points)
        self.text.set_speak_duration(self.cfg.gamification.speak_s)
        level = self.iset.settings.merge_level if self.iset else 0
        self.level_chip.setText(t(f"player.level.{level}"))
        self.level_chip.set_kind("blue" if level == 0 else "emerald")
        self.interaction.set_accent(EMERALD_600 if self.trial else ACCENT)
        self.progress_bar.set_accent(EMERALD_600 if self.trial else ACCENT)

    def _show_step(self, idx: int) -> None:
        assert self.iset is not None
        self.current = idx
        step: Step = self.iset.steps[idx]
        hidden = set(self.iset.settings.hidden_elements)
        n = self.iset.n_steps
        placeholder = self.cfg.ui.show_hidden_placeholder

        self.progress_label.setText(t("player.step_of", n=idx + 1, total=n))
        self.title_label.setText(self.iset.title)
        self.progress_bar.setRange(0, n)
        self.progress_bar.setValue(idx + 1)

        self.text.setText(step.text)
        self.text.set_step(idx + 1)
        self.text.setVisible("text" not in hidden)

        images = self._media(step, "image")
        self.image.set_images(images)
        self.image.setVisible("image" not in hidden and bool(images))

        videos = [p for p, _ in self._media(step, "video")]
        show_video = "video" not in hidden and bool(videos)
        self.video.setVisible(show_video)
        if show_video:
            self.video.set_videos(videos)
        else:
            self.video.stop()
        for el, ph in self.placeholders.items():
            ph.setVisible(placeholder and el in hidden)

        self.interaction.set_interaction(step.interaction)
        self.btn_back.setEnabled(idx > 0)
        last = idx == n - 1
        self.btn_next.setText((t("player.finish") if last else t("player.next")) + "  ")
        set_button_icon(self.btn_next, "flag" if last else "next", "#ffffff", trailing=True)
        self.btn_next.setIconSize(QSize(max(12, round(30 * self._scale)), max(12, round(30 * self._scale))))

    def _scaled(self, name: str) -> QRect:
        r = self.cfg.aoi[name]
        sx = self.width() / self.cfg.screen.width
        sy = self.height() / self.cfg.screen.height
        return QRect(round(r.x * sx), round(r.y * sy), round(r.w * sx), round(r.h * sy))

    def resizeEvent(self, event) -> None:
        for name, w in self.elements.items():
            w.setGeometry(self._scaled(name))
        for el, ph in self.placeholders.items():
            ph.setGeometry(self._scaled(el))
        scale = min(self.width() / self.cfg.screen.width, self.height() / self.cfg.screen.height)
        self._apply_style(scale)
        super().resizeEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.isAutoRepeat():
            return
        if event.key() == self._next_key:
            self.go_next()
        elif self._back_key is not None and event.key() == self._back_key:
            self.go_back()
        else:
            super().keyPressEvent(event)

    def _apply_style(self, scale: float) -> None:
        self._scale = scale or 1.0
        px = {k: max(9, round(v * scale)) for k, v in FONT_PX.items()}
        s = lambda v: max(1, round(v * scale))  # noqa: E731
        for w in (self.text, self.image, self.video, self.interaction, self.progress_bar,
                  *self.placeholders.values()):
            w.set_scale(scale)
        accent, accent_dark = (EMERALD_600, EMERALD_700) if self.trial else (ACCENT, ACCENT_DARK)
        self.setStyleSheet(f"""
            QWidget#player {{ background: {PLAYER_BG}; }}
            QWidget {{ font-family: {FONT_FAMILY}; }}
            QLabel {{ color: {SLATE_900}; background: transparent; }}
            QLabel#progress_label {{ font-size: {px['progress']}px; font-weight: 700; color: {SLATE_700}; }}
            QLabel#progress_sep {{ font-size: {px['progress']}px; color: {SLATE_300}; }}
            QLabel#progress_title {{ font-size: {px['progress']}px; font-weight: 400; color: {SLATE_400}; }}
            QWidget#chip_amber {{ background: {AMBER_100}; border: {s(2)}px solid {AMBER_300}; border-radius: {s(15)}px; }}
            QWidget#chip_blue {{ background: {BLUE_100}; border: {s(2)}px solid {BLUE_300}; border-radius: {s(15)}px; }}
            QWidget#chip_emerald {{ background: {EMERALD_100}; border: {s(2)}px solid {EMERALD_300}; border-radius: {s(15)}px; }}
            QWidget#chip_neutral {{ background: {SLATE_100}; border: {s(2)}px solid {SLATE_300}; border-radius: {s(15)}px; }}
            QWidget#chip_amber QLabel, QWidget#chip_blue QLabel, QWidget#chip_emerald QLabel,
            QWidget#chip_neutral QLabel {{ border: none; background: transparent; }}
            QWidget#chip_amber QLabel#chiptext {{ color: {AMBER_900}; font-size: {px['chip']}px; font-weight: 700; padding: {s(3)}px 0px; }}
            QWidget#chip_blue QLabel#chiptext {{ color: {BLUE_900}; font-size: {px['chip']}px; font-weight: 700; padding: {s(3)}px 0px; }}
            QWidget#chip_emerald QLabel#chiptext {{ color: {EMERALD_800}; font-size: {px['chip']}px; font-weight: 700; padding: {s(3)}px 0px; }}
            QWidget#chip_neutral QLabel#chiptext {{ color: {SLATE_800}; font-size: {px['chip']}px; font-weight: 700; padding: {s(3)}px 0px; }}
            QProgressBar {{ background: {SLATE_200}; border: none; border-radius: {s(7)}px;
                            min-height: {s(14)}px; max-height: {s(14)}px; }}
            QProgressBar::chunk {{ background: {accent}; border-radius: {s(7)}px; }}
            QWidget#videocard {{ background: {SLATE_900}; border: {s(2)}px solid {SLATE_800}; border-radius: {s(22)}px; }}
            QLabel#videochip {{ color: {EMERALD_400}; font-size: {s(17)}px; font-weight: 700; letter-spacing: 1px;
                                font-family: {MONO_FAMILY}; }}
            QLabel#videomsg {{ color: {SLATE_300}; font-size: {px['placeholder']}px; }}
            QPushButton {{ font-size: {px['button']}px; font-weight: 700; border-radius: {s(14)}px; }}
            QPushButton#btn_next {{ background: {accent}; color: white; border: none; }}
            QPushButton#btn_next:pressed {{ background: {accent_dark}; }}
            QPushButton#btn_back {{ background: #ffffff; color: {SLATE_700}; border: {s(2)}px solid {SLATE_300}; }}
            QPushButton#btn_back:pressed {{ background: {SLATE_100}; }}
            QPushButton#btn_back:disabled {{ color: {SLATE_300}; border-color: {SLATE_200}; }}
        """)
        self.video.set_scale(scale)
        scale_button_icons(self, max(12, round(30 * scale)))

    def sizeHint(self) -> QSize:
        return QSize(self.cfg.screen.width, self.cfg.screen.height)
