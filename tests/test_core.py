from __future__ import annotations

from pathlib import Path

import pytest

from indir.agent.tools import is_command_blocked, list_directory, run_command
from indir.config import ExecutionConfig, load_config, save_config
from indir.context import (
    build_directory_summary,
    build_suggested_commands,
    build_system_prompt,
    normalize_path,
    resolve_run_command,
)


def test_build_directory_summary(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_text("x")
    (tmp_path / "b.jpg").write_text("x")
    (tmp_path / "c.jpg").write_text("x")
    summary = build_directory_summary(tmp_path)
    assert "1 .mp4 file: a.mp4" in summary
    assert "2 .jpg files" in summary


def test_build_suggested_commands_single_mp4(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_text("x")
    suggested = build_suggested_commands(tmp_path)
    assert "clip.mp4" in suggested
    assert "clip.webm" in suggested
    assert "command -v ffmpeg" in suggested


def test_resolve_run_command_uses_exact_suggestion(tmp_path: Path) -> None:
    (tmp_path / "my clip.mp4").write_text("x")
    bad = 'ffmpeg -y -i "wrong.mp4" out.webm'
    fixed = resolve_run_command(tmp_path, bad)
    assert "my clip.mp4" in fixed
    assert "my clip.webm" in fixed
    assert "command -v ffmpeg" in fixed


def test_build_system_prompt_includes_conditioning(tmp_path: Path) -> None:
    (tmp_path / "example.txt").write_text("hi")
    prompt = build_system_prompt(tmp_path)

    assert str(tmp_path) in prompt
    assert "example.txt" in prompt
    assert "primary reference for the chat" in prompt
    assert "pacman -S" in prompt
    assert "Task recipes" in prompt
    assert "Never respond with an empty message" in prompt


def test_normalize_path(tmp_path: Path) -> None:
    assert normalize_path(str(tmp_path)) == tmp_path.resolve()
    uri = f"file://{tmp_path}"
    assert normalize_path(uri) == tmp_path.resolve()


def test_load_config_defaults() -> None:
    config = load_config(Path("/nonexistent/config.toml"))
    assert config.backend.provider == "ollama"
    assert config.execution.mode == "confirm"


def test_opencode_config_round_trip(tmp_path: Path) -> None:
    config = load_config(Path("/nonexistent/config.toml"))
    config.backend.provider = "opencode"
    config.backend.opencode.api_key = "oc-test-key"
    config.backend.opencode.model = "glm-5.3"

    path = save_config(config, tmp_path / "config.toml")
    loaded = load_config(path)

    assert loaded.backend.provider == "opencode"
    assert loaded.backend.opencode.api_key == "oc-test-key"
    assert loaded.backend.opencode.model == "glm-5.3"
    assert loaded.backend.opencode.base_url == "https://opencode.ai/zen/v1"
    assert loaded.backend.opencode.resolved_api_key() == "oc-test-key"


def test_create_backend_opencode(tmp_path: Path) -> None:
    from indir.backends.openai_compat import OpenAICompatBackend
    from indir.backends.registry import create_backend

    config = load_config(Path("/nonexistent/config.toml"))
    config.backend.provider = "opencode"
    config.backend.opencode.api_key = "oc-test-key"

    backend = create_backend(config, tmp_path)
    assert isinstance(backend, OpenAICompatBackend)
    assert backend.config.model == "kimi-k2.6"


def test_create_backend_opencode_requires_key(tmp_path: Path, monkeypatch) -> None:
    from indir.backends.registry import create_backend

    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    config = load_config(Path("/nonexistent/config.toml"))
    config.backend.provider = "opencode"

    with pytest.raises(ValueError, match="API key"):
        create_backend(config, tmp_path)


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
