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


def _is_launch_target(raw: str) -> bool:
    """True when the first word names something we should open, not ask about.

    A file manager passes an absolute path or a file:// URI and those must keep
    launching the chat window. Anything else is a request for the current
    directory, so `indir convert the mp4s to webm` does what it says.
    """
    if raw.startswith("file://"):
        return True
    return Path(raw).expanduser().exists()


def _looks_like_a_path(raw: str) -> bool:
    """A single word that is shaped like a path is a typo'd path, not a request.

    `indir ~/Downloads/nope` should say it does not exist; a request is
    several words long, or a bare word like `list`.
    """
    return (
        raw.startswith("file://")
        or raw.startswith(("~", "/", "./", "../"))
        or "/" in raw
    )


def main(argv: list[str] | None = None) -> int:
    # Always work from one argument list: mixing argv with sys.argv let a host
    # process steer main([...]) into the doctor path.
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list == ["doctor"]:
        return _run_doctor()

    parser = argparse.ArgumentParser(
        prog="indir",
        usage=(
            "indir REQUEST... [--yes]\n"
            "       indir PATH [--tui] [--config PATH]\n"
            "       indir --prompt REQUEST [PATH] [--yes]\n"
            "       indir --doctor"
        ),
        description="InDir — Dolphin/Thunar/Nautilus right-click AI assistant",
        epilog=(
            "Run a request in the current directory from the terminal:\n"
            "  indir convert the mp4s to webm\n"
            "  indir --yes list every video file\n"
            "Ask about another folder:\n"
            "  indir --prompt 'what is in here?' ~/Downloads\n"
            "Open the window instead:\n"
            "  indir .            indir ~/Downloads/clip.webm --tui\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH|REQUEST",
        help=(
            "A file or directory path, a file:// URI from the file manager, "
            "or — when it names nothing on disk — a request for the current "
            "directory"
        ),
    )
    parser.add_argument(
        "--prompt",
        "-p",
        metavar="REQUEST",
        default=None,
        help="Run REQUEST against PATH (or the current directory) without a UI",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Approve proposed commands without asking (blocklist still applies)",
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

    request: str | None = None
    base: str

    if args.prompt is not None:
        request = args.prompt.strip()
        if not request:
            parser.error("--prompt needs a request")
        if len(args.paths) > 1:
            print("Select a single folder to run the request in.", file=sys.stderr)
            return 1
        base = args.paths[0] if args.paths else "."
    elif args.paths and (
        _is_launch_target(args.paths[0])
        or (len(args.paths) == 1 and _looks_like_a_path(args.paths[0]))
    ):
        if len(args.paths) != 1:
            print("Select a single file or folder.", file=sys.stderr)
            return 1
        base = args.paths[0]
    elif args.paths:
        request = " ".join(args.paths).strip()
        base = "."
    else:
        parser.error("a request or a path is required (try: indir convert the mp4s to webm)")

    ensure_config_permissions(args.config)
    try:
        target = resolve_launch_target(base)
    except FileNotFoundError as exc:
        print(f"Error: {exc} does not exist", file=sys.stderr)
        return 1

    config = load_config(args.config)

    if request is not None:
        if args.tui:
            from indir.ui.tui import run_tui

            return run_tui(
                target.directory,
                config,
                focus_file=target.focus_file,
                initial_request=request,
            )
        from indir.ui.headless import run_request

        return run_request(
            target.directory,
            config,
            request,
            focus_file=target.focus_file,
            assume_yes=args.yes,
        )

    use_tui = args.tui or config.ui.mode == "tui"

    if use_tui:
        from indir.ui.tui import run_tui

        return run_tui(target.directory, config, focus_file=target.focus_file)

    from indir.ui.chat_window import run_qt_ui

    return run_qt_ui(target.directory, config, focus_file=target.focus_file)


if __name__ == "__main__":
    sys.exit(main())
