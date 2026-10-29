from __future__ import annotations

import argparse
import sys
from pathlib import Path

from indir.config import load_config, ensure_config_permissions
from indir.context import resolve_launch_target


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
    # Always work from one argument list: mixing argv with sys.argv let a host
    # process steer main([...]) into the doctor path.
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list == ["doctor"]:
        return _run_doctor()

    parser = argparse.ArgumentParser(
        prog="indir",
        usage="indir PATH [--tui] [--config PATH]\n       indir --doctor",
        description="InDir — Dolphin/Thunar/Nautilus right-click AI assistant",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="File or directory path, or a file:// URI from the file manager",
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
    args = parser.parse_args(args_list)

    if args.doctor:
        if args.paths:
            parser.error("--doctor does not take a path")
        return _run_doctor()

    if not args.paths:
        parser.error("a file or directory path is required")
    if len(args.paths) != 1:
        print("Select a single file or folder.", file=sys.stderr)
        return 1

    ensure_config_permissions(args.config)
    try:
        target = resolve_launch_target(args.paths[0])
    except FileNotFoundError as exc:
        print(f"Error: {exc} does not exist", file=sys.stderr)
        return 1

    config = load_config(args.config)
    use_tui = args.tui or config.ui.mode == "tui"

    if use_tui:
        from indir.ui.tui import run_tui

        return run_tui(target.directory, config, focus_file=target.focus_file)

    from indir.ui.chat_window import run_qt_ui

    return run_qt_ui(target.directory, config, focus_file=target.focus_file)


if __name__ == "__main__":
    sys.exit(main())
