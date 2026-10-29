"""InDir — Nautilus (GNOME Files) right-click integration.

This file is a template: scripts/nautilus_extension.py replaces
__INDIR_CLI__ and __INDIR_ICON__ during ./install and copies the result to
~/.local/share/nautilus-python/extensions/indir.py. Edit the template in the
project and re-run the installer rather than patching the installed copy.

Needs nautilus-python (Arch: pacman -S nautilus-python). Nautilus loads this
module inside its own process, so it must stay dependency-free apart from
PyGObject and the launch of the indir CLI.
"""

from __future__ import annotations

import os
import shutil
import sys

from gi import require_version

# Nautilus up to ~49 ships the 4.0 extension API, 51 ships 4.1. Ask for the
# newest this machine has: a failed require_version raises, and Nautilus then
# drops the extension without any user-visible error.
for _nautilus_api in ("4.1", "4.0"):
    try:
        require_version("Nautilus", _nautilus_api)
        break
    except ValueError:
        continue

from gi.repository import GObject, Gio, Nautilus

CLI_PATH = "__INDIR_CLI__"
ICON = "__INDIR_ICON__"
MENU_ITEM_ID = "InDirNautilus::indir"
MENU_LABEL = "InDir"
MENU_TIP = "AI assistant scoped to this folder or file"


def indir_command():
    """The CLI the installer recorded, or an absolute 'indir' found on PATH.

    A relative PATH hit is refused: it would run whatever ./indir happens to be
    in Nautilus's working directory.
    """
    if CLI_PATH and os.access(CLI_PATH, os.X_OK):
        return CLI_PATH
    found = shutil.which("indir")
    if found and os.path.isabs(found) and os.access(found, os.X_OK):
        return found
    return None


def launch(target):
    """Start the InDir window for one path. Returns False when nothing ran."""
    command = indir_command()
    if not command:
        return False
    try:
        Gio.Subprocess.new([command, str(target)], Gio.SubprocessFlags.NONE)
    except Exception as exc:  # GLib.Error, e.g. a deleted CLI or broken venv
        print(f"InDir: could not launch {command}: {exc}", file=sys.stderr)
        return False
    return True


def file_info_path(file_info):
    """Local path for a Nautilus.FileInfo, or None for remote/virtual items."""
    if file_info is None:
        return None
    location = file_info.get_location()
    if location is None:
        return None
    return location.get_path()


class InDirMenuProvider(GObject.GObject, Nautilus.MenuProvider):
    def _make_item(self, target):
        item = Nautilus.MenuItem(
            name=MENU_ITEM_ID,
            label=MENU_LABEL,
            tip=MENU_TIP,
            icon=ICON,
        )
        item.connect("activate", self._on_activate, target)
        return item

    def _on_activate(self, _item, target):
        launch(target)

    def get_file_items(self, *args):
        # Nautilus 43+ passes (files); older versions passed (window, files).
        files = args[-1] if args else None
        if not files or len(files) != 1:
            return []
        # The CLI takes exactly one path, so only offer InDir for one selection.
        path = file_info_path(files[0])
        if not path or not indir_command():
            return []
        return [self._make_item(path)]

    def get_background_items(self, *args):
        folder = args[-1] if args else None
        path = file_info_path(folder)
        if not path or not indir_command():
            return []
        return [self._make_item(path)]
