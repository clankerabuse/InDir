#!/usr/bin/env python3
"""Install or remove the InDir Nautilus (GNOME Files) extension."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTENSION_SOURCE = REPO_ROOT / "nautilus" / "indir.py"
SUPPORT_SOURCE = REPO_ROOT / "src" / "indir" / "nautilus_support.py"

NAUTILUS_PYTHON_DIR = Path.home() / ".local" / "share" / "nautilus-python"
DEFAULT_EXT_DIR = NAUTILUS_PYTHON_DIR / "extensions"

EXTENSION_NAME = "indir_nautilus.py"
SUPPORT_NAME = "indir_nautilus_support.py"

#: Marker that the nautilus-python bridge is actually installed; without it the
#: extension file sits in the directory unseen.
BRIDGE_LIBRARY = Path("/usr/lib/nautilus/extensions-4/libnautilus-python.so")


def extension_path(ext_dir: Path = DEFAULT_EXT_DIR) -> Path:
    return Path(ext_dir) / EXTENSION_NAME


def support_path(ext_dir: Path = DEFAULT_EXT_DIR) -> Path:
    return Path(ext_dir).parent / SUPPORT_NAME


def _place(src: Path, dest: Path, *, link: bool) -> None:
    if dest.is_symlink() or dest.exists():
        dest.unlink()
    if link:
        try:
            dest.symlink_to(Path(src).resolve())
            return
        except OSError:
            pass
    shutil.copy2(src, dest)


def install(
    ext_dir: Path = DEFAULT_EXT_DIR,
    *,
    link: bool = True,
    ext_source: Path = EXTENSION_SOURCE,
    support_source: Path = SUPPORT_SOURCE,
) -> list[Path]:
    """Install the extension and its support module; returns the written paths."""
    ext_dir = Path(ext_dir)
    for src in (ext_source, support_source):
        if not Path(src).is_file():
            raise FileNotFoundError(f"missing extension source: {src}")

    ext_dir.mkdir(parents=True, exist_ok=True)
    installed = [extension_path(ext_dir), support_path(ext_dir)]
    _place(Path(ext_source), installed[0], link=link)
    _place(Path(support_source), installed[1], link=link)
    return installed


def remove(ext_dir: Path = DEFAULT_EXT_DIR) -> list[Path]:
    """Remove the extension files; returns the paths that existed."""
    removed = []
    for target in (extension_path(ext_dir), support_path(ext_dir)):
        if target.is_symlink() or target.exists():
            target.unlink()
            removed.append(target)
    return removed


def status(ext_dir: Path = DEFAULT_EXT_DIR) -> str:
    has_ext = extension_path(ext_dir).is_file()
    has_support = support_path(ext_dir).is_file()
    if has_ext and has_support:
        return "installed"
    if has_ext or has_support:
        return "partial"
    return "missing"


def bridge_available() -> bool:
    return BRIDGE_LIBRARY.exists()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the InDir Nautilus extension")
    parser.add_argument("action", choices=("install", "remove", "check"))
    parser.add_argument("--cli", help="Full path to the indir binary (sanity check)")
    parser.add_argument(
        "--ext-dir",
        type=Path,
        default=DEFAULT_EXT_DIR,
        help=f"Extensions directory (default: {DEFAULT_EXT_DIR})",
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        help="Copy the extension instead of symlinking the checkout",
    )
    args = parser.parse_args(argv)

    if args.action == "install":
        try:
            written = install(args.ext_dir, link=not args.copy)
        except FileNotFoundError as exc:
            print(exc, file=sys.stderr)
            return 1
        for path in written:
            print(f"  → {path}")
        print(f"Installed Nautilus extension in {args.ext_dir}")
        if args.cli and not os.access(args.cli, os.X_OK):
            print(f"  ! {args.cli} is not executable — re-run ./install", file=sys.stderr)
        if not bridge_available():
            print(
                "  ! nautilus-python is not installed — Nautilus will not load the "
                "extension until you install it (sudo pacman -S nautilus-python)",
                file=sys.stderr,
            )
        return 0

    if args.action == "remove":
        removed = remove(args.ext_dir)
        if removed:
            for path in removed:
                print(f"  Removed {path}")
        else:
            print("Nautilus extension not present")
        return 0

    state = status(args.ext_dir)
    print(state)
    return 0 if state == "installed" else 1


if __name__ == "__main__":
    sys.exit(main())
