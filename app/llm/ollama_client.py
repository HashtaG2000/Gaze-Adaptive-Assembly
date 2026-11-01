"""Local LLM via Ollama (https://ollama.com) - no API key, data stays on the lab computer.

Setup: install Ollama, then `ollama pull <model>` (model name in config.yaml llm.ollama.model).
Uses the /api/chat endpoint with `format` = JSON schema (structured output). Only connects to
the configured host (default 127.0.0.1); nothing is sent anywhere else.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from app.config import OllamaConfig
from app.llm.client import CallType, LLMError

MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class OllamaClient:
    name = "ollama"

    def __init__(self, cfg: OllamaConfig) -> None:
        self.cfg = cfg
        self.url = f"http://{cfg.host}:{cfg.port}/api/chat"

    def complete_json(self, call: CallType, prompt: str, schema: dict[str, Any],
                      payload: dict[str, Any]) -> str:
        body = json.dumps({
            "model": self.cfg.model,
            "messages": [{"role": "user", "content": prompt}],
            "format": schema,
            "stream": False,
            "options": {"temperature": self.cfg.temperature},
        }).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout_s) as resp:  # noqa: S310 (fixed local URL)
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            detail = exc.read(2000).decode("utf-8", "replace")
            raise LLMError(f"Ollama HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"Ollama not reachable at {self.cfg.host}:{self.cfg.port} ({exc}). "
                           f"Is Ollama running and the model '{self.cfg.model}' pulled?") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LLMError("Ollama response too large")
        try:
            data = json.loads(raw.decode("utf-8"))
            text = data["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMError(f"unexpected Ollama response: {raw[:300]!r}") from exc
        if not isinstance(text, str) or not text.strip():
            raise LLMError("empty response from Ollama")
        return text

    def ping(self) -> bool:
        try:
            with urllib.request.urlopen(f"http://{self.cfg.host}:{self.cfg.port}/api/tags", timeout=2):  # noqa: S310
                return True
        except (urllib.error.URLError, TimeoutError, OSError):
            return False
