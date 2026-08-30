#!/usr/bin/env python3
"""Verify indir installation."""

from __future__ import annotations

import shutil
import sys
import urllib.request
from pathlib import Path

from indir.config import CONFIG_PATH, LEGACY_CONFIG_PATH, load_config

SERVICEMENU_PATH = (
    Path.home() / ".local" / "share" / "kio" / "servicemenus" / "indir.desktop"
)
LEGACY_SERVICEMENU_PATH = (
    Path.home() / ".local" / "share" / "kio" / "servicemenus" / "ai-assistant.desktop"
)
LOCAL_BIN = Path.home() / ".local" / "bin" / "indir"


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def warn(msg: str) -> None:
    print(f"  ! {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")


def main() -> int:
    print("InDir — installation check\n")
    errors = 0

    cli = shutil.which("indir") or (
        str(LOCAL_BIN) if LOCAL_BIN.is_symlink() or LOCAL_BIN.exists() else None
    )
    if cli:
        ok(f"CLI found: {cli}")
    else:
        fail("CLI not found (run ./install)")
        errors += 1

    config_path = CONFIG_PATH if CONFIG_PATH.exists() else LEGACY_CONFIG_PATH
    if config_path.exists():
        ok(f"Config: {config_path}")
        if config_path.stat().st_mode & 0o077:
            warn(f"Config is world-readable — run: chmod 600 {config_path}")
    else:
        fail(f"Config missing: {CONFIG_PATH}")
        errors += 1

    menu_path = SERVICEMENU_PATH if SERVICEMENU_PATH.exists() else LEGACY_SERVICEMENU_PATH
    if menu_path.exists():
        ok(f"Dolphin menu: {menu_path}")
        text = menu_path.read_text(encoding="utf-8")
        if "Exec=indir " in text and "Exec=/" not in text:
            warn("Service menu uses bare 'indir' — re-run ./install to fix")
    else:
        fail(f"Dolphin menu missing: {SERVICEMENU_PATH}")
        errors += 1

    try:
        from indir.backends.registry import create_backend

        config = load_config()
        provider = config.backend.provider
        ok(f"Backend provider: {provider}")

        if provider == "ollama":
            try:
                urllib.request.urlopen(
                    f"{config.backend.ollama.base_url}/api/tags", timeout=3
                )
                ok(f"Ollama reachable at {config.backend.ollama.base_url}")
            except Exception:
                warn("Ollama not reachable — start with: ollama serve")
        elif provider in ("grok", "openai", "anthropic", "cursor"):
            section = getattr(config.backend, provider)
            if section.resolved_api_key():
                ok(f"{provider} API key configured")
            else:
                fail(f"{provider} API key missing — re-run ./install or set api_key in config")
                errors += 1

        create_backend(config, Path.home())
        ok("Backend initialized successfully")
    except ImportError:
        warn("Package not importable — install with ./install first")
    except Exception as exc:
        fail(f"Backend check failed: {exc}")
        errors += 1

    print()
    if errors:
        print(f"{errors} issue(s) found.")
        return 1
    print("All checks passed. Right-click a folder in Dolphin → InDir")
    return 0


if __name__ == "__main__":
    sys.exit(main())
