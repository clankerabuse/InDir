from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "indir"
CONFIG_PATH = CONFIG_DIR / "config.toml"
LEGACY_CONFIG_DIR = Path.home() / ".config" / "directory-ai"
LEGACY_CONFIG_PATH = LEGACY_CONFIG_DIR / "config.toml"
STATE_DIR = Path.home() / ".local" / "state" / "indir"
LEGACY_STATE_DIR = Path.home() / ".local" / "state" / "directory-ai"
HISTORY_LOG = STATE_DIR / "history.log"


def _resolve_api_key(inline_key: str, env_var: str) -> str | None:
    if inline_key.strip():
        return inline_key.strip()
    return os.environ.get(env_var)


@dataclass
class OllamaConfig:
    base_url: str = "http://localhost:11434"
    model: str = "llama3.2"


@dataclass
class OpenAIConfig:
    api_key_env: str = "OPENAI_API_KEY"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"

    def resolved_api_key(self) -> str | None:
        return _resolve_api_key(self.api_key, self.api_key_env)


@dataclass
class AnthropicConfig:
    api_key_env: str = "ANTHROPIC_API_KEY"
    api_key: str = ""
    model: str = "claude-sonnet-4-20250514"

    def resolved_api_key(self) -> str | None:
        return _resolve_api_key(self.api_key, self.api_key_env)


@dataclass
class GrokConfig:
    api_key_env: str = "XAI_API_KEY"
    api_key: str = ""
    base_url: str = "https://api.x.ai/v1"
    model: str = "grok-3"

    def resolved_api_key(self) -> str | None:
        return _resolve_api_key(self.api_key, self.api_key_env)


@dataclass
class CursorConfig:
    api_key_env: str = "CURSOR_API_KEY"
    api_key: str = ""
    model: str = "composer-2.5"
    local_cwd: bool = True

    def resolved_api_key(self) -> str | None:
        return _resolve_api_key(self.api_key, self.api_key_env)


@dataclass
class BackendConfig:
    provider: str = "ollama"
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    openai: OpenAIConfig = field(default_factory=OpenAIConfig)
    grok: GrokConfig = field(default_factory=GrokConfig)
    anthropic: AnthropicConfig = field(default_factory=AnthropicConfig)
    cursor: CursorConfig = field(default_factory=CursorConfig)


@dataclass
class ExecutionConfig:
    mode: str = "confirm"
    blocklist: list[str] = field(
        default_factory=lambda: ["rm -rf /", "mkfs", ":(){ :|:& };:"]
    )
    max_output_bytes: int = 65536


@dataclass
class UIConfig:
    mode: str = "qt"


@dataclass
class AppConfig:
    backend: BackendConfig = field(default_factory=BackendConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    ui: UIConfig = field(default_factory=UIConfig)


def _merge_dict(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _dict_to_config(data: dict) -> AppConfig:
    backend_data = data.get("backend", {})
    execution_data = data.get("execution", {})
    ui_data = data.get("ui", {})

    return AppConfig(
        backend=BackendConfig(
            provider=backend_data.get("provider", "ollama"),
            ollama=OllamaConfig(**backend_data.get("ollama", {})),
            openai=OpenAIConfig(**backend_data.get("openai", {})),
            grok=GrokConfig(**backend_data.get("grok", {})),
            anthropic=AnthropicConfig(**backend_data.get("anthropic", {})),
            cursor=CursorConfig(**backend_data.get("cursor", {})),
        ),
        execution=ExecutionConfig(
            mode=execution_data.get("mode", "confirm"),
            blocklist=execution_data.get(
                "blocklist",
                ["rm -rf /", "mkfs", ":(){ :|:& };:"],
            ),
            max_output_bytes=execution_data.get("max_output_bytes", 65536),
        ),
        ui=UIConfig(mode=ui_data.get("mode", "qt")),
    )


def _resolve_config_path(path: Path | None) -> Path:
    if path is not None:
        return path
    if CONFIG_PATH.exists():
        return CONFIG_PATH
    if LEGACY_CONFIG_PATH.exists():
        return LEGACY_CONFIG_PATH
    return CONFIG_PATH


def load_config(path: Path | None = None) -> AppConfig:
    config_path = _resolve_config_path(path)
    if not config_path.exists():
        return AppConfig()
    with config_path.open("rb") as f:
        data = tomllib.load(f)
    return _dict_to_config(data)


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _toml_list(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(v) for v in values) + "]"


def config_to_toml(config: AppConfig) -> str:
    """Serialize an AppConfig back into TOML text."""
    backend = config.backend
    lines = [
        "[backend]",
        f"provider = {_toml_string(backend.provider)}",
        "",
        "[backend.ollama]",
        f"base_url = {_toml_string(backend.ollama.base_url)}",
        f"model = {_toml_string(backend.ollama.model)}",
        "",
        "[backend.openai]",
        f"api_key_env = {_toml_string(backend.openai.api_key_env)}",
        f"api_key = {_toml_string(backend.openai.api_key)}",
        f"base_url = {_toml_string(backend.openai.base_url)}",
        f"model = {_toml_string(backend.openai.model)}",
        "",
        "[backend.grok]",
        f"api_key_env = {_toml_string(backend.grok.api_key_env)}",
        f"api_key = {_toml_string(backend.grok.api_key)}",
        f"base_url = {_toml_string(backend.grok.base_url)}",
        f"model = {_toml_string(backend.grok.model)}",
        "",
        "[backend.anthropic]",
        f"api_key_env = {_toml_string(backend.anthropic.api_key_env)}",
        f"api_key = {_toml_string(backend.anthropic.api_key)}",
        f"model = {_toml_string(backend.anthropic.model)}",
        "",
        "[backend.cursor]",
        f"api_key_env = {_toml_string(backend.cursor.api_key_env)}",
        f"api_key = {_toml_string(backend.cursor.api_key)}",
        f"model = {_toml_string(backend.cursor.model)}",
        f"local_cwd = {_toml_bool(backend.cursor.local_cwd)}",
        "",
        "[execution]",
        f"mode = {_toml_string(config.execution.mode)}",
        f"blocklist = {_toml_list(config.execution.blocklist)}",
        f"max_output_bytes = {config.execution.max_output_bytes}",
        "",
        "[ui]",
        f"mode = {_toml_string(config.ui.mode)}",
        "",
    ]
    return "\n".join(lines)


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    """Write an AppConfig to disk as TOML, creating the config dir if needed."""
    config_path = path or _resolve_config_path(None)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_to_toml(config), encoding="utf-8")
    config_path.chmod(0o600)
    return config_path


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if STATE_DIR.exists():
        STATE_DIR.chmod(0o700)
    if HISTORY_LOG.exists():
        HISTORY_LOG.chmod(0o600)


def ensure_config_permissions(path: Path | None = None) -> None:
    config_path = path or _resolve_config_path(None)
    if config_path.exists():
        config_path.chmod(0o600)
