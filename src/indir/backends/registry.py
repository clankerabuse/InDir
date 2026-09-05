from __future__ import annotations

from pathlib import Path

from indir.backends.anthropic import AnthropicBackend
from indir.backends.base import ChatBackend
from indir.backends.cursor_sdk import CursorSDKBackend
from indir.backends.ollama import OllamaBackend
from indir.backends.openai_compat import OpenAICompatBackend
from indir.config import AppConfig


def create_backend(config: AppConfig, directory: Path) -> ChatBackend:
    provider = config.backend.provider.lower()
    if provider == "ollama":
        return OllamaBackend(config.backend.ollama)
    if provider == "openai":
        return OpenAICompatBackend(config.backend.openai)
    if provider == "grok":
        return OpenAICompatBackend(config.backend.grok)
    if provider == "anthropic":
        return AnthropicBackend(config.backend.anthropic)
    if provider == "cursor":
        return CursorSDKBackend(config.backend.cursor, directory)
    if provider == "opencode":
        return OpenAICompatBackend(config.backend.opencode)
    raise ValueError(f"Unknown backend provider: {provider}")
