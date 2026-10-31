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
from indir.agent.policy import ApprovalPolicy, Risk
from indir.agent.verify import TurnVerifier, closing_is_a_claim, correction_prompt
from indir.backends.base import TOOL_DEFINITIONS, WIDEN_SCOPE_TOOL, ChatBackend, ChatMessage
from indir.config import AppConfig
from indir.context import build_directory_summary, build_system_prompt, resolve_run_command

# Keep recent turns only so long chats don't keep growing prefill cost.
_MAX_HISTORY_MESSAGES = 24

# How many times a failed check is handed back to the model before the turn is
# reported as not done. Bounded: a model that cannot act will not start acting
# on the fifth request either, and every retry costs a round-trip.
_MAX_VERIFY_ROUNDS = 2


class EventType(str, Enum):
    ASSISTANT_TEXT = "assistant_text"
    THINKING = "thinking"
    TOOL_RESULT = "tool_result"
    COMMAND_PENDING = "command_pending"
    COMMAND_RESULT = "command_result"
    ERROR = "error"
    SCOPE_WIDENED = "scope_widened"
    VERIFICATION = "verification"


@dataclass
class AgentEvent:
    type: EventType
    content: str
    command: str | None = None
    tool_call_id: str | None = None
    ok: bool | None = None
    # Why a command ran without being asked about (read-only, or a kind the
    # user already said "always" to).
    note: str | None = None


@dataclass
class PendingCommand:
    tool_call_id: str
    command: str
    kind: str = ""
    grantable: bool = False
    reason: str = ""
    warnings: tuple[str, ...] = ()


