from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def normalize_path(raw: str) -> Path:
    """Convert a file-manager URI or path string to an absolute Path."""
    if raw.startswith("file://"):
        from urllib.parse import unquote, urlparse

        parsed = urlparse(raw)
        path = unquote(parsed.path)
        return Path(path).resolve()
    return Path(raw).expanduser().resolve()


@dataclass(frozen=True)
class LaunchTarget:
    """Where a chat is rooted. `focus_file` is set when the user opened one file."""

    directory: Path
    focus_file: Path | None = None


def resolve_launch_target(raw: str) -> LaunchTarget:
    """Open a folder as-is, or a file focused inside its parent folder."""
    path = normalize_path(raw)
    if path.is_dir():
        return LaunchTarget(directory=path)
    if path.is_file():
        return LaunchTarget(directory=path.parent, focus_file=path)
    raise FileNotFoundError(path)


def format_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def build_directory_listing(directory: Path, max_entries: int = 100) -> str:
    if not directory.is_dir():
        return f"(not a directory: {directory})"

    lines: list[str] = []
    try:
        entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        return f"(cannot list directory: {exc})"

    for entry in entries[:max_entries]:
        try:
            if entry.is_dir():
                lines.append(f"  [dir]  {entry.name}/")
            else:
                size = entry.stat().st_size
                lines.append(f"  [file] {entry.name} ({format_size(size)})")
        except OSError:
            lines.append(f"  [?]    {entry.name}")

    if len(entries) > max_entries:
        lines.append(f"  ... and {len(entries) - max_entries} more entries")

    return "\n".join(lines) if lines else "  (empty directory)"


def build_directory_summary(directory: Path) -> str:
    """Short counts by extension to help resolve 'this mp4' etc."""
    if not directory.is_dir():
        return ""

    by_ext: dict[str, list[str]] = {}
    try:
        entries = list(directory.iterdir())
    except OSError:
        return ""

    for entry in entries:
        if not entry.is_file():
            continue
        ext = entry.suffix.lower() or "(no extension)"
        by_ext.setdefault(ext, []).append(entry.name)

    if not by_ext:
        return "No files in this directory."

    parts: list[str] = []
    for ext in sorted(by_ext):
        names = by_ext[ext]
        if len(names) == 1:
            parts.append(f"- 1 {ext} file: {names[0]}")
        else:
            parts.append(f"- {len(names)} {ext} files")
    return "\n".join(parts)


def _files_by_extension(
    directory: Path, focus_file: Path | None = None
) -> dict[str, list[str]]:
    if focus_file is not None:
        return {focus_file.suffix.lower(): [focus_file.name]}

    by_ext: dict[str, list[str]] = {}
    try:
        entries = list(directory.iterdir())
    except OSError:
        return {}

    for entry in entries:
        if entry.is_file():
            ext = entry.suffix.lower()
            by_ext.setdefault(ext, []).append(entry.name)
    return by_ext


def build_suggested_commands(directory: Path, focus_file: Path | None = None) -> str:
    """Copy-paste commands for unambiguous single-file cases."""
    if focus_file is None and not directory.is_dir():
        return ""

    by_ext = _files_by_extension(directory, focus_file)
    if not by_ext:
        return ""

    lines: list[str] = []
    if by_ext.get(".mp4") and len(by_ext[".mp4"]) == 1:
        name = by_ext[".mp4"][0]
        out = str(Path(name).with_suffix(".webm"))
        lines.append(
            "Convert mp4 → webm (copy this command exactly; do not change the filenames):"
        )
        lines.append(
            f'command -v ffmpeg && ffmpeg -y -i "{name}" -c:v libvpx-vp9 -crf 32 -b:v 0 '
            f'-c:a libopus "{out}"'
        )
    if by_ext.get(".webm") and len(by_ext[".webm"]) == 1:
        name = by_ext[".webm"][0]
        out = str(Path(name).with_suffix(".mp4"))
        lines.append(
            "Convert webm → mp4 (copy this command exactly; do not change the filenames):"
        )
        lines.append(
            f'command -v ffmpeg && ffmpeg -y -i "{name}" -c:v libx264 -crf 23 -c:a aac "{out}"'
        )

    if not lines:
        return ""
    heading = "this file" if focus_file is not None else "this directory"
    return f"## Suggested commands for {heading}\n" + "\n".join(lines)


def get_suggested_command_lines(
    directory: Path, focus_file: Path | None = None
) -> list[str]:
    """Return executable command strings for unambiguous single-file cases."""
    if focus_file is None and not directory.is_dir():
        return []

    by_ext = _files_by_extension(directory, focus_file)

    commands: list[str] = []
    if by_ext.get(".mp4") and len(by_ext[".mp4"]) == 1:
        name = by_ext[".mp4"][0]
        out = str(Path(name).with_suffix(".webm"))
        commands.append(
            f'command -v ffmpeg && ffmpeg -y -i "{name}" -c:v libvpx-vp9 -crf 32 -b:v 0 '
            f'-c:a libopus "{out}"'
        )
    if by_ext.get(".webm") and len(by_ext[".webm"]) == 1:
        name = by_ext[".webm"][0]
        out = str(Path(name).with_suffix(".mp4"))
        commands.append(
            f'command -v ffmpeg && ffmpeg -y -i "{name}" -c:v libx264 -crf 23 -c:a aac "{out}"'
        )
    return commands


