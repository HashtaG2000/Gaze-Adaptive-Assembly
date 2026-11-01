"""Provider-agnostic LLM client interface.

Every client returns the RAW response text; parsing and validation happen in validate.py,
so invalid output from any provider is handled (and logged) the same way.

`payload` is the structured input the prompt was built from. Real providers ignore it and
use `prompt`; the mock client uses it to behave deterministically.
"""
from __future__ import annotations

from typing import Any, Literal, Protocol

CallType = Literal["analysis", "generation"]


class LLMError(RuntimeError):
    """Provider call failed (network, quota, timeout, refused...)."""


class LLMClient(Protocol):
    name: str

    def complete_json(self, call: CallType, prompt: str, schema: dict[str, Any],
                      payload: dict[str, Any]) -> str:
        """Return the model's raw text (expected to be JSON matching `schema`). Raise LLMError."""
        ...
