from __future__ import annotations

import json
from typing import Any

import httpx

from indir.backends.base import ChatBackend, ChatMessage, ChatResponse, ToolCall
from indir.config import AnthropicConfig


class AnthropicBackend(ChatBackend):
    def __init__(self, config: AnthropicConfig) -> None:
        if not config.resolved_api_key():
            raise ValueError(
                f"Missing API key: set {config.api_key_env} or api_key in config"
            )
        self.config = config
        self.client = httpx.Client(
            base_url="https://api.anthropic.com",
            headers={
                "x-api-key": config.resolved_api_key(),
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=120.0,
        )

    def _to_anthropic_messages(
        self, messages: list[ChatMessage]
    ) -> tuple[str | None, list[dict[str, Any]]]:
        system: str | None = None
        result: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == "system":
                system = msg.content
            elif msg.role == "user":
                result.append({"role": "user", "content": msg.content or ""})
            elif msg.role == "assistant":
                content: list[dict[str, Any]] = []
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
                result.append({"role": "assistant", "content": content})
            elif msg.role == "tool":
                result.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_call_id,
                                "content": msg.content or "",
                            }
                        ],
                    }
                )

        return system, result

    def _to_anthropic_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "input_schema": t["function"]["parameters"],
            }
            for t in tools
        ]

    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResponse:
        system, anthropic_messages = self._to_anthropic_messages(messages)
        payload: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": 4096,
            "messages": anthropic_messages,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = self._to_anthropic_tools(tools)

        response = self.client.post("/v1/messages", json=payload)
        response.raise_for_status()
        data = response.json()

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        arguments=block.get("input", {}),
                    )
                )

        stop_reason = data.get("stop_reason", "end_turn")
        finish_reason = "tool_calls" if tool_calls else "stop"

        return ChatResponse(
            message=ChatMessage(
                role="assistant",
                content="\n".join(text_parts) if text_parts else None,
                tool_calls=tool_calls,
            ),
            finish_reason=finish_reason if tool_calls else stop_reason,
        )
