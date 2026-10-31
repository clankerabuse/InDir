"""Which commands need the user's approval.

Confirm mode asks about every command, and a single conversion then costs three
"y" presses: one for the listing the model does to find the file, one for the
ffmpeg run, one for the check afterwards. Three presses for one click is how
people learn to hold down `y` on everything, which is worse than not asking.

So the question is not "ask or not" but "ask about what". Every command is
graded before it runs:

- READ_ONLY   cannot change anything (`ls`, `file`, `ffprobe`, `grep`). It runs
              without asking: there is nothing to approve.
- CONTAINED   writes a file inside the folder the chat is rooted in
              (`ffmpeg ... out.webm`, `mkdir`, `tar -xf`). It asks, and
              "always" remembers that kind for the rest of the chat.
- GUARDED     anything that can lose data or reach outside the folder: `rm`,
              `dd`, `mv` onto an existing file, a path outside the working
              directory, a shell or interpreter, a download, `git`, a package
              manager. It asks every time, and "always" is not offered.
- BLOCKED     the existing blocklist.

Approval fatigue is a security problem, so the cheap answer stays available
where it is cheap: you can stop reading prompts for conversions in a folder
without ever auto-approving a deletion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from indir.agent.tools import is_command_blocked
from indir.agent.verify import analyze_command, segments


class Risk(str, Enum):
    READ_ONLY = "read_only"
    CONTAINED = "contained"
    GUARDED = "guarded"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Decision:
    risk: Risk
    kind: str = ""
    reason: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def runs_without_asking(self) -> bool:
        # A blocked command is refused by the runner anyway, so there is
        # nothing to approve: letting it through tells the model at once.
        return self.risk in (Risk.READ_ONLY, Risk.BLOCKED)

    @property
    def can_remember(self) -> bool:
        """Only a contained write may be granted for the rest of the chat."""
        return self.risk is Risk.CONTAINED and bool(self.kind)

    @property
    def needs_approval(self) -> bool:
        return self.risk is not Risk.READ_ONLY

    def note(self) -> str:
        """One line for a transcript: why it ran, or why it had to ask."""
        if self.risk is Risk.READ_ONLY:
            return f"read-only — {self.reason}"
        if self.warnings:
            return f"{self.reason} ({'; '.join(self.warnings)})"
        return self.reason


# Programs that only read. Kept generous on purpose: a prompt for `ffprobe` is
# exactly the noise this module exists to remove.
_READ_ONLY = {
    "ls", "cat", "head", "tail", "stat", "file", "wc", "du", "df", "pwd",
    "echo", "printf", "true", "false", "which", "type", "whereis", "tree",
    "grep", "egrep", "rg", "awk", "cut", "sort", "uniq", "tr", "nl", "od",
    "xxd", "hexdump", "strings", "md5sum", "sha1sum", "sha256sum", "b2sum",
    "ffprobe", "exiftool", "mediainfo", "identify", "pdfinfo", "zcat",
    "diff", "cmp", "basename", "dirname", "readlink", "realpath", "seq",
    "hostname", "uname", "id", "whoami", "date", "uptime", "free", "ps",
    "nproc", "lscpu", "lsblk", "mount", "test", "[", "sleep", "cd",
}

# Programs that run arbitrary code or reach the network. None of these is a
# routine file operation, so they always ask.
_GUARDED = {
    "bash", "sh", "zsh", "fish", "dash", "ksh", "python", "python3", "perl",
    "ruby", "node", "deno", "bun", "php", "lua", "eval", "xargs", "exec",
    "sudo", "su", "doas", "pkexec", "systemctl", "service", "kill", "pkill",
    "curl", "wget", "git", "ssh", "scp", "sftp", "rsync", "nc", "telnet",
    "pamac", "yay", "paru", "pacman", "apt", "apt-get", "dnf", "zypper",
    "pip", "pip3", "pipx", "npm", "pnpm", "yarn", "cargo", "make", "cmake",
    "docker", "podman", "flatpak", "snap",
    "rm", "rmdir", "unlink", "shred", "truncate", "dd", "mkfs", "fdisk",
    "parted", "chmod", "chown", "chgrp", "setfacl", "chattr", "wipefs",
    "crontab", "reboot", "shutdown", "poweroff", "init", "iptables", "nft",
}

# Programs that read in place and only write when a flag says so.
_FLAG_GUARDED = {
    "find": (("-delete", "-exec", "-execdir", "-ok", "-fprint", "-fls"),
             "deletes or runs commands"),
    "sed": (("-i", "--in-place"), "edits files in place"),
    "awk": (("-i", "-o"), "may write files"),
    "gzip": (("-d",), "removes the source file"),
    "bzip2": (("-d",), "removes the source file"),
    "xz": (("-d",), "removes the source file"),
}

# Replacing an existing file with one of these destroys it outright.
_REPLACES = {"mv", "cp", "ln", "install", "tee", "touch", "truncate"}

_SUBSTITUTION_RE = re.compile(r"\$\(|`|\|\s*(?:sh|bash|zsh)\b")


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _resolve(directory: Path, raw: str) -> Path:
    candidate = Path(raw).expanduser()
    return candidate if candidate.is_absolute() else directory / candidate


def _grade(program: str, args: list[str]) -> Risk | None:
    if program in _GUARDED:
        return Risk.GUARDED
    if program in _FLAG_GUARDED:
        flags, _ = _FLAG_GUARDED[program]
        return Risk.GUARDED if any(flag in args for flag in flags) else Risk.READ_ONLY
    if program in _READ_ONLY:
        return Risk.READ_ONLY
    return None


def _scope(directory: Path, writes: list[str]) -> str:
    """The deepest folder a grant covers, from the outputs of this command."""
    scopes: list[str] = []
    for raw in writes:
        target = _resolve(directory, raw)
        parent = target if target.is_dir() else target.parent
        try:
            scopes.append(str(parent.resolve().relative_to(directory.resolve())) or ".")
        except (ValueError, OSError):
            scopes.append(".")
    return scopes[0] if len(set(scopes)) == 1 else "."


@dataclass
class ApprovalPolicy:
    """Grades commands and remembers what the user said "always" to."""

    blocklist: list[str] = field(default_factory=list)
    grants: set[str] = field(default_factory=set)

    def grant(self, kind: str) -> None:
        if kind:
            self.grants.add(kind)

    def granted(self, kind: str) -> bool:
        return bool(kind) and kind in self.grants

    def decide(self, command: str, directory: Path) -> Decision:
        blocked = is_command_blocked(command, self.blocklist)
        if blocked:
            return Decision(
                risk=Risk.BLOCKED, reason=f"matches the blocked pattern {blocked!r}"
            )

        # A substitution or a pipe into a shell can run anything, whatever the
        # visible programs are.
        if _SUBSTITUTION_RE.search(command):
            return Decision(
                risk=Risk.GUARDED,
                reason="runs a nested shell command that cannot be reviewed",
            )

        for program, args in segments(command):
            risk = _grade(program, args)
            if risk is Risk.GUARDED:
                return Decision(
                    risk=Risk.GUARDED,
                    reason=_guard_reason(program),
                )

        effect = analyze_command(command)
        if not effect.mutating and not effect.redirects:
            names = ", ".join(effect.programs) or "this command"
            return Decision(risk=Risk.READ_ONLY, reason=f"{names} only reads")

        writes = [*effect.outputs, *effect.redirects]
        outside = [raw for raw in writes if not _within(_resolve(directory, raw), directory)]
        if outside:
            return Decision(
                risk=Risk.GUARDED,
                reason=f"writes outside the folder this chat is scoped to: {outside[0]}",
            )
        removed = [
            raw for raw in effect.removals if not _within(_resolve(directory, raw), directory)
        ]
        if removed:
            return Decision(
                risk=Risk.GUARDED,
                reason=f"deletes outside the folder this chat is scoped to: {removed[0]}",
            )

        # The program that does the writing, not the last one in the pipeline
        # (`... mv ... && ls` is a rename, not an ls).
        graded = segments(command)
        program = next(
            (name for name, args in graded if _grade(name, args) is None),
            effect.programs[0] if effect.programs else "command",
        )
        warnings: list[str] = []
        for raw in writes:
            target = _resolve(directory, raw)
            try:
                exists = target.exists()
            except OSError:
                exists = False
            if not exists:
                continue
            if program in _REPLACES:
                return Decision(
                    risk=Risk.GUARDED,
                    reason=f"would replace the existing {target.name}",
                )
            warnings.append(f"overwrites the existing {target.name}")

        return Decision(
            risk=Risk.CONTAINED,
            kind=f"{program}:{_scope(directory, writes)}",
            reason=f"{program} writes inside this folder",
            warnings=tuple(warnings),
        )


def _guard_reason(program: str) -> str:
    if program in {"rm", "rmdir", "unlink", "shred", "truncate", "dd", "wipefs"}:
        return f"`{program}` can delete data"
    if program in {"chmod", "chown", "chgrp", "setfacl", "chattr"}:
        return f"`{program}` changes file permissions"
    if program in {"curl", "wget", "git", "ssh", "scp", "rsync", "nc", "telnet"}:
        return f"`{program}` reaches the network"
    if program in {"pacman", "yay", "paru", "apt", "apt-get", "dnf", "flatpak", "snap",
                   "pip", "pip3", "npm", "cargo", "make", "docker"}:
        return f"`{program}` installs or builds software"
    if program in {"bash", "sh", "zsh", "fish", "python", "python3", "perl", "ruby",
                   "node", "php", "lua", "eval", "exec", "xargs"}:
        return f"`{program}` runs code that cannot be reviewed in advance"
    if program in _FLAG_GUARDED:
        return f"`{program}` {_FLAG_GUARDED[program][1]}"
    return f"`{program}` is not a routine file operation"
