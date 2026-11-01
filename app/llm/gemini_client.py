"""Gemini implementation (google-genai SDK). API key from the environment only."""
from __future__ import annotations

import os
from typing import Any

from app.config import GeminiConfig
from app.llm.client import CallType, LLMError


class GeminiClient:
    name = "gemini"

    def __init__(self, cfg: GeminiConfig) -> None:
        key = os.environ.get(cfg.api_key_env)
        if not key:
            raise LLMError(f"Environment variable {cfg.api_key_env} is not set")
        from google import genai
        from google.genai import types

        self._types = types
        self.cfg = cfg
        self._client = genai.Client(
            api_key=key, http_options=types.HttpOptions(timeout=int(cfg.timeout_s * 1000)))
        self._schema_supported = True

    def complete_json(self, call: CallType, prompt: str, schema: dict[str, Any],
                      payload: dict[str, Any]) -> str:
        try:
            return self._generate(prompt, schema if self._schema_supported else None)
        except LLMError:
            raise
        except Exception as exc:
            # Structured output may reject parts of the JSON schema. The prompt already contains
            # the schema, so retry once in plain JSON mode; validate.py checks the result anyway.
            if self._schema_supported and "schema" in str(exc).lower():
                self._schema_supported = False
                try:
                    return self._generate(prompt, None)
                except Exception as exc2:
                    raise LLMError(f"{exc2.__class__.__name__}: {exc2}") from exc2
            raise LLMError(f"{exc.__class__.__name__}: {exc}") from exc

    def _generate(self, prompt: str, schema: dict[str, Any] | None) -> str:
        t = self._types
        kwargs: dict[str, Any] = dict(temperature=self.cfg.temperature, response_mime_type="application/json")
        if schema is not None:
            kwargs["response_json_schema"] = schema
        resp = self._client.models.generate_content(
            model=self.cfg.model, contents=prompt, config=t.GenerateContentConfig(**kwargs))
        text = resp.text
        if not text:
            raise LLMError(f"empty response (finish reason: {_finish_reason(resp)})")
        return text


def _finish_reason(resp: Any) -> str:
    try:
        return str(resp.candidates[0].finish_reason)
    except Exception:
        return "unknown"
