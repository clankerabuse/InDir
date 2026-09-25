#!/usr/bin/env python3
"""Install or remove the InDir Thunar custom action (uca.xml)."""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

UNIQUE_ID = "indir-thunar-1"
ACTION_NAME = "InDir"
DEFAULT_UCA = Path.home() / ".config" / "Thunar" / "uca.xml"


FILE_SCOPE_TAGS = (
    "directories",
    "audio-files",
    "image-files",
    "other-files",
    "text-files",
    "video-files",
)


def format_command(cli_path: str | Path) -> str:
    return f"{shlex.quote(str(cli_path))} %f"


def action_installed(uca_path: Path = DEFAULT_UCA) -> bool:
    if not uca_path.is_file():
        return False
    return UNIQUE_ID in uca_path.read_text(encoding="utf-8", errors="replace")


def _find_action(root: ET.Element) -> ET.Element | None:
    for action in root.findall("action"):
        if action.findtext("unique-id") == UNIQUE_ID:
            return action
    return None


def _write_tree(tree: ET.ElementTree, uca_path: Path) -> None:
    ET.indent(tree, space="\t")
    xml = ET.tostring(tree.getroot(), encoding="unicode")
    xml = re.sub(r"<([A-Za-z0-9_-]+) />", r"<\1/>", xml)
    uca_path.write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n" + xml + "\n",
        encoding="utf-8",
    )


def _set_child(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    el = parent.find(tag)
    if el is None:
        el = ET.SubElement(parent, tag)
    el.text = text
    return el


def upsert_action(
    uca_path: Path, cli_path: str | Path, *, icon: str = "indir"
) -> None:
    if uca_path.is_file() and uca_path.stat().st_size > 0:
        tree = ET.parse(uca_path)
        root = tree.getroot()
        if root.tag != "actions":
            raise ValueError(f"Unexpected Thunar uca.xml root: {root.tag}")
    else:
        root = ET.Element("actions")
        tree = ET.ElementTree(root)

    action = _find_action(root)
    if action is None:
        action = ET.SubElement(root, "action")

    _set_child(action, "icon", icon)
    _set_child(action, "name", ACTION_NAME)
    _set_child(action, "unique-id", UNIQUE_ID)
    _set_child(action, "command", format_command(cli_path))
    _set_child(action, "description", "Open InDir on this file or folder")
    _set_child(action, "patterns", "*")
    for tag in FILE_SCOPE_TAGS:
        if action.find(tag) is None:
            ET.SubElement(action, tag)

    uca_path.parent.mkdir(parents=True, exist_ok=True)
    _write_tree(tree, uca_path)


def refresh_action_for_files(uca_path: Path, icon: str) -> bool:
    """If InDir is already installed, also show it for files and use the logo."""
    if not uca_path.is_file():
        return False
    try:
        tree = ET.parse(uca_path)
    except ET.ParseError:
        return False
    action = _find_action(tree.getroot())
    if action is None:
        return False
    has_files = all(action.find(tag) is not None for tag in FILE_SCOPE_TAGS)
    current_icon = action.findtext("icon") or ""
    if has_files and current_icon == str(icon):
        return False
    command = action.findtext("command") or ""
    try:
        cli = shlex.split(command)[0]
    except ValueError:
        return False
    if not cli:
        return False
    upsert_action(uca_path, cli, icon=str(icon))
    return True


def remove_action(uca_path: Path = DEFAULT_UCA) -> bool:
    if not uca_path.is_file():
        return False
    tree = ET.parse(uca_path)
    root = tree.getroot()
    action = _find_action(root)
    if action is None:
        return False
    root.remove(action)
    _write_tree(tree, uca_path)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage InDir Thunar custom action")
    parser.add_argument("action", choices=("install", "remove", "check"))
    parser.add_argument("--cli", help="Full path to the indir binary (required for install)")
    parser.add_argument("--icon", default="indir", help="Icon name or absolute path")
    parser.add_argument("--uca", type=Path, default=DEFAULT_UCA)
    args = parser.parse_args(argv)

    if args.action == "install":
        if not args.cli:
            print("--cli is required for install", file=sys.stderr)
            return 2
        upsert_action(args.uca, args.cli, icon=args.icon)
        print(f"Installed Thunar action in {args.uca}")
        return 0
    if args.action == "remove":
        if remove_action(args.uca):
            print(f"Removed Thunar action from {args.uca}")
        else:
            print("Thunar action not present")
        return 0
    print("installed" if action_installed(args.uca) else "missing")
    return 0 if action_installed(args.uca) else 1


if __name__ == "__main__":
    sys.exit(main())
