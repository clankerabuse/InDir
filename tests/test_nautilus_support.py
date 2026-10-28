from __future__ import annotations

import os
from pathlib import Path

import pytest

from indir import nautilus_support as support


class FakeLocation:
    def __init__(self, path: str | None) -> None:
        self._path = path

    def get_path(self) -> str | None:
        return self._path


class FakeFile:
    def __init__(self, path: str | None, *, directory: bool = False) -> None:
        self._location = FakeLocation(path)
        self._directory = directory

    def get_location(self) -> FakeLocation:
        return self._location

    def is_directory(self) -> bool:
        return self._directory


def _fake_cli(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


# ── selected_path ────────────────────────────────────────────────────────────


def test_selected_path_single_file() -> None:
    assert support.selected_path([FakeFile("/home/me/clip.webm")]) == "/home/me/clip.webm"


def test_selected_path_single_folder() -> None:
    files = [FakeFile("/home/me/Projects", directory=True)]
    assert support.selected_path(files) == "/home/me/Projects"


def test_selected_path_rejects_multiple() -> None:
    files = [FakeFile("/home/me/a"), FakeFile("/home/me/b")]
    assert support.selected_path(files) is None


def test_selected_path_rejects_empty() -> None:
    assert support.selected_path([]) is None
    assert support.selected_path(None) is None


def test_selected_path_rejects_remote_location() -> None:
    assert support.selected_path([FakeFile(None)]) is None
    assert support.selected_path([FakeFile("")]) is None


def test_selected_path_rejects_object_without_location() -> None:
    assert support.selected_path([object()]) is None


# ── build_command ────────────────────────────────────────────────────────────


def test_build_command_is_argv_pairs() -> None:
    assert support.build_command("/opt/indir/.venv/bin/indir", "/home/me/a b") == [
        "/opt/indir/.venv/bin/indir",
        "/home/me/a b",
    ]


# ── resolve_cli_path ─────────────────────────────────────────────────────────


def test_resolve_prefers_path_lookup(tmp_path: Path) -> None:
    found = _fake_cli(tmp_path / "bin" / "indir")
    cli = support.resolve_cli_path(
        which=lambda name: str(found),
        home=tmp_path / "home",
        repo_root=tmp_path / "repo",
    )
    assert cli == str(found)


def test_resolve_falls_back_to_local_bin(tmp_path: Path) -> None:
    home = tmp_path / "home"
    local = _fake_cli(home / ".local" / "bin" / "indir")
    cli = support.resolve_cli_path(
        which=lambda name: None, home=home, repo_root=tmp_path / "repo"
    )
    assert cli == str(local)


def test_resolve_falls_back_to_repo_venv(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    venv_cli = _fake_cli(repo / ".venv" / "bin" / "indir")
    cli = support.resolve_cli_path(
        which=lambda name: None, home=tmp_path / "home", repo_root=repo
    )
    assert cli == str(venv_cli)


def test_resolve_returns_none_when_missing(tmp_path: Path) -> None:
    assert (
        support.resolve_cli_path(
            which=lambda name: None, home=tmp_path / "home", repo_root=tmp_path / "r"
        )
        is None
    )


def test_resolve_ignores_non_executable(tmp_path: Path) -> None:
    home = tmp_path / "home"
    stale = home / ".local" / "bin" / "indir"
    stale.parent.mkdir(parents=True)
    stale.write_text("#!/bin/sh\n", encoding="utf-8")
    stale.chmod(0o644)
    assert (
        support.resolve_cli_path(
            which=lambda name: None, home=home, repo_root=tmp_path / "repo"
        )
        is None
    )
    assert not os.access(stale, os.X_OK)


def test_support_name_matches_installer_expectation() -> None:
    assert support.SUPPORT_NAME == "indir_nautilus_support.py"


def test_menu_identity() -> None:
    assert support.MENU_LABEL == "InDir"
    assert support.MENU_ITEM_NAME.startswith("InDir")
