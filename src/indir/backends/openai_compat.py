from __future__ import annotations

import json
from typing import Any

import httpx

from indir.backends.base import ChatBackend, ChatMessage, ChatResponse, ToolCall
from indir.config import OpenAIConfig


class OpenAICompatBackend(ChatBackend):
    def __init__(self, config: OpenAIConfig) -> None:
        if not config.resolved_api_key():
            raise ValueError(
                f"Missing API key: set {config.api_key_env} or api_key in config"
            )
        self.config = config
        self.client = httpx.Client(
            base_url=config.base_url,
            headers={"Authorization": f"Bearer {config.resolved_api_key()}"},
            timeout=120.0,
        )

    def _to_openai_messages(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "tool":
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.tool_call_id,
                        "content": msg.content or "",
                    }
                )
            elif msg.role == "assistant" and msg.tool_calls:
                result.append(
                    {
                        "role": "assistant",
                        "content": msg.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.arguments),
                                },
                            }
                            for tc in msg.tool_calls
                        ],
                    }
                )
            else:
                result.append({"role": msg.role, "content": msg.content or ""})
        return result

    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResponse:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": self._to_openai_messages(messages),
        }
        if tools:
            payload["tools"] = tools

        response = self.client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()
        choice = data["choices"][0]
        msg = choice["message"]

        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            args = fn.get("arguments", "{}")
            if isinstance(args, str):
                args = json.loads(args)
            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    name=fn.get("name", ""),
                    arguments=args,
                )
            )

        return ChatResponse(
            message=ChatMessage(
                role="assistant",
                content=msg.get("content"),
                tool_calls=tool_calls,
            ),
            finish_reason=choice.get("finish_reason", "stop"),
        )
