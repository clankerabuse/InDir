from __future__ import annotations

from pathlib import Path

from indir.branding import (
    desktop_entry_text,
    ensure_launcher_branding,
    patch_dolphin_servicemenu,
)


def test_ensure_launcher_branding_writes_icon_and_desktop(tmp_path: Path) -> None:
    icon = ensure_launcher_branding(tmp_path, render_pngs=False)
    installed = tmp_path / ".local/share/icons/hicolor/scalable/apps/indir.svg"
    desktop = tmp_path / ".local/share/applications/indir.desktop"

    assert icon.is_file()
    assert installed.read_bytes() == icon.read_bytes()
    text = desktop.read_text(encoding="utf-8")
    assert "Name=InDir" in text
    assert f"Icon={installed}" in text
    assert "Exec=indir" in text
    assert "StartupWMClass=indir" in text
    assert "NoDisplay=true" in text


def test_patch_dolphin_servicemenu_adds_files_and_icon(tmp_path: Path) -> None:
    menu = tmp_path / "indir.desktop"
    menu.write_text(
        """[Desktop Entry]
Type=Service
MimeType=inode/directory;
Actions=inDir;
Icon=system-run

[Desktop Action inDir]
Name=InDir
Exec=/opt/indir %U
""",
        encoding="utf-8",
    )
    icon = tmp_path / "indir.svg"
    icon.write_text("<svg/>", encoding="utf-8")

    assert patch_dolphin_servicemenu(menu, icon) is True
    text = menu.read_text(encoding="utf-8")
    assert "MimeType=inode/directory;all/allfiles;" in text
    assert f"Icon={icon}" in text
    assert "Exec=/opt/indir %U" in text
    assert patch_dolphin_servicemenu(menu, icon) is False


def test_desktop_entry_uses_absolute_icon(tmp_path: Path) -> None:
    icon = tmp_path / "indir.svg"
    text = desktop_entry_text(icon)
    assert f"Icon={icon}\n" in text
