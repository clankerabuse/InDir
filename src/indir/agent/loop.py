from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from indir.agent.tools import (
    CommandResult,
    format_command_result,
    list_directory,
    run_command,
)
from indir.backends.base import TOOL_DEFINITIONS, ChatBackend, ChatMessage
from indir.config import AppConfig
from indir.context import build_system_prompt, resolve_run_command

# Keep recent turns only so long chats don't keep growing prefill cost.
_MAX_HISTORY_MESSAGES = 24


class EventType(str, Enum):
    ASSISTANT_TEXT = "assistant_text"
    THINKING = "thinking"
    TOOL_RESULT = "tool_result"
    COMMAND_PENDING = "command_pending"
    COMMAND_RESULT = "command_result"
    ERROR = "error"


@dataclass
class AgentEvent:
    type: EventType
    content: str
    command: str | None = None
    tool_call_id: str | None = None


@dataclass
class PendingCommand:
    tool_call_id: str
    command: str


@dataclass
class AgentSession:
    directory: Path
    config: AppConfig
    backend: ChatBackend
    messages: list[ChatMessage] = field(default_factory=list)
    pending_commands: list[PendingCommand] = field(default_factory=list)
    _confirm_callback: Callable[[str], bool] | None = field(default=None, repr=False)
    _cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _active_proc: subprocess.Popen[str] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.messages:
            self.messages.append(
                ChatMessage(role="system", content=build_system_prompt(self.directory))
            )

    def set_confirm_callback(self, callback: Callable[[str], bool]) -> None:
        self._confirm_callback = callback

    def clear_cancel(self) -> None:
        self._cancel_event.clear()

    def request_cancel(self) -> str:
        """Request cancel and return a short status explaining what we're waiting on."""
        self._cancel_event.set()
        proc = self._active_proc
        killing_command = proc is not None and proc.poll() is None
        abort = getattr(self.backend, "abort", None)
        if callable(abort):
            try:
                abort()
            except Exception:
                pass
        if killing_command and proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
            return "Stopping — ending the running command…"
        return "Stopping — waiting for the model connection to close…"

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def cancel_wait_hint(self) -> str:
        """Explain a slow cancel for the status line."""
        proc = self._active_proc
        if proc is not None and proc.poll() is None:
            return "Still stopping — waiting for the shell command to exit…"
        return "Still stopping — the API connection can take a few seconds to drop…"

    def _cancelled_event(self) -> AgentEvent:
        return AgentEvent(type=EventType.ASSISTANT_TEXT, content="Stopped.")

    def _run_shell(self, command: str) -> CommandResult:
        def on_start(proc: subprocess.Popen[str]) -> None:
            self._active_proc = proc

        def on_finish() -> None:
            self._active_proc = None

        return run_command(
            self.directory,
            command,
            self.config.execution,
            cancel_event=self._cancel_event,
            on_start=on_start,
            on_finish=on_finish,
        )

    def _messages_for_api(self) -> list[ChatMessage]:
        if len(self.messages) <= _MAX_HISTORY_MESSAGES + 1:
            return list(self.messages)
        system = [m for m in self.messages if m.role == "system"][:1]
        rest = [m for m in self.messages if m.role != "system"]
        return system + rest[-_MAX_HISTORY_MESSAGES:]

    def _finish_successful_command(self) -> list[AgentEvent]:
        done = ChatMessage(role="assistant", content="Done.")
        self.messages.append(done)
        return [AgentEvent(type=EventType.ASSISTANT_TEXT, content="Done.")]

    def _execute_tool(self, name: str, arguments: dict, tool_call_id: str) -> AgentEvent | None:
        if name == "list_directory":
            pattern = arguments.get("pattern")
            result = list_directory(self.directory, pattern)
            self.messages.append(
                ChatMessage(
                    role="tool",
                    content=result,
                    tool_call_id=tool_call_id,
                    name=name,
                )
            )
            return AgentEvent(type=EventType.TOOL_RESULT, content=result)

        if name == "run_command":
            command = resolve_run_command(self.directory, arguments.get("command", ""))
            if self.config.execution.mode == "auto":
                cmd_result = self._run_shell(command)
                formatted = format_command_result(cmd_result)
                self.messages.append(
                    ChatMessage(
                        role="tool",
                        content=formatted,
                        tool_call_id=tool_call_id,
                        name=name,
                    )
                )
                return AgentEvent(
                    type=EventType.COMMAND_RESULT,
                    content=formatted,
                    command=command,
                    tool_call_id=tool_call_id,
                )

            self.pending_commands.append(
                PendingCommand(tool_call_id=tool_call_id, command=command)
            )
            return AgentEvent(
                type=EventType.COMMAND_PENDING,
                content=f"Proposed command: {command}",
                command=command,
                tool_call_id=tool_call_id,
            )

        error = f"Unknown tool: {name}"
        self.messages.append(
            ChatMessage(role="tool", content=error, tool_call_id=tool_call_id, name=name)
        )
        return AgentEvent(type=EventType.ERROR, content=error)

    def approve_command(self, tool_call_id: str, command: str | None = None) -> list[AgentEvent]:
        pending = next(
            (p for p in self.pending_commands if p.tool_call_id == tool_call_id), None
        )
        if not pending:
            return [AgentEvent(type=EventType.ERROR, content="No pending command found")]

        self.pending_commands.remove(pending)
        cmd = command if command is not None else pending.command
        cmd_result = self._run_shell(cmd)
        formatted = format_command_result(cmd_result)
        self.messages.append(
            ChatMessage(
                role="tool",
                content=formatted,
                tool_call_id=tool_call_id,
                name="run_command",
            )
        )
        events = [
            AgentEvent(
                type=EventType.COMMAND_RESULT,
                content=formatted,
                command=cmd,
                tool_call_id=tool_call_id,
            )
        ]
        if self.is_cancelled():
            events.append(self._cancelled_event())
            return events
        # Successful commands skip a follow-up model round-trip.
        if cmd_result.returncode == 0 and not cmd_result.blocked:
            events.extend(self._finish_successful_command())
            return events
        events.extend(self._continue_after_tool())
        return events

    def reject_command(self, tool_call_id: str) -> list[AgentEvent]:
        pending = next(
            (p for p in self.pending_commands if p.tool_call_id == tool_call_id), None
        )
        if not pending:
            return [AgentEvent(type=EventType.ERROR, content="No pending command found")]

        self.pending_commands.remove(pending)
        self.messages.append(
            ChatMessage(
                role="tool",
                content="Command rejected by user.",
                tool_call_id=tool_call_id,
                name="run_command",
            )
        )
        return self._continue_after_tool()

    def _continue_after_tool(self) -> list[AgentEvent]:
        return self._run_loop(max_iterations=5)

    @staticmethod
    def _summarize_tool_calls(tool_calls: list) -> str:
        parts: list[str] = []
        for tc in tool_calls:
            if tc.name == "run_command":
                command = tc.arguments.get("command", "")
                if command:
                    parts.append(f"I'll run: {command}")
            elif tc.name == "list_directory":
                pattern = tc.arguments.get("pattern")
                if pattern:
                    parts.append(f"I'll list files matching: {pattern}")
                else:
                    parts.append("I'll list the directory contents.")
        return "\n".join(parts)

    def send_user_message(self, text: str) -> list[AgentEvent]:
        self.messages.append(ChatMessage(role="user", content=text))
        return self._run_loop()

    def _run_loop(self, max_iterations: int = 10) -> list[AgentEvent]:
        events: list[AgentEvent] = []

        for _ in range(max_iterations):
            if self.is_cancelled():
                events.append(self._cancelled_event())
                return events

            try:
                response = self.backend.chat(self._messages_for_api(), tools=TOOL_DEFINITIONS)
            except Exception as exc:
                if self.is_cancelled():
                    events.append(self._cancelled_event())
                    return events
                events.append(
                    AgentEvent(type=EventType.ERROR, content=f"Backend error: {exc}")
                )
                return events

            if self.is_cancelled():
                events.append(self._cancelled_event())
                return events

            assistant_msg = response.message
            self.messages.append(assistant_msg)

            if assistant_msg.thinking:
                events.append(
                    AgentEvent(
                        type=EventType.THINKING,
                        content=assistant_msg.thinking,
                    )
                )

            if assistant_msg.content:
                events.append(
                    AgentEvent(
                        type=EventType.ASSISTANT_TEXT,
                        content=assistant_msg.content,
                    )
                )
            elif assistant_msg.tool_calls:
                summary = self._summarize_tool_calls(assistant_msg.tool_calls)
                if summary:
                    events.append(
                        AgentEvent(
                            type=EventType.ASSISTANT_TEXT,
                            content=summary,
                        )
                    )

            if not assistant_msg.tool_calls:
                break

            saw_list = False
            command_failed = False
            for tc in assistant_msg.tool_calls:
                if self.is_cancelled():
                    events.append(self._cancelled_event())
                    return events
                event = self._execute_tool(tc.name, tc.arguments, tc.id)
                if event:
                    events.append(event)
                if event and event.type == EventType.COMMAND_PENDING:
                    return events
                if tc.name == "list_directory":
                    saw_list = True
                if event and event.type == EventType.COMMAND_RESULT:
                    if "Exit code: 0" not in event.content or "Command blocked" in event.content:
                        command_failed = True
                    if self.is_cancelled():
                        events.append(self._cancelled_event())
                        return events

            # Skip another model call when auto-mode commands all succeeded.
            if (
                not saw_list
                and not command_failed
                and all(tc.name == "run_command" for tc in assistant_msg.tool_calls)
            ):
                events.extend(self._finish_successful_command())
                break

        return events
