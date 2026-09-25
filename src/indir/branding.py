"""App icon and desktop entry so window managers don't use a generic icon."""

from __future__ import annotations

import subprocess
from pathlib import Path

APP_ID = "indir"
PNG_SIZES = (16, 24, 32, 48, 64, 128, 256)

_ASSETS = Path(__file__).resolve().parent / "ui" / "assets"
_BUNDLED_SVG = _ASSETS / "indir.svg"


def bundled_icon() -> Path:
    return _BUNDLED_SVG


def icon_svg_path(home: Path | None = None) -> Path:
    root = home if home is not None else Path.home()
    return root / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "indir.svg"


def desktop_entry_path(home: Path | None = None) -> Path:
    root = home if home is not None else Path.home()
    return root / ".local" / "share" / "applications" / "indir.desktop"


def dolphin_menu_path(home: Path | None = None) -> Path:
    root = home if home is not None else Path.home()
    return root / ".local" / "share" / "kio" / "servicemenus" / "indir.desktop"


def thunar_menu_path(home: Path | None = None) -> Path:
    root = home if home is not None else Path.home()
    return root / ".config" / "Thunar" / "uca.xml"


def desktop_entry_text(icon_path: Path) -> str:
    return f"""[Desktop Entry]
Type=Application
Name=InDir
GenericName=Folder assistant
Comment=AI assistant scoped to a folder or a single file
Exec=indir
TryExec=indir
Icon={icon_path}
StartupWMClass={APP_ID}
Terminal=false
NoDisplay=true
Categories=Utility;
"""


def ensure_launcher_branding(home: Path | None = None, *, render_pngs: bool = True) -> Path:
    """Install the icon and app desktop file. Returns the icon path Qt should load."""
    svg = bundled_icon()
    installed = icon_svg_path(home)
    data = svg.read_bytes()
    if not installed.exists() or installed.read_bytes() != data:
        installed.parent.mkdir(parents=True, exist_ok=True)
        installed.write_bytes(data)

    desktop = desktop_entry_path(home)
    entry = desktop_entry_text(installed)
    if not desktop.exists() or desktop.read_text(encoding="utf-8") != entry:
        desktop.parent.mkdir(parents=True, exist_ok=True)
        desktop.write_text(entry, encoding="utf-8")

    if render_pngs and not _pngs_current(installed, home):
        _render_pngs(installed, home)

    patch_dolphin_servicemenu(dolphin_menu_path(home), installed)
    _refresh_thunar_menu(thunar_menu_path(home), installed)
    return svg if svg.is_file() else installed


def patch_dolphin_servicemenu(menu_path: Path, icon_path: Path) -> bool:
    """Point an existing Dolphin menu at the logo and at files as well as folders."""
    if not menu_path.is_file():
        return False
    text = menu_path.read_text(encoding="utf-8")
    updated = text
    if "all/allfiles" not in updated:
        updated = updated.replace(
            "MimeType=inode/directory;",
            "MimeType=inode/directory;all/allfiles;",
        )
    lines: list[str] = []
    for line in updated.splitlines(keepends=True):
        if line.startswith("Icon="):
            newline = "\n" if line.endswith("\n") else ""
            lines.append(f"Icon={icon_path}{newline}")
        else:
            lines.append(line)
    updated = "".join(lines)
    if updated == text:
        return False
    menu_path.write_text(updated, encoding="utf-8")
    return True


def _refresh_thunar_menu(uca_path: Path, icon_path: Path) -> None:
    if not uca_path.is_file():
        return
    script = Path(__file__).resolve().parents[2] / "scripts" / "thunar_uca.py"
    if not script.is_file():
        return
    import importlib.util

    spec = importlib.util.spec_from_file_location("indir_thunar_uca", script)
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.refresh_action_for_files(uca_path, icon_path)


def _pngs_current(svg: Path, home: Path | None) -> bool:
    root = home if home is not None else Path.home()
    try:
        svg_mtime = svg.stat().st_mtime
    except OSError:
        return False
    for size in PNG_SIZES:
        dest = root / ".local" / "share" / "icons" / "hicolor" / f"{size}x{size}" / "apps" / "indir.png"
        try:
            if not dest.is_file() or dest.stat().st_mtime < svg_mtime:
                return False
        except OSError:
            return False
    return True


def _render_pngs(svg: Path, home: Path | None) -> None:
    try:
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication, QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer
    except Exception:
        return

    root = home if home is not None else Path.home()
    app = QGuiApplication.instance()
    created = False
    if app is None:
        app = QGuiApplication([])
        created = True
    try:
        renderer = QSvgRenderer(str(svg))
        if not renderer.isValid():
            return
        for size in PNG_SIZES:
            dest = root / ".local" / "share" / "icons" / "hicolor" / f"{size}x{size}" / "apps" / "indir.png"
            image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()
            dest.parent.mkdir(parents=True, exist_ok=True)
            image.save(str(dest))
    except Exception:
        return
    finally:
        if created:
            app.quit()

    _refresh_icon_caches(root)


def _refresh_icon_caches(home: Path) -> None:
    hicolor = home / ".local" / "share" / "icons" / "hicolor"
    apps = home / ".local" / "share" / "applications"
    for cmd in (
        ["gtk-update-icon-cache", "-f", "-t", str(hicolor)],
        ["update-desktop-database", str(apps)],
    ):
        try:
            subprocess.run(cmd, check=False, capture_output=True)
        except OSError:
            pass


def installed_png_paths(home: Path | None = None) -> list[Path]:
    root = home if home is not None else Path.home()
    return [
        root / ".local" / "share" / "icons" / "hicolor" / f"{size}x{size}" / "apps" / "indir.png"
        for size in PNG_SIZES
    ]
