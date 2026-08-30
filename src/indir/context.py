from __future__ import annotations

from pathlib import Path


def normalize_path(raw: str) -> Path:
    """Convert a Dolphin URI or path string to an absolute directory Path."""
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


def build_system_prompt(directory: Path) -> str:
    listing = build_directory_listing(directory)
    return f"""You are a helpful AI assistant scoped to a single directory on the user's machine.

Working directory: {directory}

Current directory contents:
{listing}

Rules:
- All shell commands run with cwd set to the working directory above.
- Prefer safe, non-destructive commands. Ask before deleting or overwriting files unless the user explicitly requests it.
- Use list_directory to inspect files when needed.
- Use run_command to propose shell commands for tasks like format conversion, renaming, or batch operations.
- Keep responses concise and actionable.
- When proposing commands, explain briefly what they do."""
