from __future__ import annotations

"""Fetches the list of available models for a backend using its API.

Used by the settings UI so users never have to hardcode model names —
plug in a base URL / API key and the dropdown populates itself.
"""

import httpx

_TIMEOUT = 15.0


def list_ollama_models(base_url: str) -> list[str]:
    with httpx.Client(base_url=base_url, timeout=_TIMEOUT) as client:
        response = client.get("/api/tags")
        response.raise_for_status()
        data = response.json()
    names = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    return sorted(names)


def list_openai_compatible_models(base_url: str, api_key: str) -> list[str]:
    if not api_key:
        raise ValueError("An API key is required to list models.")
    with httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=_TIMEOUT,
    ) as client:
        response = client.get("/models")
        response.raise_for_status()
        data = response.json()
    ids = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
    return sorted(ids)


def list_anthropic_models(api_key: str) -> list[str]:
    if not api_key:
        raise ValueError("An API key is required to list models.")
    with httpx.Client(
        base_url="https://api.anthropic.com",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        timeout=_TIMEOUT,
    ) as client:
        response = client.get("/v1/models")
        response.raise_for_status()
        data = response.json()
    ids = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
    return sorted(ids, reverse=True)


def list_cursor_models(api_key: str) -> list[str]:
    if not api_key:
        raise ValueError("An API key is required to list models.")
    try:
        from cursor_sdk import Cursor
    except ImportError as exc:
        raise ImportError(
            "cursor-sdk is not installed. Install with: pip install indir[cursor]"
        ) from exc
    models = Cursor.models.list(api_key=api_key)
    return sorted(m.id for m in models if m.id)


def list_models(provider: str, *, base_url: str = "", api_key: str = "") -> list[str]:
    """Return available model IDs for the given provider, or raise on failure."""
    if provider == "ollama":
        return list_ollama_models(base_url)
    if provider in ("openai", "grok"):
        return list_openai_compatible_models(base_url, api_key)
    if provider == "anthropic":
        return list_anthropic_models(api_key)
    if provider == "cursor":
        return list_cursor_models(api_key)
    raise ValueError(f"Model listing is not supported for provider: {provider}")
