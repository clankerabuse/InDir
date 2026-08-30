from __future__ import annotations

from pathlib import Path

import pytest

from indir.agent.tools import is_command_blocked, list_directory, run_command
from indir.config import ExecutionConfig, load_config
from indir.context import normalize_path


def test_normalize_path(tmp_path: Path) -> None:
    assert normalize_path(str(tmp_path)) == tmp_path.resolve()
    uri = f"file://{tmp_path}"
    assert normalize_path(uri) == tmp_path.resolve()


def test_load_config_defaults() -> None:
    config = load_config(Path("/nonexistent/config.toml"))
    assert config.backend.provider == "ollama"
    assert config.execution.mode == "confirm"


def test_is_command_blocked() -> None:
    blocklist = ["rm -rf /", "mkfs"]
    assert is_command_blocked("rm -rf / home", blocklist) == "rm -rf /"
    assert is_command_blocked("ls -la", blocklist) is None


def test_list_directory(tmp_path: Path) -> None:
    (tmp_path / "video.webm").write_text("fake")
    (tmp_path / "subdir").mkdir()
    result = list_directory(tmp_path)
    assert "video.webm" in result
    assert "[dir]" in result

    filtered = list_directory(tmp_path, "*.webm")
    assert "video.webm" in filtered
    assert "subdir" not in filtered


def test_run_command_blocked(tmp_path: Path) -> None:
    config = ExecutionConfig()
    result = run_command(tmp_path, "rm -rf /", config)
    assert result.blocked
    assert result.returncode == -1


def test_run_command_success(tmp_path: Path) -> None:
    config = ExecutionConfig()
    result = run_command(tmp_path, "echo hello", config)
    assert result.returncode == 0
    assert "hello" in result.stdout