def resolve_run_command(
    directory: Path, command: str, focus_file: Path | None = None
) -> str:
    """Prefer exact suggested commands when the model mangles filenames."""
    suggestions = get_suggested_command_lines(directory, focus_file)
    if not suggestions or "ffmpeg" not in command.lower():
        return command
    if command in suggestions:
        return command

    lower = command.lower()
    for suggested in suggestions:
        if "-c:a libopus" in suggested and "webm" in lower:
            return suggested
        if "-c:a aac" in suggested and ".mp4" in lower:
            return suggested
    return command


def _focused_file_prompt(directory: Path, focus_file: Path) -> str:
    try:
        size = format_size(focus_file.stat().st_size)
    except OSError:
        size = "unknown size"
    suggested = build_suggested_commands(directory, focus_file)
    suggested_block = f"\n{suggested}\n" if suggested else ""
    return f"""You are InDir, an AI assistant focused on a single file.

Focused file: {focus_file.name}
Full path: {focus_file}
Size: {size}
Working directory (cwd for commands): {directory}
{suggested_block}
## Scope
- This file is the only subject. Do not read, list, rename, convert, or modify any other file.
- Refer to it by its exact basename, in double quotes.
- If the user asks to include the folder, other files, or otherwise widen the scope, call widen_scope before that work. After it returns, the whole working directory is in scope.
- Stay inside the working directory unless they ask to go elsewhere.

## Behavior
1. For action requests on this file, briefly say what you'll do, then call run_command in the same turn. Never ask "should I proceed?" — the UI has approve/deny.
2. Do not call list_directory while focused on this file. Call widen_scope first if they want the rest of the folder.
3. If a suggested command is provided above, copy it verbatim into run_command.
4. Keep output basenames the same; only change the extension.
5. Prefer one command: command -v TOOL && TOOL ...
6. Keep replies short.

## Recipes
webm: command -v ffmpeg && ffmpeg -y -i "SOURCE.mp4" -c:v libvpx-vp9 -crf 32 -b:v 0 -c:a libopus "SOURCE.webm"
mp4: command -v ffmpeg && ffmpeg -y -i "SOURCE.webm" -c:v libx264 -crf 23 -c:a aac "SOURCE.mp4"
rename jpeg→jpg: for f in *.jpeg; do mv -- "$f" "${{f%.jpeg}}.jpg"; done

## Safety
- Commands run with cwd set to the working directory.
- Prefer non-destructive commands. Ask before deleting/overwriting unless the user asked for it."""


def build_system_prompt(directory: Path, focus_file: Path | None = None) -> str:
    if focus_file is not None:
        return _focused_file_prompt(directory, focus_file)
    # Keep the system prompt lean: summary + recipes only. Full listings come from
    # list_directory when the model actually needs them — less prefill every turn.
    summary = build_directory_summary(directory)
    suggested = build_suggested_commands(directory)
    suggested_block = f"\n{suggested}\n" if suggested else ""
    return f"""You are InDir, an AI assistant scoped to one directory on the user's machine.

Working directory: {directory}

File summary:
{summary}
{suggested_block}
## Scope
- This directory is the primary reference for the chat.
- Use run_command for CLI tools. If a tool is missing, tell the user how to install it (Arch: pacman -S <package>) and stop.
- Stay scoped here unless the user asks otherwise.

## Behavior
1. For action requests, briefly say what you'll do, then call run_command in the same turn. Never ask "should I proceed?" — the UI has approve/deny.
2. Resolve "this file" / "this mp4" from the file summary:
   - Exactly one match → use it immediately.
   - Several matches → list names and ask which.
   - None / need filenames → call list_directory (e.g. pattern "*.mp4"). Do not call it when the summary already identifies the file.
3. Quote filenames with double quotes. If a suggested command is provided above, copy it verbatim into run_command.
4. Keep output basenames the same; only change the extension (foo.mp4 → foo.webm).
5. Prefer one command: command -v TOOL && TOOL ...
6. Keep replies short.

## Recipes
webm: command -v ffmpeg && ffmpeg -y -i "SOURCE.mp4" -c:v libvpx-vp9 -crf 32 -b:v 0 -c:a libopus "SOURCE.webm"
mp4: command -v ffmpeg && ffmpeg -y -i "SOURCE.webm" -c:v libx264 -crf 23 -c:a aac "SOURCE.mp4"
rename jpeg→jpg: for f in *.jpeg; do mv -- "$f" "${{f%.jpeg}}.jpg"; done

## Safety
- Commands run with cwd set to the working directory.
- Prefer non-destructive commands. Ask before deleting/overwriting unless the user asked for it."""
