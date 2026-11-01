"""AOI layout export: the player's fixed element rectangles as JSON (pixels + normalised) and PNG."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen

from app.config import AppConfig
from app.core.files import write_json_atomic

PALETTE = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#7a5cc7"]


def aoi_layout(cfg: AppConfig) -> dict:
    w, h = cfg.screen.width, cfg.screen.height
    return {
        "screen": {"width": w, "height": h},
        "aois": {name: {"x": r.x, "y": r.y, "w": r.w, "h": r.h,
                        "norm": {"x": round(r.x / w, 6), "y": round(r.y / h, 6),
                                 "w": round(r.w / w, 6), "h": round(r.h / h, 6)}}
                 for name, r in cfg.aoi.items()},
        "note": "Pixels on the configured canvas; gaze samples are normalised (0..1) to the same screen.",
    }


def export_aoi_layout(cfg: AppConfig, out_dir: Path) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"aoi_layout_{stamp}.json"
    png_path = out_dir / f"aoi_layout_{stamp}.png"
    write_json_atomic(json_path, aoi_layout(cfg))
    img = QImage(cfg.screen.width, cfg.screen.height, QImage.Format.Format_RGB32)
    img.fill(QColor("#ffffff"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = QFont()
    font.setPixelSize(22)
    font.setBold(True)
    p.setFont(font)
    for i, (name, r) in enumerate(cfg.aoi.items()):
        colour = QColor(PALETTE[i % len(PALETTE)])
        fill = QColor(colour)
        fill.setAlpha(40)
        rect = QRectF(r.x, r.y, r.w, r.h)
        p.setPen(QPen(colour, 3))
        p.setBrush(fill)
        p.drawRect(rect)
        p.setPen(colour.darker(130))
        p.drawText(rect.adjusted(10, 6, -10, -6), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                   f"{name}  [{r.x}, {r.y}, {r.w}×{r.h}]")
    p.end()
    img.save(str(png_path))
    return json_path, png_path
