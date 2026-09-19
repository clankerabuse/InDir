from __future__ import annotations

import fnmatch
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


def is_command_blocked(command: str, blocklist: list[str]) -> str | None:
    normalized = command.strip().lower()
    for pattern in blocklist:
        if pattern.lower() in normalized:
            return pattern
    return None


def log_command(directory: Path, command: str, returncode: int) -> None:
    ensure_state_dir()
    timestamp = datetime.now(timezone.utc).isoformat()
    with HISTORY_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{timestamp} | {directory} | rc={returncode} | {command}\n")


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
