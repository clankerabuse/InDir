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
THUNAR_UCA_PATH = Path.home() / ".config" / "Thunar" / "uca.xml"
THUNAR_UNIQUE_ID = "indir-thunar-1"
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

    dolphin_bin = shutil.which("dolphin")
    menu_path = SERVICEMENU_PATH if SERVICEMENU_PATH.exists() else LEGACY_SERVICEMENU_PATH
    dolphin_ok = menu_path.exists()
    if dolphin_ok:
        ok(f"Dolphin menu: {menu_path}")
        text = menu_path.read_text(encoding="utf-8")
        if "Exec=indir " in text and "Exec=/" not in text:
            warn("Service menu uses bare 'indir' — re-run ./install to fix")
        if not menu_path.stat().st_mode & 0o111:
            warn(f"Service menu not executable — run: chmod +x {menu_path}")
    elif dolphin_bin:
        warn(f"Dolphin menu missing: {SERVICEMENU_PATH}")
    else:
        warn("Dolphin not installed — skipped Dolphin menu check")

    thunar_bin = shutil.which("thunar")
    thunar_text = (
        THUNAR_UCA_PATH.read_text(encoding="utf-8", errors="replace")
        if THUNAR_UCA_PATH.is_file()
        else ""
    )
    thunar_ok = THUNAR_UNIQUE_ID in thunar_text
    if thunar_ok:
        ok(f"Thunar menu: {THUNAR_UCA_PATH}")
        if ">indir %f<" in thunar_text or ">indir %f</command>" in thunar_text:
            warn("Thunar action uses bare 'indir' — re-run ./install to fix")
    elif thunar_bin:
        warn("Thunar custom action missing — re-run ./install to add it")
    else:
        warn("Thunar not installed — skipped Thunar menu check")

    if not dolphin_ok and not thunar_ok and (dolphin_bin or thunar_bin):
        fail("No file-manager menu installed — re-run ./install")
        errors += 1

    try:
        from indir.backends.registry import create_backend

        config = load_config()
        provider = config.backend.provider
        ok(f"Backend provider: {provider}")

        if provider == "ollama":
            base_url = config.backend.ollama.base_url
            model = config.backend.ollama.model
            try:
                import json

                with urllib.request.urlopen(f"{base_url}/api/tags", timeout=3) as resp:
                    tags = json.loads(resp.read().decode())
                ok(f"Ollama reachable at {base_url}")
                installed = [m["name"] for m in tags.get("models", [])]
                if model in installed:
                    ok(f"Ollama model: {model}")
                elif installed:
                    matches = [m for m in installed if m.startswith(f"{model}:")]
                    warn(f"Ollama model '{model}' not installed — pick a model in settings")
                    if matches:
                        warn(f"Did you mean '{matches[0]}'?")
                    else:
                        warn(f"Installed models: {', '.join(installed)}")
                else:
                    warn("No Ollama models installed — pull one or pick a cloud backend in settings")
            except Exception:
                warn("Ollama not reachable — start with: ollama serve, or pick another backend in settings")
        elif provider in ("grok", "openai", "anthropic", "cursor", "opencode"):
            section = getattr(config.backend, provider)
            if section.resolved_api_key():
                ok(f"{provider} API key configured")
            else:
                warn(
                    f"{provider} API key not set yet — open InDir settings (gear) to configure"
                )

        try:
            create_backend(config, Path.home())
            ok("Backend initialized successfully")
        except Exception as exc:
            warn(f"Backend not ready yet ({exc}) — configure via settings if needed")
    except ImportError:
        warn("Package not importable — install with ./install first")
    except Exception as exc:
        fail(f"Backend check failed: {exc}")
        errors += 1

    print()
    if errors:
        print(f"{errors} issue(s) found.")
        return 1
    print("All checks passed. Right-click a folder → InDir → gear icon for backend settings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
