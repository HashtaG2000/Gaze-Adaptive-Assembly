"""Vision clients: compare(prompt, schema, reference, photo) -> raw JSON text.

    gemini   Google Gemini (API key from the environment, e.g. gemini-2.5-flash)
    ollama   local model via Ollama (no key; e.g. qwen2.5vl:7b, gemma3:4b, llama3.2-vision)
    mock     no model: grey-scale pixel similarity of both images (deterministic, for tests;
             it does not understand the assembly)

Images are passed as JPEG bytes (already downscaled). Errors raise LLMError.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional, Protocol

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from app.config import AppConfig
from app.llm.client import LLMError

VISION_NAMES = ("off", "mock", "ollama", "gemini")
MAX_RESPONSE_BYTES = 1024 * 1024


class VisionClient(Protocol):
    name: str

    def compare(self, prompt: str, schema: dict[str, Any], reference: bytes, photo: bytes) -> str:
        ...


class GeminiVision:
    def __init__(self, cfg: AppConfig) -> None:
        key = os.environ.get(cfg.llm.gemini.api_key_env)
        if not key:
            raise LLMError(f"Environment variable {cfg.llm.gemini.api_key_env} is not set")
        from google import genai
        from google.genai import types
        self._types = types
        self.model = cfg.verification.gemini_model
        self.temperature = cfg.llm.gemini.temperature
        self._client = genai.Client(api_key=key, http_options=types.HttpOptions(
            timeout=int(cfg.verification.timeout_s * 1000)))
        self.name = f"gemini:{self.model}"

    def compare(self, prompt: str, schema: dict[str, Any], reference: bytes, photo: bytes) -> str:
        t = self._types
        contents = [prompt, "IMAGE 1 (REFERENCE):", t.Part.from_bytes(data=reference, mime_type="image/jpeg"),
                    "IMAGE 2 (PHOTO):", t.Part.from_bytes(data=photo, mime_type="image/jpeg")]
        try:
            resp = self._client.models.generate_content(
                model=self.model, contents=contents,
                config=t.GenerateContentConfig(temperature=self.temperature, response_mime_type="application/json",
                                               response_json_schema=schema))
        except Exception as exc:  # SDK / network / quota
            raise LLMError(f"{exc.__class__.__name__}: {exc}") from exc
        if not resp.text:
            raise LLMError("empty response")
        return resp.text


class OllamaVision:
    def __init__(self, cfg: AppConfig) -> None:
        o = cfg.llm.ollama
        self.url = f"http://{o.host}:{o.port}/api/chat"
        self.model = cfg.verification.ollama_model
        self.temperature = o.temperature
        self.timeout = cfg.verification.timeout_s
        self.name = f"ollama:{self.model}"

    def compare(self, prompt: str, schema: dict[str, Any], reference: bytes, photo: bytes) -> str:
        body = json.dumps({
            "model": self.model, "stream": False, "format": schema,
            "options": {"temperature": self.temperature},
            "messages": [{"role": "user", "content": prompt + "\n(Image 1 = REFERENCE, image 2 = PHOTO.)",
                          "images": [base64.b64encode(reference).decode(), base64.b64encode(photo).decode()]}],
        }).encode()
        req = urllib.request.Request(self.url, data=body, method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (configured local host)
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise LLMError(f"Ollama HTTP {exc.code}: {exc.read(2000).decode('utf-8', 'replace')}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"Ollama not reachable ({exc}). Is the model '{self.model}' pulled?") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LLMError("Ollama response too large")
        try:
            text = json.loads(raw.decode("utf-8"))["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMError(f"unexpected Ollama response: {raw[:300]!r}") from exc
        if not isinstance(text, str) or not text.strip():
            raise LLMError("empty response from Ollama")
        return text


def grey_thumbnail(data: bytes, size: int = 64) -> np.ndarray:
    img = QImage.fromData(data)
    if img.isNull():
        raise LLMError("image could not be decoded")
    img = img.convertToFormat(QImage.Format.Format_Grayscale8).scaled(
        size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    line = img.bytesPerLine()
    arr = np.frombuffer(img.constBits(), dtype=np.uint8, count=line * size).reshape(size, line)
    return arr[:, :size].astype(float) / 255.0


def block_similarity(reference: bytes, photo: bytes, grid: int = 8) -> float:
    """1 - the largest mean grey difference over a grid x grid split of both thumbnails.
    A single missing brick is a strong local difference, which a whole-image mean would hide."""
    diff = np.abs(grey_thumbnail(reference) - grey_thumbnail(photo))
    size = diff.shape[0] // grid
    blocks = diff[: size * grid, : size * grid].reshape(grid, size, grid, size).mean(axis=(1, 3))
    return float(1.0 - blocks.max())


class MockVision:
    """Pixel comparison, NOT understanding (it cannot tell a rotated brick from a shadow):
    block similarity against mock_similarity. Deterministic - for tests and dry runs only."""
    name = "mock"

    def __init__(self, cfg: AppConfig) -> None:
        self.need = cfg.verification.mock_similarity

    def compare(self, prompt: str, schema: dict[str, Any], reference: bytes, photo: bytes) -> str:
        sim = block_similarity(reference, photo)
        tol = max(1e-6, 1.0 - self.need)                 # allowed block difference
        diff = 1.0 - sim
        if diff <= tol:
            verdict, conf = "correct", 1.0 - 0.5 * diff / tol
        else:
            verdict, conf = "error", 0.5 + 4.0 * (diff - tol)
        return json.dumps({"verdict": verdict, "confidence": round(max(0.0, min(1.0, conf)), 3),
                           "reason": f"[mock] block similarity {sim:.2f} (needs {self.need:.2f} for correct)"})


def make_vision_client(name: str, cfg: AppConfig) -> Optional[VisionClient]:
    if name == "off":
        return None
    if name == "mock":
        return MockVision(cfg)
    if name == "ollama":
        return OllamaVision(cfg)
    if name == "gemini":
        return GeminiVision(cfg)
    raise ValueError(f"unknown checker {name!r}")
