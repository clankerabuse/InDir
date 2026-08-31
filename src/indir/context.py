from __future__ import annotations

from pathlib import Path


def normalize_path(raw: str) -> Path:
    """Convert a file-manager URI or path string to an absolute directory Path."""
    if raw.startswith("file://"):
        from urllib.parse import unquote, urlparse

        parsed = urlparse(raw)
        path = unquote(parsed.path)
        return Path(path).resolve()
    return Path(raw).expanduser().resolve()


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


def build_suggested_commands(directory: Path) -> str:
    """Copy-paste commands for unambiguous single-file cases."""
    if not directory.is_dir():
        return ""

    by_ext: dict[str, list[str]] = {}
    try:
        entries = list(directory.iterdir())
    except OSError:
        return ""

    for entry in entries:
        if entry.is_file():
            ext = entry.suffix.lower()
            by_ext.setdefault(ext, []).append(entry.name)

    lines: list[str] = []
    if by_ext.get(".mp4") and len(by_ext[".mp4"]) == 1:
        name = by_ext[".mp4"][0]
        out = str(Path(name).with_suffix(".webm"))
        lines.append(
            'Convert mp4 → webm (copy this command exactly; do not change the filenames):'
        )
        lines.append(
            f'command -v ffmpeg && ffmpeg -y -i "{name}" -c:v libvpx-vp9 -crf 32 -b:v 0 '
            f'-c:a libopus "{out}"'
        )
    if by_ext.get(".webm") and len(by_ext[".webm"]) == 1:
        name = by_ext[".webm"][0]
        out = str(Path(name).with_suffix(".mp4"))
        lines.append(
            'Convert webm → mp4 (copy this command exactly; do not change the filenames):'
        )
        lines.append(
            f'command -v ffmpeg && ffmpeg -y -i "{name}" -c:v libx264 -crf 23 -c:a aac "{out}"'
        )

    if not lines:
        return ""
    return "## Suggested commands for this directory\n" + "\n".join(lines)


def get_suggested_command_lines(directory: Path) -> list[str]:
    """Return executable command strings for unambiguous single-file cases."""
    if not directory.is_dir():
        return []

    by_ext: dict[str, list[str]] = {}
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []

    for entry in entries:
        if entry.is_file():
            ext = entry.suffix.lower()
            by_ext.setdefault(ext, []).append(entry.name)

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


def resolve_run_command(directory: Path, command: str) -> str:
    """Prefer exact suggested commands when the model mangles filenames."""
    suggestions = get_suggested_command_lines(directory)
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


def build_system_prompt(directory: Path) -> str:
    listing = build_directory_listing(directory)
    summary = build_directory_summary(directory)
    suggested = build_suggested_commands(directory)
    suggested_block = f"\n{suggested}\n" if suggested else ""
    return f"""You are InDir, an AI assistant scoped to one directory on the user's machine.

Working directory: {directory}

File summary:
{summary}

Current directory contents:
{listing}
{suggested_block}
## Conditioning (follow every turn)
- I use this directory as the primary reference for the chat. I inspect its files to answer questions and complete tasks.
- I use CLI tools on the system via run_command. If a tool is missing, I tell the user how to install it (Arch Linux: pacman -S <package>) and stop.
- I stay scoped to this directory unless the user explicitly asks otherwise.

## Required behavior
1. Always write 1-3 sentences of explanation in your message text before calling any tool. Never respond with an empty message and only a tool call.
2. Resolve "this file", "this mp4", "it", etc. using the file summary and listing above:
   - If the summary shows exactly one matching file (e.g. "1 .mp4 file: foo.mp4"), use that file immediately. Do not ask the user to confirm or choose.
   - If several files match, list the names and ask which one.
   - If none match, use list_directory with a glob (e.g. "*.mp4") before acting.
3. For action requests ("convert", "rename", "compress", etc.): after your explanation, you MUST call run_command in the same turn. Never ask "should I proceed?", "do you want to convert?", "say yes to confirm", or similar — the UI has an approve/deny button for commands.
4. Do not call list_directory if the file summary already identifies the target file.
5. Quote every filename in shell commands with double quotes. Use the exact filename from the listing. If a suggested command is provided above, copy it verbatim into run_command.
6. Output filenames keep the same basename as the input; only change the extension (foo.mp4 → foo.webm).
7. Prefer one run_command that checks the tool then runs the action:
   command -v ffmpeg && ffmpeg -y -i "input.mp4" -c:v libvpx-vp9 -crf 32 -b:v 0 -c:a libopus "input.webm"
8. Keep responses concise and actionable. When proposing a command, say what it does and which file(s) it affects.

## Action request workflow (convert / rename / batch)
Example user message: "convert this mp4 to webm"
Correct response pattern:
  Text: "Converting foo.mp4 to foo.webm with ffmpeg."
  Tool: run_command with the webm recipe using foo.mp4
Wrong: asking the user to confirm in chat, or listing the directory when the file summary already shows one .mp4 file.

## Task recipes
Video convert to webm:
  command -v ffmpeg && ffmpeg -y -i "SOURCE.mp4" -c:v libvpx-vp9 -crf 32 -b:v 0 -c:a libopus "SOURCE.webm"
(change extension on output; keep the same basename)

Video convert to mp4:
  command -v ffmpeg && ffmpeg -y -i "SOURCE.webm" -c:v libx264 -crf 23 -c:a aac "SOURCE.mp4"

Batch rename (example .jpeg → .jpg):
  for f in *.jpeg; do mv -- "$f" "${{f%.jpeg}}.jpg"; done

## Safety
- All shell commands run with cwd set to the working directory above.
- Prefer safe, non-destructive commands. Ask before deleting or overwriting files unless the user explicitly requests it."""
