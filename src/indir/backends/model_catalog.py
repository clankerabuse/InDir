from __future__ import annotations

"""Fetches the list of available models for a backend using its API.

Used by the settings UI so users never have to hardcode model names —
plug in a base URL / API key and the dropdown populates itself.
"""

import json
import re
import uuid
from urllib.parse import urlparse

import httpx

_TIMEOUT = 15.0

OPENCODE_ZEN_BASE_URL = "https://opencode.ai/zen/v1"
OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"

# Both OpenCode gateways reject /chat/completions that arrive without a session
# id: "Request is missing x-opencode-session and cannot be routed efficiently"
# (HTTP 400). One id per conversation is what the OpenCode client sends.
OPENCODE_SESSION_HEADER = "x-opencode-session"

# Probe model for gateway detection: must exist on both the Zen and the Go
# catalog, otherwise detection reports a gateway error instead of a gateway.
_OPENCODE_PROBE_MODEL = "kimi-k2.7-code"

# The Go gateway lists every model it carries, but these are served only on the
# Responses or Messages API and reject /chat/completions with
# "ModelProtocolUnsupported". Verified against the live Go catalog.
_OPENCODE_GO_UNSUPPORTED = frozenset(
    {
        "claude-haiku-5-5",
        "gemini-4-argon-contributor",
        "gpt-5.6-luna",
        "gpt-6-luna",
        "grok-4.6",
        "grok-4.7",
        "minimax-m2.7",
        "muse-spark-1.2-contributor",
        "muse-spark-1.3-contributor",
    }
)


def is_opencode_gateway(base_url: str) -> bool:
    """True when the base URL points at an OpenCode gateway (Zen or Go)."""
    host = urlparse(base_url).hostname or ""
    return host == "opencode.ai" or host.endswith(".opencode.ai")


def new_opencode_session_id() -> str:
    return str(uuid.uuid4())


