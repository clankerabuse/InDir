from __future__ import annotations

from pathlib import Path
from typing import Any

from indir.backends.base import ChatBackend, ChatMessage, ChatResponse, ToolCall
from indir.config import CursorConfig


class CursorSDKBackend(ChatBackend):
    def __init__(self, config: CursorConfig, directory: Path) -> None:
        if not config.resolved_api_key():
            raise ValueError(
                f"Missing API key: set {config.api_key_env} or api_key in config"
            )
        try:
            from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
        except ImportError as exc:
            raise ImportError(
                "cursor-sdk is not installed. Install with: pip install indir[cursor]"
            ) from exc

        self.config = config
        self.directory = directory
        self._Agent = Agent
        self._AgentOptions = AgentOptions
        self._LocalAgentOptions = LocalAgentOptions

    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResponse:
        user_messages = [m for m in messages if m.role == "user"]
        if not user_messages:
            return ChatResponse(message=ChatMessage(role="assistant", content=""))

        last_user = user_messages[-1].content or ""
        system_msgs = [m.content for m in messages if m.role == "system"]
        prompt = last_user
        if system_msgs:
            prompt = f"{system_msgs[0]}\n\nUser request: {last_user}"

        options_kwargs: dict[str, Any] = {
            "api_key": self.config.resolved_api_key(),
            "model": self.config.model,
        }
        if self.config.local_cwd:
            options_kwargs["local"] = self._LocalAgentOptions(cwd=str(self.directory))

        result = self._Agent.prompt(prompt, self._AgentOptions(**options_kwargs))
        if getattr(result, "status", None) == "error":
            run_id = getattr(result, "id", "unknown")
            raise RuntimeError(f"Cursor agent run failed ({run_id})")

        content = getattr(result, "result", None) or str(result)

        return ChatResponse(
            message=ChatMessage(role="assistant", content=content),
            finish_reason="stop",
        )
