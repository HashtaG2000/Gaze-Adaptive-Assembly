"""Create an LLM client by name ('mock' | 'gemini' | 'ollama')."""
from __future__ import annotations

from app.config import AppConfig
from app.llm.client import LLMClient

CLIENT_NAMES = ("mock", "ollama", "gemini")


def make_llm_client(name: str, cfg: AppConfig) -> LLMClient:
    if name == "mock":
        from app.llm.mock_client import MockLLMClient
        return MockLLMClient()
    if name == "gemini":
        from app.llm.gemini_client import GeminiClient
        return GeminiClient(cfg.llm.gemini)          # raises LLMError if the API key is missing
    if name == "ollama":
        from app.llm.ollama_client import OllamaClient
        return OllamaClient(cfg.llm.ollama)
    raise ValueError(f"unknown LLM client {name!r}")
