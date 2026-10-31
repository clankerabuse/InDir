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
    assert any("echo hello" in e.content for e in text)
    # `echo` is read-only, so it runs without an approval prompt.
    results = [e for e in events if e.type == EventType.COMMAND_RESULT]
    assert results and results[0].note and "without asking" in results[0].note


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


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_agent_skips_followup_after_successful_auto_command(httpx_mock, tmp_path: Path) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:11434/api/chat",
        json={
            "message": {
                "role": "assistant",
                "content": "Running echo.",
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
    config.execution.mode = "auto"
    backend = OllamaBackend(OllamaConfig())
    session = AgentSession(tmp_path, config, backend)
    events = session.send_user_message("say hello")

    assert any(e.type == EventType.COMMAND_RESULT for e in events)
    done = [e for e in events if e.type == EventType.ASSISTANT_TEXT and e.content == "Done."]
    assert len(done) == 1
    # Only the initial model call — no second round-trip after success.
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_agent_approve_skips_followup_on_success(httpx_mock, tmp_path: Path) -> None:
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
    pending = next(e for e in events if e.type == EventType.COMMAND_PENDING)

    approve_events = session.approve_command(pending.tool_call_id or "")
    assert any(e.type == EventType.COMMAND_RESULT for e in approve_events)
    assert any(e.content == "Done." for e in approve_events)
    assert len(httpx_mock.get_requests()) == 1


def test_file_focus_blocks_listing_until_widened(tmp_path: Path) -> None:
    focus = tmp_path / "only.txt"
    focus.write_text("hi")
    (tmp_path / "other.txt").write_text("x")

    config = AppConfig()
    backend = OllamaBackend(OllamaConfig())
    session = AgentSession(tmp_path, config, backend, focus_file=focus)

    assert "only.txt" in session.messages[0].content
    assert "other.txt" not in session.messages[0].content
    assert any(tool["function"]["name"] == "widen_scope" for tool in session._tool_definitions())

    blocked = session._execute_tool("list_directory", {}, "list-1")
    assert blocked is not None
    assert "widen_scope" in blocked.content
    assert "other.txt" not in blocked.content

    widened = session._widen_scope("wide-1")
    assert widened.type == EventType.SCOPE_WIDENED
    assert session.scope_widened
    assert "primary reference for the chat" in session.messages[0].content
    assert "single file" not in session.messages[0].content
    assert all(tool["function"]["name"] != "widen_scope" for tool in session._tool_definitions())

    listed = session._execute_tool("list_directory", {}, "list-2")
    assert listed is not None
    assert "other.txt" in listed.content
    assert "only.txt" in listed.content
