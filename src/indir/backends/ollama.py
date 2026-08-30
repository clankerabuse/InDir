from __future__ import annotations

import json
from typing import Any

import httpx

from indir.backends.base import ChatBackend, ChatMessage, ChatResponse, ToolCall
from indir.config import OllamaConfig


class OllamaBackend(ChatBackend):
    def __init__(self, config: OllamaConfig) -> None:
        self.config = config
        self.client = httpx.Client(base_url=config.base_url, timeout=120.0)

    def _to_ollama_messages(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "tool":
                result.append({"role": "tool", "content": msg.content or ""})
            elif msg.role == "assistant" and msg.tool_calls:
                result.append(
                    {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": tc.name,
                                    "arguments": tc.arguments,
                                }
                            }
                            for tc in msg.tool_calls
                        ],
                    }
                )
            else:
                result.append({"role": msg.role, "content": msg.content or ""})
        return result

    def _to_ollama_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "parameters": t["function"]["parameters"],
                },
            }
            for t in tools
        ]

    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResponse:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": self._to_ollama_messages(messages),
            "stream": False,
        }
        if tools:
            payload["tools"] = self._to_ollama_tools(tools)

        response = self.client.post("/api/chat", json=payload)
        if response.status_code == 404:
            try:
                err = response.json().get("error", "")
                if err:
                    raise RuntimeError(
                        f"Ollama: {err}. Run `ollama list` and set the exact model "
                        f"name in ~/.config/indir/config.toml."
                    ) from None
            except (ValueError, AttributeError):
                pass
        response.raise_for_status()
        data = response.json()
        msg = data.get("message", {})

        tool_calls: list[ToolCall] = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args)
            tool_calls.append(
                ToolCall(
                    id=f"call_{i}",
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
            finish_reason="tool_calls" if tool_calls else "stop",
        )
