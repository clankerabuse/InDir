from __future__ import annotations

from pathlib import Path

import pytest

from indir.agent.loop import AgentSession, EventType
from indir.backends.ollama import OllamaBackend
from indir.config import AppConfig, OllamaConfig


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_agent_empty_assistant_text_gets_summary(httpx_mock, tmp_path: Path) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:11434/api/chat",
        json={
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "run_command",
                            "arguments": {"command": "echo hello"},
                        }
                    }
                ],
            }
        },
    )

    config = AppConfig()
    backend = OllamaBackend(OllamaConfig())
    session = AgentSession(tmp_path, config, backend)
    events = session.send_user_message("say hello")

    text = [e for e in events if e.type == EventType.ASSISTANT_TEXT]
    assert len(text) == 1
    assert "echo hello" in text[0].content


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_agent_loop_with_tool_call(httpx_mock, tmp_path: Path) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:11434/api/chat",
        json={
            "message": {
                "role": "assistant",
                "content": "I'll list the directory.",
                "tool_calls": [
                    {
                        "function": {
                            "name": "list_directory",
                            "arguments": {},
                        }
                    }
                ],
            }
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:11434/api/chat",
        json={
            "message": {
                "role": "assistant",
                "content": "Done listing.",
            }
        },
    )

    (tmp_path / "test.txt").write_text("hi")
    config = AppConfig()
    backend = OllamaBackend(OllamaConfig())
    session = AgentSession(tmp_path, config, backend)
    events = session.send_user_message("list files")

    assert any(e.type == EventType.ASSISTANT_TEXT for e in events)
    assert any(e.type == EventType.TOOL_RESULT for e in events)


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_agent_command_pending_in_confirm_mode(httpx_mock, tmp_path: Path) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:11434/api/chat",
        json={
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "function": {
                            "name": "run_command",
                            "arguments": {"command": "echo hello"},
                        }
                    }
                ],
            }
        },
    )

    config = AppConfig()
    config.execution.mode = "confirm"
    backend = OllamaBackend(OllamaConfig())
    session = AgentSession(tmp_path, config, backend)
    events = session.send_user_message("say hello")

    pending = [e for e in events if e.type == EventType.COMMAND_PENDING]
    assert len(pending) == 1
    assert pending[0].command == "echo hello"

    assert not any(e.type == EventType.COMMAND_RESULT for e in events)
