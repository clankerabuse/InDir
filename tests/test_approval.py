"""The approval buttons must always do something.

A provider that omits the tool-call id (or sends null) used to produce an
approval card that could not be answered: the id keyed every lookup, so the
Approve/Deny buttons were wired to nothing and the click disappeared without a
message. These tests pin the guarantee — a click on Approve either runs the
command or says why it could not.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from indir.agent.loop import AgentSession, EventType
from indir.backends.base import ChatBackend, ChatMessage, ChatResponse, ToolCall
import indir.ui.chat_window as chat_window
from indir.config import AppConfig


class StubBackend(ChatBackend):
    """Proposes one command, then says it is done. `tool_call_id` is what a
    misbehaving provider sends: "" or None."""

    def __init__(self, command: str, tool_call_id: str | None) -> None:
        self.command = command
        self.tool_call_id = tool_call_id
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return ChatResponse(
                message=ChatMessage(
                    role="assistant",
                    content="I'll do it.",
                    tool_calls=[
                        ToolCall(
                            id=self.tool_call_id,
                            name="run_command",
                            arguments={"command": self.command},
                        )
                    ],
                )
            )
        return ChatResponse(message=ChatMessage(role="assistant", content="Done."))


def _config() -> AppConfig:
    config = AppConfig()
    config.execution.mode = "confirm"
    config.execution.verify = False
    return config


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _pump(app: QApplication, predicate, timeout: float = 10.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


def _finish(app: QApplication, window) -> None:
    """Let the worker stop before Qt tears the window down.

    A QThread still running when its wrapper is collected aborts the process,
    so the tests wait for idle and close cleanly instead of relying on luck.
    """
    _pump(app, lambda: not window._worker_running(), 10)
    window.close()
    window.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("tool_call_id", ["", None])
def test_session_approves_command_without_provider_id(tmp_path: Path, tool_call_id) -> None:
    """The id the provider forgot is filled in before the command is filed."""
    backend = StubBackend("touch made.txt", tool_call_id)
    session = AgentSession(tmp_path, _config(), backend)

    events = session.send_user_message("make the file")
    pending = [e for e in events if e.type == EventType.COMMAND_PENDING]
    assert len(pending) == 1
    assert pending[0].tool_call_id, "a pending command must always be identifiable"

    result = session.approve_command(pending[0].tool_call_id or "")
    assert any(e.type == EventType.COMMAND_RESULT for e in result)
    assert (tmp_path / "made.txt").exists()


def test_window_approve_runs_command_without_provider_id(tmp_path: Path, monkeypatch) -> None:
    """Clicking Approve on an id-less proposal runs the command."""
    target = tmp_path / "made.txt"
    backend = StubBackend(f"touch {target.name}", "")
    monkeypatch.setattr(chat_window, "create_backend", lambda cfg, directory: backend)

    app = _app()
    window = chat_window.ChatWindow(tmp_path, _config())
    window.show()
    try:
        window.input_box.setPlainText("make the file")
        window._on_send()

        assert _pump(app, lambda: window._pending_tool_call_id is not None)
        assert window.approve_btn.isEnabled()

        window.approve_btn.click()
        assert _pump(app, lambda: target.exists())
        assert not window.session.pending_commands
    finally:
        _finish(app, window)


def test_window_approve_with_nothing_pending_says_so(tmp_path: Path) -> None:
    """A click with no command waiting reports instead of doing nothing."""
    app = _app()
    window = chat_window.ChatWindow(tmp_path, _config())
    window.show()
    try:
        window._on_approve_command()
        app.processEvents()
        assert "waiting for approval" in window.status_label.text().lower()
    finally:
        _finish(app, window)


def test_window_approve_recovers_from_a_stale_id(tmp_path: Path, monkeypatch) -> None:
    """A card whose id no longer matches still acts on the command it shows."""
    target = tmp_path / "made.txt"
    backend = StubBackend(f"touch {target.name}", "call_known")
    monkeypatch.setattr(chat_window, "create_backend", lambda cfg, directory: backend)

    app = _app()
    window = chat_window.ChatWindow(tmp_path, _config())
    window.show()
    try:
        window.input_box.setPlainText("make the file")
        window._on_send()
        assert _pump(app, lambda: window._pending_tool_call_id is not None)

        window._pending_tool_call_id = "call_that_no_longer_exists"
        window.approve_btn.click()

        assert _pump(app, lambda: target.exists())
        assert not window.session.pending_commands
    finally:
        _finish(app, window)


def test_window_restores_a_card_the_session_still_holds(tmp_path: Path, monkeypatch) -> None:
    """A pending command that lost its card gets the card back."""
    backend = StubBackend("touch made.txt", "call_known")
    monkeypatch.setattr(chat_window, "create_backend", lambda cfg, directory: backend)

    app = _app()
    window = chat_window.ChatWindow(tmp_path, _config())
    window.show()
    try:
        window.input_box.setPlainText("make the file")
        window._on_send()
        assert _pump(app, lambda: window._pending_tool_call_id is not None)

        window._clear_approval()
        assert window._pending_tool_call_id is None

        window._restore_pending_approval()
        app.processEvents()
        assert window._pending_tool_call_id == "call_known"
    finally:
        _finish(app, window)


def test_closing_during_a_command_stops_the_worker(tmp_path: Path, monkeypatch) -> None:
    """Closing mid-command must not leave a QThread running behind the window."""
    backend = StubBackend("sleep 30", "call_slow")
    monkeypatch.setattr(chat_window, "create_backend", lambda cfg, directory: backend)

    app = _app()
    window = chat_window.ChatWindow(tmp_path, _config())
    window.show()
    try:
        window.input_box.setPlainText("wait a while")
        window._on_send()
        assert _pump(app, lambda: window._pending_tool_call_id is not None)

        window.approve_btn.click()
        assert _pump(app, lambda: window._worker_running(), 5)

        started = time.time()
        window.close()
        assert time.time() - started < 10
        assert not window._worker_running()
    finally:
        _finish(app, window)


def test_window_deny_clears_the_card(tmp_path: Path, monkeypatch) -> None:
    """Deny answers the command instead of leaving it pending forever."""
    backend = StubBackend("touch made.txt", "call_deny")
    monkeypatch.setattr(chat_window, "create_backend", lambda cfg, directory: backend)

    app = _app()
    window = chat_window.ChatWindow(tmp_path, _config())
    window.show()
    try:
        window.input_box.setPlainText("make the file")
        window._on_send()
        assert _pump(app, lambda: window._pending_tool_call_id is not None)

        window.deny_btn.click()
        assert _pump(app, lambda: not window._worker_running())
        app.processEvents()

        assert not window.session.pending_commands
        assert window._pending_tool_call_id is None
        assert not (tmp_path / "made.txt").exists()
    finally:
        _finish(app, window)


def test_window_approve_with_an_emptied_preview_runs_the_command(
    tmp_path: Path, monkeypatch
) -> None:
    """Clearing the preview box must not turn into running an empty command.

    `approve_command` treats an empty string as the command to run: the shell
    exits 0, the turn reports "Done." and nothing happened.
    """
    target = tmp_path / "made.txt"
    backend = StubBackend(f"touch {target.name}", "call_empty")
    monkeypatch.setattr(chat_window, "create_backend", lambda cfg, directory: backend)

    app = _app()
    window = chat_window.ChatWindow(tmp_path, _config())
    window.show()
    try:
        window.input_box.setPlainText("make the file")
        window._on_send()
        assert _pump(app, lambda: window._pending_tool_call_id is not None)

        window._command_preview.command_edit.setPlainText("   ")
        window.approve_btn.click()

        assert _pump(app, lambda: target.exists())
    finally:
        _finish(app, window)
