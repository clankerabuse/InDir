from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from indir.agent.loop import AgentEvent, AgentSession, EventType
from indir.backends.registry import create_backend
from indir.config import AppConfig
from indir.ui.spinner import Spinner

_EXIT_CODE_RE = re.compile(r"^Exit code: (-?\d+)$")

_NOT_CONFIGURED_HINT = (
    "Configure a backend first: run `indir .`, open the settings gear, "
    "pick provider/model/API key, then retry."
)


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def _command_result_parts(content: str) -> tuple[int, str]:
    """Split format_command_result() output into (exit code, human body)."""
    exit_code = 0
    body: list[str] = []
    for line in content.splitlines():
        if line.startswith("Command: "):
            continue
        match = _EXIT_CODE_RE.match(line)
        if match:
            exit_code = int(match.group(1))
            continue
        body.append(line)
    return exit_code, "\n".join(body).strip()


def _render(event: AgentEvent, out: TextIO, err: TextIO) -> int:
    """Print one event. Returns non-zero when it represents a failure."""
    if event.type == EventType.ASSISTANT_TEXT:
        text = (event.content or "").strip()
        if text:
            out.write(f"{text}\n")
        return 0

    if event.type == EventType.TOOL_RESULT:
        out.write(_indent(event.content) + "\n")
        return 0

    if event.type == EventType.COMMAND_RESULT:
        exit_code, body = _command_result_parts(event.content)
        if event.note:
            # Say why nothing was asked: an unprompted command must never look
            # like the user approved it.
            err.write(f"InDir: {event.note}\n")
        out.write(f"$ {event.command}\n")
        if body:
            out.write(_indent(body) + "\n")
        if "Command blocked" in event.content:
            err.write("InDir: command blocked by the safety blocklist.\n")
            return 1
        if exit_code != 0:
            err.write(f"InDir: command exited with status {exit_code}\n")
            return 1
        return 0

    if event.type == EventType.ERROR:
        err.write(f"InDir: {event.content}\n")
        return 1

    if event.type == EventType.VERIFICATION:
        # A failed check is the exit status: this is what stops a script from
        # treating "the model said it did it" as success.
        if event.ok is False:
            err.write(f"InDir: {event.content}\n")
            return 1
        return 0

    if event.type == EventType.SCOPE_WIDENED:
        err.write(f"InDir: scope widened to the whole folder ({event.content})\n")
        return 0

    # THINKING stays quiet: a terminal run prints answers, not the model's notes.
    return 0


def _prompt(
    command: str,
    stdin: TextIO,
    out: TextIO,
    *,
    note: str | None = None,
    warnings: tuple[str, ...] = (),
    remember: str | None = None,
) -> str:
    """Ask about one command. Returns "y", "n" or "a" (always this kind).

    `remember` names what "always" would cover; it is None for a command the
    policy will always ask about, and then the option is not offered.
    """
    out.write(f"\nProposed command:\n{_indent(command)}\n")
    if note:
        out.write(f"  {note}\n")
    for warning in warnings:
        out.write(f"  ! {warning}\n")
    if remember:
        out.write("Run it? [y]es / [N]o / [a]lways for this kind: ")
    else:
        out.write("Run it? [y]es / [N]o: ")
    out.flush()
    line = stdin.readline()
    if not line:
        # EOF (Ctrl-D): treat as "no" rather than silently running anything.
        return "n"
    choice = line.strip().lower()
    if choice.startswith("y"):
        return "y"
    if choice.startswith("a") and remember:
        return "a"
    return "n"


def run_request(
    directory: Path,
    config: AppConfig,
    request: str,
    *,
    focus_file: Path | None = None,
    assume_yes: bool = False,
    stdin: TextIO | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    interactive: bool | None = None,
    ask: Callable[[str], str] | None = None,
) -> int:
    """Run one request against `directory` from the terminal, no window.

    Commands go through the same approval the GUI uses, expressed as a Y/n
    prompt; `assume_yes` (or execution.mode = "auto") answers yes up front.
    Without a terminal on stdin the run refuses to execute anything instead of
    hanging or guessing.
    """
    out_stream: TextIO = out if out is not None else sys.stdout
    err_stream: TextIO = err if err is not None else sys.stderr
    in_stream: TextIO = stdin if stdin is not None else sys.stdin
    is_interactive = (
        interactive
        if interactive is not None
        else bool(getattr(in_stream, "isatty", lambda: False)())
    )

    try:
        backend = create_backend(config, directory)
    except Exception as exc:
        err_stream.write(f"InDir: backend not ready — {exc}\n{_NOT_CONFIGURED_HINT}\n")
        return 1

    session = AgentSession(directory, config, backend, focus_file=focus_file)
    # --yes and auto mode approve everything up front; smart mode is handled by
    # the session's approval policy, which still stops for guarded commands.
    always = assume_yes or config.execution.mode == "auto"

    # A live indicator while waiting, so a slow model or ffmpeg run does not look
    # like a hung terminal. Silent unless a stream is a TTY.
    spinner = Spinner.maybe(err_stream, out_stream)
    session.set_progress_callback(
        lambda phase, detail: spinner.set_label(
            f"running {detail}" if phase == "command" and detail else "thinking"
        )
    )

    try:
        with spinner:
            events = session.send_user_message(request)
    except KeyboardInterrupt:
        err_stream.write("\nInDir: interrupted.\n")
        return 130

    status = 0
    while True:
        for event in events:
            # Flush per event so a piped run reads in the order it happened.
            out_stream.flush()
            if _render(event, out_stream, err_stream):
                status = 1
        # Flush before anything goes to stderr, so a piped run reads in order.
        out_stream.flush()

        pending = session.pending_commands
        if not pending:
            break
        next_command = pending[0]

        if not always:
            if not is_interactive and ask is None:
                err_stream.write(
                    "InDir: not running this command because stdin is not a "
                    f"terminal:\n{_indent(next_command.command)}\n"
                    "Re-run with --yes to approve commands up front.\n"
                )
                return 1
            remember = (
                next_command.kind
                if next_command.grantable and next_command.kind
                else None
            )
            choice = (
                ask(next_command.command)
                if ask is not None
                else _prompt(
                    next_command.command,
                    in_stream,
                    out_stream,
                    note=next_command.reason or None,
                    warnings=next_command.warnings,
                    remember=remember,
                )
            )
            # "a" remembers this kind of write, not every command in the run:
            # a conversion should not pre-approve a deletion three turns later.
            if choice == "a":
                session.remember(next_command.kind)
                err_stream.write(
                    f"InDir: won't ask again — {next_command.reason}.\n"
                )
            elif choice != "y":
                err_stream.write("InDir: command skipped.\n")
                try:
                    with spinner:
                        events = session.reject_command(next_command.tool_call_id)
                except KeyboardInterrupt:
                    err_stream.write("\nInDir: interrupted.\n")
                    return 130
                continue

        try:
            with spinner:
                events = session.approve_command(next_command.tool_call_id)
        except KeyboardInterrupt:
            err_stream.write("\nInDir: interrupted.\n")
            return 130

    out_stream.flush()
    return status
