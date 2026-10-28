"""Helpers for the Nautilus (GNOME Files) extension.

Deliberately free of ``gi`` / GObject imports: the test suite exercises this
module directly, and ``scripts/nautilus_ext.py`` installs it verbatim next to
the extension. Anything that touches the Nautilus API lives in
``nautilus/indir.py``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

MENU_ITEM_NAME = "InDirNautilus::indir"
MENU_LABEL = "InDir"
MENU_TIP = "Ask the AI about this file or folder"

#: Filename the installer gives this module inside ~/.local/share/nautilus-python/
SUPPORT_NAME = "indir_nautilus_support.py"


def _repo_root() -> Path:
    """Project checkout root, derived from this file (src/indir/nautilus_support.py)."""
    return Path(__file__).resolve().parents[2]


def _is_executable(path: str | Path) -> bool:
    candidate = Path(path)
    return candidate.is_file() and os.access(candidate, os.X_OK)


def resolve_cli_path(
    *,
    which=None,
    home: Path | str | None = None,
    repo_root: Path | str | None = None,
) -> str | None:
    """Locate the ``indir`` binary for the extension subprocess.

    Nautilus runs extensions inside its own embedded interpreter, which has no
    access to the project virtualenv, so the CLI is resolved by path. Order:
    ``$PATH``, then ``~/.local/bin/indir``, then ``<repo>/.venv/bin/indir``.
    """
    lookup = which or shutil.which
    home_dir = Path(home) if home is not None else Path.home()
    root = Path(repo_root) if repo_root is not None else _repo_root()

    candidates: list[str] = []
    found = lookup("indir")
    if found:
        candidates.append(str(found))
    candidates.append(str(home_dir / ".local" / "bin" / "indir"))
    candidates.append(str(root / ".venv" / "bin" / "indir"))

    for candidate in candidates:
        if _is_executable(candidate):
            return candidate
    return None


def selected_path(files) -> str | None:
    """Return the selected path, or ``None`` when InDir should stay hidden.

    InDir takes exactly one file or folder per launch, so multi-selections and
    non-local (remote/trash) locations get no menu item.
    """
    entries = list(files or [])
    if len(entries) != 1:
        return None

    entry = entries[0]
    get_location = getattr(entry, "get_location", None)
    if get_location is None:
        return None
    location = get_location()
    if location is None:
        return None

    get_path = getattr(location, "get_path", None)
    if get_path is None:
        return None
    path = get_path()
    return path or None


def build_command(cli_path: str | Path, path: str | Path) -> list[str]:
    """argv for launching InDir on a single file or folder."""
    return [str(cli_path), str(path)]