def opencode_headers(api_key: str, session_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if session_id:
        headers[OPENCODE_SESSION_HEADER] = session_id
    return headers


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


def detect_opencode_base_url(api_key: str) -> str:
    """Return the OpenCode gateway URL for the given key.

    The same key works on both gateways, but only Go subscription keys are
    authorized on the Go endpoint. A minimal probe tells the two apart; Go is
    preferred because it is flat-rate instead of pay-per-use.
    """
    if not api_key:
        raise ValueError("An API key is required to detect the OpenCode gateway.")
    with httpx.Client(
        base_url=OPENCODE_GO_BASE_URL,
        headers=opencode_headers(api_key, new_opencode_session_id()),
        timeout=_TIMEOUT,
    ) as client:
        response = client.post(
            "/chat/completions",
            json={
                "model": _OPENCODE_PROBE_MODEL,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            },
        )
    if response.is_success:
        return OPENCODE_GO_BASE_URL
    if response.status_code in (401, 403):
        # Not authorized on Go: either a Zen pay-per-use key, or a dead key.
        # Zen has to answer for the key before we call it a Zen key, otherwise
        # a stale key shows up as a confusing "401 on the Zen endpoint".
        if _opencode_key_reaches_zen(api_key):
            return OPENCODE_ZEN_BASE_URL
        raise ValueError(
            "OpenCode rejected this API key on both the Go and Zen gateways, so it "
            "looks invalid or expired. Copy a current key from "
            "https://opencode.ai/auth (Go subscription keys start with 'oc_sk_')."
        )
    # A Go key whose probe model was retired still gets past auth and reaches the
    # upstream, so a model-level rejection also proves this is the Go gateway —
    # unless Zen rejects the key too, which means the key is dead after all.
    if _is_model_level_rejection(response):
        if not _opencode_key_reaches_zen(api_key):
            raise ValueError(
                "OpenCode rejected this API key on both the Go and Zen gateways, so it "
                "looks invalid or expired. Copy a current key from "
                "https://opencode.ai/auth (Go subscription keys start with 'oc_sk_')."
            )
        return OPENCODE_GO_BASE_URL
    raise RuntimeError(
        f"OpenCode gateway detection failed (HTTP {response.status_code}): "
        f"{_redact_secrets(response.text[:200])}"
    )


# Error type/code strings the gateways use for "that model is gone", normalised
# to lowercase with separators removed: model_not_found == ModelNotFound.
_MODEL_REJECTION_CODES = frozenset(
    {
        "modeldeprecated",
        "modelretired",
        "modelnotfound",
        "modelunavailable",
        "modelnotsupported",
        "invalidmodel",
        "unknownmodel",
        "unsupportedmodel",
    }
)

# Prose fallbacks, matched against the error *message* only.
_MODEL_REJECTION_MARKERS = (
    "model is unavailable",
    "model has been deprecated",
    "model is deprecated",
    "model was retired",
    "no longer available",
    "does not exist",
    "unknown model",
    "invalid model",
    "unsupported model",
)

_SECRET_PATTERN = re.compile(r"(oc_sk_|sk-ant-|sk-|xai-|gh[pousr]_|Bearer\s+)[A-Za-z0-9_\-]{6,}")


def _redact_secrets(text: str) -> str:
    """Strip anything that looks like a credential from gateway error text.

    Gateways sometimes echo the submitted key back in the rejection body, and the
    message ends up in the UI and the terminal.
    """
    return _SECRET_PATTERN.sub(r"\1[redacted]", text)


def _error_fields(response: httpx.Response) -> tuple[str, str]:
    """(type-or-code, message) from a gateway error body, lowercased.

    Only the structured error object is consulted: a 400 that merely echoes the
    submitted request (which contains "model": "...") must not look like a
    model-level rejection.
    """
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return "", response.text.lower()
    if not isinstance(payload, dict):
        return "", ""
    error = payload.get("error")
    if isinstance(error, dict):
        code = error.get("type") or error.get("code") or ""
        message = error.get("message") or ""
        return str(code).lower(), str(message).lower()
    # Some endpoints report {"type": ..., "message": ...} without the wrapper.
    code = payload.get("type") or payload.get("code") or ""
    return str(code).lower(), str(payload.get("message") or "").lower()


def _is_model_level_rejection(response: httpx.Response) -> bool:
    """True when the gateway rejected the probe *model*, not the credential.

    A retired probe model answers 400 ("Model is unavailable") or 410
    ({"type": "ModelDeprecated", ...}). Both mean authentication succeeded first,
    so the request must not be reported as a bad key or a broken gateway. A 410
    counts even with an empty body: Gone is only served once auth has passed.
    """
    if response.status_code == 410:
        return True
    if response.status_code != 400:
        return False
    code, message = _error_fields(response)
    normalised_code = re.sub(r"[\s_\-]", "", code)
    if normalised_code in _MODEL_REJECTION_CODES:
        return True
    return any(marker in message for marker in _MODEL_REJECTION_MARKERS)


def _opencode_key_reaches_zen(api_key: str) -> bool:
    """True unless Zen explicitly rejects the key.

    An unreachable or unexpected gateway must not be reported as a dead key.
    """
    with httpx.Client(
        base_url=OPENCODE_ZEN_BASE_URL,
        headers=opencode_headers(api_key),
        timeout=_TIMEOUT,
    ) as client:
        try:
            response = client.get("/models")
        except httpx.HTTPError:
            return True
    return response.status_code not in (401, 403)


def list_opencode_models(base_url: str, api_key: str) -> list[str]:
    ids = list_openai_compatible_models(base_url, api_key)
    if base_url.rstrip("/") == OPENCODE_GO_BASE_URL:
        ids = [m for m in ids if m not in _OPENCODE_GO_UNSUPPORTED]
    return ids


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
    if provider == "opencode":
        return list_opencode_models(base_url, api_key)
    if provider == "anthropic":
        return list_anthropic_models(api_key)
    if provider == "cursor":
        return list_cursor_models(api_key)
    raise ValueError(f"Model listing is not supported for provider: {provider}")
