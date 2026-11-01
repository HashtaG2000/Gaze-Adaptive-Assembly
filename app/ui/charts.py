"""pyqtgraph helpers for the dark cockpit (colours from data/theme.json -> chart).

Categorical colours are used in a fixed order (same AOI group = same colour everywhere).
Legends sit in a row above the plot so they never cover data; bars show values on hover and
the tables next to the charts carry every number.
"""
from __future__ import annotations

from typing import Optional

import pyqtgraph as pg
from PySide6.QtCore import QPointF
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QToolTip

from app.ui.style import CHART

SURFACE = CHART["surface"]
TEXT = CHART["text"]
GRID = CHART["grid"]
REFERENCE = CHART["reference"]
PALETTE: list[str] = list(CHART["categorical"]) or ["#3987e5"]


def colour(i: int) -> str:
    return PALETTE[i % len(PALETTE)]


# AOIs folded into six groups; fixed order = fixed colour
AOI_GROUPS: list[tuple[str, tuple[str, ...], str]] = [
    ("text", ("text",), colour(0)),
    ("image", ("image",), colour(1)),
    ("video", ("video",), colour(2)),
    ("navigation", ("progress", "interaction", "btn_back", "btn_next"), colour(3)),
    ("off-screen", ("off_screen",), colour(4)),
    ("other / invalid", ("other", "invalid"), colour(5)),
]

pg.setConfigOptions(antialias=True, background=SURFACE, foreground=TEXT)


def style_plot(p: pg.PlotItem, title: str, ylabel: str, xlabel: str) -> None:
    p.setTitle(title, color=TEXT, size="10pt")
    p.setLabel("left", ylabel, color=TEXT)
    p.setLabel("bottom", xlabel, color=TEXT)
    p.showGrid(x=False, y=True, alpha=0.2)
    p.setMenuEnabled(False)
    p.setMouseEnabled(x=False, y=False)
    p.hideButtons()
    for ax in ("left", "bottom"):
        p.getAxis(ax).setPen(pg.mkPen(GRID))
        p.getAxis(ax).setTextPen(pg.mkPen(TEXT))


class Chart:
    """Plot with its legend in a row ABOVE the plot area."""

    def __init__(self, title: str, ylabel: str, xlabel: str = "Step") -> None:
        self.widget = pg.GraphicsLayoutWidget()
        self.widget.setBackground(SURFACE)
        self.widget.ci.setContentsMargins(4, 4, 4, 4)
        self.legend = pg.LegendItem(colCount=6, labelTextColor=TEXT, labelTextSize="9pt")
        self.widget.addItem(self.legend, row=0, col=0)
        self.plot: pg.PlotItem = self.widget.addPlot(row=1, col=0)
        style_plot(self.plot, title, ylabel, xlabel)
        self.hover = HoverColumns(self)

    def clear(self) -> None:
        self.plot.clear()
        self.legend.clear()
        self.hover.texts = []

    def add(self, item, name: Optional[str] = None) -> None:
        self.plot.addItem(item)
        if name:
            self.legend.addItem(item, name)

    def set_ticks(self, ticks: list[tuple[float, str]]) -> None:
        self.plot.getAxis("bottom").setTicks([ticks])


class HoverColumns:
    """Tooltip per x position 1..n: hit target is the whole column (bigger than the mark)."""

    def __init__(self, chart: Chart) -> None:
        self.chart = chart
        self.texts: list[str] = []
        chart.widget.scene().sigMouseMoved.connect(self._moved)

    def _moved(self, pos: QPointF) -> None:
        vb = self.chart.plot.vb
        if not self.texts or not vb.sceneBoundingRect().contains(pos):
            QToolTip.hideText()
            return
        x = vb.mapSceneToView(pos).x()
        i = int(round(x)) - 1
        if 0 <= i < len(self.texts) and abs(x - (i + 1)) <= 0.5:
            QToolTip.showText(QCursor.pos(), self.texts[i], self.chart.widget)
        else:
            QToolTip.hideText()


def bars(x: list[float], heights: list[float], brush: str, y0: Optional[list[float]] = None,
         width: float = 0.6) -> pg.BarGraphItem:
    kw = {"y0": y0} if y0 is not None else {}
    return pg.BarGraphItem(x=x, height=heights, width=width, brush=brush, pen=pg.mkPen(SURFACE, width=2), **kw)


def markers(x: list[float], y: list[float], brush: str = REFERENCE, size: int = 9) -> pg.ScatterPlotItem:
    return pg.ScatterPlotItem(x=x, y=y, size=size, brush=brush, pen=pg.mkPen(SURFACE, width=2))
