#!/usr/bin/env python3
"""Write or update indir config during installation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "indir"
CONFIG_PATH = CONFIG_DIR / "config.toml"

PROVIDERS = ("ollama", "grok", "openai", "anthropic", "cursor")

# Reference defaults for sections not in use (overwritten for active provider)
SECTION_DEFAULTS = {
    "ollama": ("qwen2.5:7b",),
    "openai": ("gpt-4o-mini",),
    "grok": ("grok-3",),
    "anthropic": ("claude-sonnet-4-20250514",),
    "cursor": ("composer-2.5",),
}


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_config(provider: str, model: str, api_key: str = "") -> str:
    if not model:
        raise ValueError("model is required")

    models = {
        "ollama": SECTION_DEFAULTS["ollama"][0],
        "openai": SECTION_DEFAULTS["openai"][0],
        "grok": SECTION_DEFAULTS["grok"][0],
        "anthropic": SECTION_DEFAULTS["anthropic"][0],
        "cursor": SECTION_DEFAULTS["cursor"][0],
    }
    models[provider] = model

    lines = [
        "[backend]",
        f"provider = {_toml_string(provider)}",
        "",
        "[backend.ollama]",
        'base_url = "http://localhost:11434"',
        f"model = {_toml_string(models['ollama'])}",
        "",
        "[backend.openai]",
        'api_key_env = "OPENAI_API_KEY"',
        'base_url = "https://api.openai.com/v1"',
        f"model = {_toml_string(models['openai'])}",
    ]
    if provider == "openai" and api_key:
        lines.append(f"api_key = {_toml_string(api_key)}")

    lines.extend(
        [
            "",
            "[backend.grok]",
            'api_key_env = "XAI_API_KEY"',
            'base_url = "https://api.x.ai/v1"',
            f"model = {_toml_string(models['grok'])}",
        ]
    )
    if provider == "grok" and api_key:
        lines.append(f"api_key = {_toml_string(api_key)}")

    lines.extend(
        [
            "",
            "[backend.anthropic]",
            'api_key_env = "ANTHROPIC_API_KEY"',
            f"model = {_toml_string(models['anthropic'])}",
        ]
    )
    if provider == "anthropic" and api_key:
        lines.append(f"api_key = {_toml_string(api_key)}")

    lines.extend(
        [
            "",
            "[backend.cursor]",
            'api_key_env = "CURSOR_API_KEY"',
            f"model = {_toml_string(models['cursor'])}",
            "local_cwd = true",
        ]
    )
    if provider == "cursor" and api_key:
        lines.append(f"api_key = {_toml_string(api_key)}")

    lines.extend(
        [
            "",
            "[execution]",
            'mode = "confirm"',
            'blocklist = ["rm -rf /", "mkfs", ":(){ :|:& };:"]',
            "max_output_bytes = 65536",
            "",
            "[ui]",
            'mode = "qt"',
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write indir config")
    parser.add_argument("--provider", choices=PROVIDERS, required=True)
    parser.add_argument("--model", required=True, help="Model ID for the chosen provider")
    parser.add_argument("--api-key", default="", help="Inline API key for cloud backends")
    parser.add_argument("--output", type=Path, default=CONFIG_PATH)
    parser.add_argument("--force", action="store_true", help="Overwrite existing config")
    args = parser.parse_args(argv)

    if args.output.exists() and not args.force:
        print(f"Config already exists: {args.output} (use --force to overwrite)")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    content = build_config(args.provider, args.model, args.api_key)
    args.output.write_text(content, encoding="utf-8")
    args.output.chmod(0o600)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
