from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from indir.backends.model_catalog import (
    OPENCODE_GO_BASE_URL,
    OPENCODE_SESSION_HEADER,
    OPENCODE_ZEN_BASE_URL,
    detect_opencode_base_url,
    is_opencode_gateway,
    list_cursor_models,
    list_models,
    list_opencode_models,
)
from indir.config import OpenCodeConfig


def test_list_cursor_models_requires_api_key() -> None:
    with pytest.raises(ValueError, match="API key"):
        list_cursor_models("")


def test_list_cursor_models_returns_sorted_ids() -> None:
    mock_models = [
        MagicMock(id="composer-2.5"),
        MagicMock(id="auto"),
        MagicMock(id=""),
    ]
    with patch("cursor_sdk.Cursor.models.list", return_value=mock_models):
        assert list_cursor_models("cursor_test_key") == ["auto", "composer-2.5"]


def test_list_models_cursor_delegates() -> None:
    with patch(
        "indir.backends.model_catalog.list_cursor_models",
        return_value=["composer-2.5"],
    ) as list_cursor:
        assert list_models("cursor", api_key="key") == ["composer-2.5"]
        list_cursor.assert_called_once_with("key")


def test_list_models_opencode_delegates() -> None:
    with patch(
        "indir.backends.model_catalog.list_opencode_models",
        return_value=["kimi-k2.6"],
    ) as list_opencode:
        assert (
            list_models(
                "opencode",
                base_url=OPENCODE_ZEN_BASE_URL,
                api_key="key",
            )
            == ["kimi-k2.6"]
        )
        list_opencode.assert_called_once_with(OPENCODE_ZEN_BASE_URL, "key")


def test_list_opencode_models_filters_go_rejects() -> None:
    with patch(
        "indir.backends.model_catalog.list_openai_compatible_models",
        return_value=["grok-4.6", "kimi-k2.6"],
    ):
        assert list_opencode_models(OPENCODE_GO_BASE_URL, "key") == ["kimi-k2.6"]
        # Zen serves its whole catalog over chat/completions — no filtering.
        assert list_opencode_models(OPENCODE_ZEN_BASE_URL, "key") == [
            "grok-4.6",
            "kimi-k2.6",
        ]


def test_detect_opencode_base_url_requires_key() -> None:
    with pytest.raises(ValueError, match="API key"):
        detect_opencode_base_url("")


def test_detect_opencode_base_url_go(httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{OPENCODE_GO_BASE_URL}/chat/completions",
        json={"choices": [{"message": {"content": "hi"}}]},
    )
    assert detect_opencode_base_url("go-key") == OPENCODE_GO_BASE_URL


def test_detect_opencode_base_url_zen(httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{OPENCODE_GO_BASE_URL}/chat/completions",
        status_code=401,
        json={"type": "error", "error": {"type": "AuthError", "message": "Invalid API key."}},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{OPENCODE_ZEN_BASE_URL}/models",
        json={"data": [{"id": "kimi-k2.6"}]},
    )
    assert detect_opencode_base_url("zen-only-key") == OPENCODE_ZEN_BASE_URL


def test_detect_opencode_base_url_dead_key_says_so(httpx_mock) -> None:
    """A stale key must not be reported as a Zen key with a raw 401."""
    httpx_mock.add_response(
        method="POST",
        url=f"{OPENCODE_GO_BASE_URL}/chat/completions",
        status_code=401,
        json={"error": {"type": "server_error", "message": "Invalid credential"}},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{OPENCODE_ZEN_BASE_URL}/models",
        status_code=401,
        json={"error": {"type": "server_error", "message": "Invalid credential"}},
    )
    with pytest.raises(ValueError, match="both the Go and Zen gateways"):
        detect_opencode_base_url("sk-stale-key")


def test_detect_opencode_base_url_survives_an_unreachable_zen(httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{OPENCODE_GO_BASE_URL}/chat/completions",
        status_code=401,
        json={"error": {"type": "server_error", "message": "Invalid credential"}},
    )
    httpx_mock.add_exception(httpx.ConnectError("network down"))
    assert detect_opencode_base_url("zen-only-key") == OPENCODE_ZEN_BASE_URL


def test_detect_opencode_base_url_unexpected_status(httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{OPENCODE_GO_BASE_URL}/chat/completions",
        status_code=500,
        text="boom",
    )
    with pytest.raises(RuntimeError, match="HTTP 500"):
        detect_opencode_base_url("key")


def test_detect_opencode_base_url_sends_a_session_id(httpx_mock) -> None:
    """The Go gateway rejects chat/completions without x-opencode-session."""
    httpx_mock.add_response(
        method="POST",
        url=f"{OPENCODE_GO_BASE_URL}/chat/completions",
        json={"choices": [{"message": {"content": "hi"}}]},
    )
    detect_opencode_base_url("go-key")

    sent = httpx_mock.get_requests()[0]
    session = sent.headers.get(OPENCODE_SESSION_HEADER)
    assert session and len(session) >= 32


def test_detect_opencode_base_url_go_when_probe_model_retired(httpx_mock) -> None:
    """A retired probe model is a model-level 400, not a rejected credential."""
    httpx_mock.add_response(
        method="POST",
        url=f"{OPENCODE_GO_BASE_URL}/chat/completions",
        status_code=400,
        json={
            "error": {
                "type": "server_error",
                "message": "Upstream request failed: Model is unavailable.",
            }
        },
    )
    assert detect_opencode_base_url("go-key") == OPENCODE_GO_BASE_URL


def test_list_opencode_models_hides_every_non_chat_model() -> None:
    rejects = [
        "claude-haiku-5-5",
        "gemini-4-argon-contributor",
        "gpt-5.6-luna",
        "gpt-6-luna",
        "grok-4.6",
        "grok-4.7",
        "minimax-m2.7",
        "muse-spark-1.2-contributor",
        "muse-spark-1.3-contributor",
    ]
    with patch(
        "indir.backends.model_catalog.list_openai_compatible_models",
        return_value=sorted([*rejects, "kimi-k3", "qwen3.6-plus"]),
    ):
        assert list_opencode_models(OPENCODE_GO_BASE_URL, "key") == [
            "kimi-k3",
            "qwen3.6-plus",
        ]


def test_is_opencode_gateway() -> None:
    assert is_opencode_gateway(OPENCODE_GO_BASE_URL)
    assert is_opencode_gateway(OPENCODE_ZEN_BASE_URL)
    assert not is_opencode_gateway("https://api.openai.com/v1")
    assert not is_opencode_gateway("https://opencode.ai.evil.test/v1")


def test_opencode_config_falls_back_to_the_go_env_var(monkeypatch) -> None:
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    config = OpenCodeConfig(api_key="")
    assert config.resolved_api_key() is None

    monkeypatch.setenv("OPENCODE_GO_API_KEY", "oc_sk_env")
    assert config.resolved_api_key() == "oc_sk_env"

    # An inline key still wins over the environment.
    assert OpenCodeConfig(api_key="inline").resolved_api_key() == "inline"
