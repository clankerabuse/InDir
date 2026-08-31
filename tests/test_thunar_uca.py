from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "thunar_uca.py"
spec = importlib.util.spec_from_file_location("thunar_uca", SCRIPT)
assert spec is not None and spec.loader is not None
thunar_uca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(thunar_uca)


EXISTING_UCA = """<?xml version='1.0' encoding='UTF-8'?>
<actions>
	<action>
		<icon>utilities-terminal</icon>
		<name>Open Terminal Here</name>
		<unique-id>1111111111-1</unique-id>
		<command>xfce4-terminal --working-directory %f</command>
		<description>Open a terminal</description>
		<patterns>*</patterns>
		<directories/>
	</action>
</actions>
"""


def test_upsert_creates_file(tmp_path: Path) -> None:
    uca = tmp_path / "uca.xml"
    thunar_uca.upsert_action(uca, "/opt/indir/.venv/bin/indir")
    text = uca.read_text(encoding="utf-8")
    assert thunar_uca.UNIQUE_ID in text
    assert "InDir" in text
    assert "/opt/indir/.venv/bin/indir %f" in text
    assert "<directories />" in text or "<directories/>" in text
    assert thunar_uca.action_installed(uca)


def test_upsert_preserves_other_actions(tmp_path: Path) -> None:
    uca = tmp_path / "uca.xml"
    uca.write_text(EXISTING_UCA, encoding="utf-8")
    thunar_uca.upsert_action(uca, "/home/me/indir")
    tree_text = uca.read_text(encoding="utf-8")
    assert "Open Terminal Here" in tree_text
    assert "xfce4-terminal" in tree_text
    assert "InDir" in tree_text
    assert tree_text.count("<action>") == 2


def test_upsert_updates_command_in_place(tmp_path: Path) -> None:
    uca = tmp_path / "uca.xml"
    thunar_uca.upsert_action(uca, "/old/path/indir")
    thunar_uca.upsert_action(uca, "/new/path/indir")
    text = uca.read_text(encoding="utf-8")
    assert "/new/path/indir %f" in text
    assert "/old/path/indir" not in text
    assert text.count(thunar_uca.UNIQUE_ID) == 1


def test_format_command_quotes_spaces() -> None:
    cmd = thunar_uca.format_command("/home/me/My Projects/indir/.venv/bin/indir")
    assert cmd.startswith("'/home/me/My Projects/indir/.venv/bin/indir'")
    assert cmd.endswith(" %f")


def test_remove_action(tmp_path: Path) -> None:
    uca = tmp_path / "uca.xml"
    uca.write_text(EXISTING_UCA, encoding="utf-8")
    thunar_uca.upsert_action(uca, "/opt/indir")
    assert thunar_uca.remove_action(uca) is True
    text = uca.read_text(encoding="utf-8")
    assert "InDir" not in text
    assert "Open Terminal Here" in text
    assert thunar_uca.remove_action(uca) is False
