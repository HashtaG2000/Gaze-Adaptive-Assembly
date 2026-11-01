"""Camera check job (runs in a worker thread) and its validation.

verification_job() loads + downscales both images, asks the vision client, validates the strict
JSON answer and returns a VerificationRecord. needs_review is set when the verdict is "unclear",
the confidence is below the threshold, there is no reference image, or the call failed - those
steps go to the observer, who decides in the Workspace.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pydantic import ValidationError
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage

from app import clock
from app.llm.client import LLMError
from app.llm.prompts import PromptTemplates
from app.models import VerificationRecord, VerificationResult
from app.models.instruction import clean_text
from app.pipeline.logger import wall_now
from app.vision.clients import VisionClient

MAX_INPUT_BYTES = 40 * 1024 * 1024
MAX_RAW_CHARS = 20000


@dataclass
class CheckJob:
    id: int
    run: int
    step_index: int
    covers: list[int]
    photo: Path                          # absolute
    photo_rel: str                       # relative to the session folder
    reference: Optional[Path]            # absolute, None = no reference image
    reference_rel: Optional[str]
    step_text: str
    part_id: str
    quantity: int
    positions: list[str] = field(default_factory=list)


def load_jpeg(path: Path, max_px: int) -> bytes:
    """Read an image (size-limited), downscale to max_px on its longest side, return JPEG bytes."""
    path = Path(path)
    if not path.is_file():
        raise LLMError(f"image missing: {path.name}")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise LLMError(f"image too large: {path.name}")
    img = QImage(str(path))
    if img.isNull():
        raise LLMError(f"image unreadable: {path.name}")
    if max(img.width(), img.height()) > max_px:
        img = img.scaled(max_px, max_px, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "JPG", 88)
    buf.close()
    return bytes(data)


def validate_verification(raw: Optional[str]) -> tuple[Optional[VerificationResult], Optional[str]]:
    text = (raw or "").strip()
    if text.startswith("```"):                          # tolerate a markdown code fence
        start, end = text.find("{"), text.rfind("}")
        text = text[start:end + 1] if 0 <= start < end else text
    try:
        data = json.loads(text)
        res = VerificationResult.model_validate_json(json.dumps(data), strict=True)
    except ValidationError as exc:
        return None, "invalid answer: " + "; ".join(e["msg"] for e in exc.errors())[:300]
    except ValueError as exc:
        return None, f"invalid answer: {str(exc)[:300]}"
    return res.model_copy(update={"reason": clean_text(res.reason)}), None


def build_prompt(templates: PromptTemplates, job: CheckJob, threshold: float) -> tuple[str, dict]:
    schema = VerificationResult.model_json_schema()
    prompt = templates.render("verification", step_text=job.step_text, part_id=job.part_id, quantity=job.quantity,
                              positions=", ".join(job.positions) or "-", threshold=f"{threshold:.2f}",
                              schema=json.dumps(schema, ensure_ascii=False))
    return prompt, schema


def verification_job(client: Optional[VisionClient], templates: PromptTemplates, job: CheckJob,
                     threshold: float, max_px: int) -> VerificationRecord:
    base = dict(id=job.id, t=clock.now(), wall_time=wall_now(), run=job.run, step_index=job.step_index,
                covers=job.covers, photo=job.photo_rel, reference=job.reference_rel)
    if client is None:
        return VerificationRecord(**base, client="off", needs_review=True, error="automatic check is off")
    if job.reference is None:
        return VerificationRecord(**base, client=client.name, needs_review=True,
                                  error="no reference image for this step (add one in Enrichments)")
    t0 = time.perf_counter()
    try:
        reference, photo = load_jpeg(job.reference, max_px), load_jpeg(job.photo, max_px)
        prompt, schema = build_prompt(templates, job, threshold)
        raw = client.compare(prompt, schema, reference, photo)
    except Exception as exc:  # noqa: BLE001 - LLMError, provider SDK bugs: never kill the worker
        msg = str(exc) if isinstance(exc, LLMError) else f"{exc.__class__.__name__}: {exc}"
        return VerificationRecord(**base, client=client.name, needs_review=True, error=msg[:1000],
                                  latency_s=round(time.perf_counter() - t0, 3))
    latency = round(time.perf_counter() - t0, 3)
    result, err = validate_verification(raw)
    if result is None:
        return VerificationRecord(**base, client=client.name, needs_review=True,
                                  raw_response=(raw or "")[:MAX_RAW_CHARS], error=err, latency_s=latency)
    review = result.verdict == "unclear" or result.confidence < threshold
    return VerificationRecord(**base, client=client.name, verdict=result.verdict, confidence=result.confidence,
                              reason=result.reason, needs_review=review, raw_response=raw[:MAX_RAW_CHARS],
                              latency_s=latency)
