from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from indir.config import ExecutionConfig, HISTORY_LOG, ensure_state_dir
from indir.context import build_directory_listing, format_size


@dataclass
class CommandResult:
    command: str
    stdout: str
    stderr: str
    returncode: int
    blocked: bool = False
    block_reason: str | None = None


def _squeeze(command: str) -> str:
    """Case-folded command with all whitespace and backslashes removed.

    Used for blocklist matching only: `rm -rf  /`, `r\\m -rf /` and `:(){:|:&};:`
    then normalise to the same form as their blocklist entries.
    """
    return re.sub(r"[\s\\]", "", command.lower())


def is_command_blocked(command: str, blocklist: list[str]) -> str | None:
    """Best-effort speed bump, NOT a security boundary.

    Shell quoting, variable expansion (`$(echo rm) -rf /`), flag reordering
    (`rm --recursive --force /`) and anything not on the list (dd, curl|sh,
    git clean -fdx) all get through. Confirm mode, where the user sees and
    approves the exact command, is the control that matters.
    """
    squeezed = _squeeze(command)
    for pattern in blocklist:
        needle = _squeeze(pattern)
        if needle and needle in squeezed:
            return pattern
    return None


def _single_line(text: str) -> str:
    """Collapse a command to one log line so it cannot forge extra entries."""
    return " ".join(text.replace("\r", "\n").split())


def log_command(directory: Path, command: str, returncode: int) -> None:
    ensure_state_dir()
    timestamp = datetime.now(timezone.utc).isoformat()
    # O_CREAT with 0o600 so the audit log is never briefly world-readable, and one
    # line per entry: the command is attacker-influenced (filenames reach it).
    fd = os.open(HISTORY_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(
            f"{timestamp} | {directory} | rc={returncode} | {_single_line(command)}\n"
        )


def list_directory(directory: Path, pattern: str | None = None) -> str:
    if not directory.is_dir():
        return f"Error: {directory} is not a directory"

    lines: list[str] = []
    try:
        entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        return f"Error listing directory: {exc}"

    for entry in entries:
        name = entry.name
        if pattern and not fnmatch.fnmatch(name, pattern):
            continue
        try:
            if entry.is_dir():
                lines.append(f"[dir]  {name}/")
            else:
                size = entry.stat().st_size
                lines.append(f"[file] {name} ({format_size(size)})")
        except OSError:
            lines.append(f"[?]    {name}")

    if not lines:
        return "No matching entries found." if pattern else "Directory is empty."
    return "\n".join(lines)


def run_command(
    directory: Path,
    command: str,
    config: ExecutionConfig,
    *,
    cancel_event: threading.Event | None = None,
    on_start: Callable[[subprocess.Popen[str]], None] | None = None,
    on_finish: Callable[[], None] | None = None,
) -> CommandResult:
    block_reason = is_command_blocked(command, config.blocklist)
    if block_reason:
        # Blocked attempts are the interesting ones for an audit trail.
        log_command(directory, command, -1)
        return CommandResult(
            command=command,
            stdout="",
            stderr=f"Command blocked (matched blocklist pattern: {block_reason})",
            returncode=-1,
            blocked=True,
            block_reason=block_reason,
        )

    if cancel_event is not None and cancel_event.is_set():
        return CommandResult(
            command=command,
            stdout="",
            stderr="Cancelled before start",
            returncode=-1,
        )

    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        return CommandResult(
            command=command,
            stdout="",
            stderr=str(exc),
            returncode=-1,
        )

    if on_start is not None:
        on_start(proc)

    try:
        try:
            stdout, stderr = proc.communicate(timeout=300)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return CommandResult(
                command=command,
                stdout="",
                stderr="Command timed out after 300 seconds",
                returncode=-1,
            )
    finally:
        if on_finish is not None:
            on_finish()

    if cancel_event is not None and cancel_event.is_set():
        return CommandResult(
            command=command,
            stdout=stdout or "",
            stderr=(stderr or "").strip() or "Cancelled by user",
            returncode=-1,
        )

    max_bytes = config.max_output_bytes
    if len(stdout) > max_bytes:
        stdout = stdout[:max_bytes] + f"\n... (truncated, {max_bytes} bytes max)"
    if len(stderr) > max_bytes:
        stderr = stderr[:max_bytes] + f"\n... (truncated, {max_bytes} bytes max)"

    log_command(directory, command, proc.returncode or 0)
    return CommandResult(
        command=command,
        stdout=stdout,
        stderr=stderr,
        returncode=proc.returncode or 0,
    )


def format_command_result(result: CommandResult) -> str:
    parts = [f"Command: {result.command}", f"Exit code: {result.returncode}"]
    if result.stdout:
        parts.append(f"stdout:\n{result.stdout}")
    if result.stderr:
        parts.append(f"stderr:\n{result.stderr}")
    return "\n".join(parts)
