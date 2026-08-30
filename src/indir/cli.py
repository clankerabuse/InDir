from __future__ import annotations

import argparse
import sys
from pathlib import Path

from indir.config import load_config, ensure_config_permissions
from indir.context import normalize_path


def _run_doctor() -> int:
    import importlib.util

    script = Path(__file__).resolve().parents[2] / "scripts" / "doctor.py"
    if not script.exists():
        print("doctor script not found", file=sys.stderr)
        return 1
    spec = importlib.util.spec_from_file_location("indir_doctor", script)
    if spec is None or spec.loader is None:
        return 1
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.main())


def main(argv: list[str] | None = None) -> int:
    if argv and argv[0] == "doctor":
        return _run_doctor()
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        return _run_doctor()

    parser = argparse.ArgumentParser(
        description="InDir — Dolphin right-click AI assistant"
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Directory path or file:// URI from Dolphin",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Use terminal UI instead of Qt window",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.toml (default: ~/.config/indir/config.toml)",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Check installation and configuration",
    )
    args = parser.parse_args(argv)

    if args.doctor:
        return _run_doctor()

    if not args.path:
        parser.error("the following arguments are required: path")

    ensure_config_permissions(args.config)
    directory = normalize_path(args.path)
    if not directory.is_dir():
        print(f"Error: {directory} is not a directory", file=sys.stderr)
        return 1

    config = load_config(args.config)
    use_tui = args.tui or config.ui.mode == "tui"

    if use_tui:
        from indir.ui.tui import run_tui

        return run_tui(directory, config)

    from indir.ui.chat_window import run_qt_ui

    return run_qt_ui(directory, config)


if __name__ == "__main__":
    sys.exit(main())
