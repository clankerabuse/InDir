#!/usr/bin/env python3
"""Install or remove the InDir Nautilus (GNOME Files) extension."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

EXTENSION_NAME = "indir.py"
DEFAULT_EXTENSION_DIR = (
    Path.home() / ".local" / "share" / "nautilus-python" / "extensions"
)
PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = PROJECT_DIR / "nautilus" / "indir_nautilus.py"
# Ships in the nautilus-python package; without it Nautilus skips the extension.
PYTHON_BINDINGS = Path("/usr/lib/nautilus/extensions-4/libnautilus-python.so")

CLI_PLACEHOLDER = "__INDIR_CLI__"
ICON_PLACEHOLDER = "__INDIR_ICON__"


def render_extension(
    cli_path: str | Path,
    icon: str = "indir",
    template: str | Path = DEFAULT_TEMPLATE,
) -> str:
    """Fill the template placeholders. json.dumps keeps the literal valid Python."""
    source = Path(template).read_text(encoding="utf-8")
    source = source.replace(f'"{CLI_PLACEHOLDER}"', json.dumps(str(cli_path)))
    source = source.replace(f'"{ICON_PLACEHOLDER}"', json.dumps(str(icon)))
    # A broken template must fail here, not silently inside Nautilus.
    compile(source, str(template), "exec")
    return source


def extension_path(ext_dir: str | Path = DEFAULT_EXTENSION_DIR) -> Path:
    return Path(ext_dir) / EXTENSION_NAME


def extension_installed(ext_dir: str | Path = DEFAULT_EXTENSION_DIR) -> bool:
    return extension_path(ext_dir).is_file()


def install_extension(
    cli_path: str | Path,
    *,
    icon: str = "indir",
    ext_dir: str | Path = DEFAULT_EXTENSION_DIR,
    template: str | Path = DEFAULT_TEMPLATE,
) -> Path:
    dest = extension_path(ext_dir)
    source = render_extension(cli_path, icon, template)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Write a fresh file and rename it into place. Path.write_text() would follow
    # a symlink planted at dest and clobber whatever it points at.
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=dest.parent,
        prefix=".indir-",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle:
            handle.write(source)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path = Path(handle.name)
        tmp_path.chmod(0o644)
        os.replace(tmp_path, dest)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
    return dest


def remove_extension(ext_dir: str | Path = DEFAULT_EXTENSION_DIR) -> bool:
    dest = extension_path(ext_dir)
    if not dest.is_file():
        return False
    dest.unlink()
    cache = dest.parent / "__pycache__"
    if cache.is_dir():
        for stale in cache.glob(f"{dest.stem}.*.pyc"):
            stale.unlink(missing_ok=True)
    return True


def python_bindings_available() -> bool:
    return PYTHON_BINDINGS.is_file()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the InDir Nautilus extension")
    parser.add_argument("action", choices=("install", "remove", "check"))
    parser.add_argument("--cli", help="Full path to the indir binary (required for install)")
    parser.add_argument(
        "--icon",
        default="indir",
        help="Icon name Nautilus should draw next to the menu item (default: indir)",
    )
    parser.add_argument("--dir", type=Path, default=DEFAULT_EXTENSION_DIR, help="Extension directory")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    args = parser.parse_args(argv)

    if args.action == "install":
        if not args.cli:
            print("--cli is required for install", file=sys.stderr)
            return 2
        if not python_bindings_available():
            print(
                f"Note: {PYTHON_BINDINGS} is missing — install nautilus-python "
                "(sudo pacman -S nautilus-python) or Nautilus will ignore the extension",
                file=sys.stderr,
            )
        dest = install_extension(args.cli, icon=args.icon, ext_dir=args.dir, template=args.template)
        print(f"Installed Nautilus extension in {dest}")
        return 0
    if args.action == "remove":
        if remove_extension(args.dir):
            print(f"Removed Nautilus extension from {extension_path(args.dir)}")
        else:
            print("Nautilus extension not present")
        return 0
    installed = extension_installed(args.dir)
    print("installed" if installed else "missing")
    return 0 if installed else 1


if __name__ == "__main__":
    sys.exit(main())