@dataclass
class AgentSession:
    directory: Path
    config: AppConfig
    backend: ChatBackend
    focus_file: Path | None = None
    messages: list[ChatMessage] = field(default_factory=list)
    pending_commands: list[PendingCommand] = field(default_factory=list)
    scope_widened: bool = False
    _confirm_callback: Callable[[str], bool] | None = field(default=None, repr=False)
    _progress_callback: Callable[[str, str | None], None] | None = field(
        default=None, repr=False
    )
    _cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _active_proc: subprocess.Popen[str] | None = field(default=None, repr=False)
    _verifier: TurnVerifier | None = field(default=None, repr=False)
    _policy: ApprovalPolicy | None = field(default=None, repr=False)
    _turn_replies: list[str] = field(default_factory=list, repr=False)
    _verify_rounds: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        self._verifier = TurnVerifier(self.directory)
        self._policy = ApprovalPolicy(list(self.config.execution.blocklist))
        if not self.messages:
            self.messages.append(
                ChatMessage(
                    role="system",
                    content=build_system_prompt(self.directory, self.focus_file),
                )
            )

    def set_confirm_callback(self, callback: Callable[[str], bool]) -> None:
        self._confirm_callback = callback

    def set_progress_callback(
        self, callback: Callable[[str, str | None], None] | None
    ) -> None:
        """Called with ("model", None) before a model call and
        ("command", <command>) before a shell command, so a UI can show what the
        agent is waiting on. Progress reporting must never break the run."""
        self._progress_callback = callback

    def _progress(self, phase: str, detail: str | None = None) -> None:
        callback = self._progress_callback
        if callback is None:
            return
        try:
            callback(phase, detail)
        except Exception:
            pass

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
        self._progress("command", command)

        def on_start(proc: subprocess.Popen[str]) -> None:
            self._active_proc = proc

        def on_finish() -> None:
            self._active_proc = None

        result = run_command(
            self.directory,
            command,
            self.config.execution,
            cancel_event=self._cancel_event,
            on_start=on_start,
            on_finish=on_finish,
        )
        if self._verifier is not None:
            self._verifier.record_command(command, result.returncode)
        return result

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

    def _verification_enabled(self) -> bool:
        return bool(getattr(self.config.execution, "verify", True))

    def _close_turn(
        self,
        events: list[AgentEvent],
        *,
        synthesize: bool,
        closing: str | None = None,
    ) -> bool:
        """Decide whether this turn is finished.

        Returns True when the turn is over and False when the work was not
        verified and the model has been given the discrepancy to fix. Nothing
        is reported as done on the strength of a reply alone, and a closing
        reply is only shown once the filesystem agrees with it.
        """
        if not self._verification_enabled() or self._verifier is None:
            if closing:
                events.append(AgentEvent(type=EventType.ASSISTANT_TEXT, content=closing))
            if synthesize:
                events.extend(self._finish_successful_command())
            return True

        verdict = self._verifier.verdict(self._turn_replies)
        check = AgentEvent(
            type=EventType.VERIFICATION, content=verdict.summary, ok=verdict.ok
        )
        if verdict.ok:
            if closing:
                events.append(
                    AgentEvent(type=EventType.ASSISTANT_TEXT, content=closing)
                )
            if verdict.checked:
                events.append(check)
            if synthesize:
                events.extend(self._finish_successful_command())
            return True

        if self._verify_rounds < _MAX_VERIFY_ROUNDS and not self.is_cancelled():
            self._verify_rounds += 1
            # The unverified claim stays out of the transcript: it is not
            # information the user should read. A system message would replace
            # the system prompt on the Anthropic backend, so the correction
            # goes in as a clearly-marked user turn.
            self.messages.append(
                ChatMessage(role="user", content=correction_prompt(verdict))
            )
            return False

        # Out of retries: report the failure instead of letting the claim stand.
        # An explanation still reaches the user; a success claim never does.
        if closing and not closing_is_a_claim(closing, verdict.claims):
            events.append(AgentEvent(type=EventType.ASSISTANT_TEXT, content=closing))
        if verdict.checked:
            events.append(check)
        # The verdict event is what reports this: the CLI turns it into a
        # non-zero exit, the window into an error bubble. Adding an assistant
        # message as well would print the same failure twice.
        self.messages.append(
            ChatMessage(role="assistant", content=verdict.failure_message())
        )
        return True

    def _abandon_turn(self) -> list[AgentEvent]:
        """Ran out of iterations without ever closing the turn."""
        if self._verifier is None or not self._verification_enabled():
            return []
        verdict = self._verifier.verdict(self._turn_replies)
        if verdict.ok:
            return []
        self.messages.append(
            ChatMessage(role="assistant", content=verdict.failure_message())
        )
        return [
            AgentEvent(
                type=EventType.VERIFICATION, content=verdict.summary, ok=False
            )
        ]

    def _runs_unprompted(self, decision) -> bool:
        """True when a decision needs no approval this time."""
        if decision is None:
            return False
        if decision.runs_without_asking:
            return True
        return bool(
            self._policy is not None
            and decision.can_remember
            and self._policy.granted(decision.kind)
        )

    def _auto_note(self, decision) -> str | None:
        if decision is None:
            return None
        if decision.risk is Risk.BLOCKED:
            return f"refused — {decision.note()}"
        if decision.runs_without_asking:
            # A read-only command cannot change anything: asking about it just
            # trains the user to approve without reading.
            return f"ran without asking — {decision.note()}"
        if self._runs_unprompted(decision):
            return f"ran without asking — you allowed {decision.kind} in this chat"
        return None

    def _execute_tool(self, name: str, arguments: dict, tool_call_id: str) -> AgentEvent | None:
        if name == "list_directory":
            if self._verifier is not None:
                self._verifier.record_inspection()
            if self.focus_file is not None and not self.scope_widened:
                result = (
                    f"This chat is focused on {self.focus_file.name} only. "
                    "Other files are out of scope. If the user asked to include "
                    "the folder or other files, call widen_scope."
                )
            else:
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
            focus = None if self.scope_widened else self.focus_file
            command = resolve_run_command(
                self.directory, arguments.get("command", ""), focus
            )
            mode = self.config.execution.mode
            decision = (
                self._policy.decide(command, self.directory)
                if mode == "smart" and self._policy is not None
                else None
            )
            note = self._auto_note(decision)
            if mode == "auto" or self._runs_unprompted(decision):
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
                    note=note,
                )

            self.pending_commands.append(
                PendingCommand(
                    tool_call_id=tool_call_id,
                    command=command,
                    kind=decision.kind if decision is not None else "",
                    grantable=decision.can_remember if decision is not None else False,
                    reason=decision.reason if decision is not None else "",
                    warnings=decision.warnings if decision is not None else (),
                )
            )
            return AgentEvent(
                type=EventType.COMMAND_PENDING,
                content=f"Proposed command: {command}",
                command=command,
                tool_call_id=tool_call_id,
                note=decision.reason if decision is not None else None,
            )

        if name == "widen_scope":
            return self._widen_scope(tool_call_id)

        error = f"Unknown tool: {name}"
        self.messages.append(
            ChatMessage(role="tool", content=error, tool_call_id=tool_call_id, name=name)
        )
        return AgentEvent(type=EventType.ERROR, content=error)

    def _tool_definitions(self) -> list[dict]:
        if self.focus_file is not None and not self.scope_widened:
            return [*TOOL_DEFINITIONS, WIDEN_SCOPE_TOOL]
        return TOOL_DEFINITIONS

    def _widen_scope(self, tool_call_id: str) -> AgentEvent:
        if self.focus_file is None or self.scope_widened:
            detail = f"Scope is already the directory {self.directory}."
            self.messages.append(
                ChatMessage(
                    role="tool",
                    content=detail,
                    tool_call_id=tool_call_id,
                    name="widen_scope",
                )
            )
            return AgentEvent(type=EventType.TOOL_RESULT, content=detail)

        self.scope_widened = True
        prompt = build_system_prompt(self.directory)
        for message in self.messages:
            if message.role == "system":
                message.content = prompt
                break
        summary = build_directory_summary(self.directory)
        detail = (
            f"Scope widened to {self.directory}.\n"
            f"{summary}\n"
            "You may now list and act on other files in this directory."
        )
        self.messages.append(
            ChatMessage(
                role="tool",
                content=detail,
                tool_call_id=tool_call_id,
                name="widen_scope",
            )
        )
        label = self.directory.name or str(self.directory)
        return AgentEvent(type=EventType.SCOPE_WIDENED, content=label)

    def approve_command(
        self, tool_call_id: str, command: str | None = None, always: bool = False
    ) -> list[AgentEvent]:
        pending = next(
            (p for p in self.pending_commands if p.tool_call_id == tool_call_id), None
        )
        if not pending:
            return [AgentEvent(type=EventType.ERROR, content="No pending command found")]

        self.pending_commands.remove(pending)
        if always and pending.grantable:
            self.remember(pending.kind)
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
        # Successful commands skip a follow-up model round-trip, but only once
        # the filesystem shows the command did what it claimed.
        if cmd_result.returncode == 0 and not cmd_result.blocked:
            if self._close_turn(events, synthesize=True):
                return events
            events.extend(self._continue_after_tool())
            return events
        events.extend(self._continue_after_tool())
        return events

    def remember(self, kind: str) -> None:
        """Allow this kind of write for the rest of the chat (not just once)."""
        if self._policy is not None:
            self._policy.grant(kind)

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
            elif tc.name == "widen_scope":
                parts.append("I'll include the whole folder.")
        return "\n".join(parts)

    def send_user_message(self, text: str) -> list[AgentEvent]:
        self.messages.append(ChatMessage(role="user", content=text))
        # Baseline the directory now: the verdict compares against this.
        if self._verifier is not None:
            self._verifier.begin(text)
        self._turn_replies = []
        self._verify_rounds = 0
        return self._run_loop()

    def _run_loop(self, max_iterations: int = 10) -> list[AgentEvent]:
        events: list[AgentEvent] = []

        for _ in range(max_iterations):
            if self.is_cancelled():
                events.append(self._cancelled_event())
                return events

            self._progress("model")
            try:
                response = self.backend.chat(self._messages_for_api(), tools=self._tool_definitions())
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
                self._turn_replies.append(assistant_msg.content)
                # A closing reply is held back until verification passes; only
                # narration that accompanies a tool call is shown immediately.
                if assistant_msg.tool_calls:
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
                if not self._close_turn(
                    events, synthesize=False, closing=assistant_msg.content
                ):
                    continue
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
                if self._close_turn(events, synthesize=True):
                    break
                continue

        else:
            # Iterations ran out with a retry still outstanding.
            events.extend(self._abandon_turn())

        return events
