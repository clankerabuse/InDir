"""InDir integration for Nautilus (GNOME Files).

Installed by ``scripts/nautilus_ext.py`` into
``~/.local/share/nautilus-python/extensions/`` and loaded by nautilus-python.
The pure decision logic lives in ``indir_nautilus_support.py``, which the
installer places one directory up so Nautilus does not try to load it as an
extension in its own right.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from gi import require_version

require_version("Nautilus", "4.1")

from gi.repository import Gio, GObject, Nautilus  # noqa: E402

ICON_NAME = "indir"
SUPPORT_NAME = "indir_nautilus_support.py"


def _load_support():
    """Import the support module from the installed or the checkout layout."""
    here = Path(os.path.abspath(__file__))
    candidates = (
        here.parent.parent / SUPPORT_NAME,  # installed: ~/.local/share/nautilus-python/
        here.parent.parent / "src" / "indir" / "nautilus_support.py",  # repo checkout
    )
    for candidate in candidates:
        if not candidate.is_file():
            continue
        spec = importlib.util.spec_from_file_location("indir_nautilus_support", candidate)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("indir_nautilus_support", module)
        spec.loader.exec_module(module)
        return module
    raise ImportError(f"Indir: {SUPPORT_NAME} not found next to {here.parent}")


support = _load_support()


class InDirExtension(GObject.GObject, Nautilus.MenuProvider):
    """Adds "InDir" to the right-click menu for a single file or folder."""

    def _make_item(self, path: str):
        cli = support.resolve_cli_path()
        if cli is None:
            return None
        item = Nautilus.MenuItem(
            name=support.MENU_ITEM_NAME,
            label=support.MENU_LABEL,
            tip=support.MENU_TIP,
            icon=ICON_NAME,
        )
        item.connect("activate", self._on_activate, cli, path)
        return item

    def _on_activate(self, _menu, cli: str, path: str) -> None:
        try:
            Gio.Subprocess.new(
                support.build_command(cli, path), Gio.SubprocessFlags.NONE
            )
        except Exception as exc:  # surfaced in the nautilus log, not to the user
            print(f"InDir: failed to launch {cli}: {exc}", file=sys.stderr)

    def get_file_items(self, *args):
        # Nautilus 4.x passes only the selection in some releases and
        # (window, selection) in others; the bundled examples do the same.
        files = args[0] if len(args) == 1 else args[1]
        path = support.selected_path(files)
        if path is None:
            return []
        item = self._make_item(path)
        return [item] if item is not None else []

    def get_background_items(self, current_folder):
        """Right-clicking empty space offers InDir for the open folder."""
        location = current_folder.get_location() if current_folder else None
        path = location.get_path() if location else None
        if not path:
            return []
        item = self._make_item(path)
        return [item] if item is not None else []
