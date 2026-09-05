from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from indir.backends.model_catalog import (
    OPENCODE_GO_BASE_URL,
    OPENCODE_ZEN_BASE_URL,
    detect_opencode_base_url,
    list_cursor_models,
    list_models,
    list_opencode_models,
)


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
