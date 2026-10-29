from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from indir.backends.model_catalog import (
    OPENCODE_GO_BASE_URL,
    _OPENCODE_PROBE_MODEL,
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


def _zen_accepts_key(httpx_mock) -> None:
    """Zen answers 200 for a live key: the key is good, only the model retired."""
    httpx_mock.add_response(
        method="GET",
        url=f"{OPENCODE_ZEN_BASE_URL}/models",
        json={"data": [{"id": "kimi-k2.7-code"}]},
    )


def _go_probe(httpx_mock, status_code: int, payload=None, text: str | None = None) -> None:
    kwargs = {"text": text} if text is not None else {"json": payload}
    httpx_mock.add_response(
        method="POST",
        url=f"{OPENCODE_GO_BASE_URL}/chat/completions",
        status_code=status_code,
        **kwargs,
    )


def test_detect_opencode_base_url_go_when_probe_model_retired(httpx_mock) -> None:
    """A retired probe model is a model-level 400, not a rejected credential."""
    _go_probe(
        httpx_mock,
        400,
        {
            "error": {
                "type": "server_error",
                "message": "Upstream request failed: Model is unavailable.",
            }
        },
    )
    _zen_accepts_key(httpx_mock)
    assert detect_opencode_base_url("go-key") == OPENCODE_GO_BASE_URL


def test_detect_opencode_base_url_go_when_probe_model_deprecated(httpx_mock) -> None:
    """The live Go gateway answers 410 ModelDeprecated for a retired probe model."""
    _go_probe(
        httpx_mock,
        410,
        {
            "type": "error",
            "error": {
                "type": "ModelDeprecated",
                "message": f"Model {_OPENCODE_PROBE_MODEL} has been deprecated. "
                "Use kimi-k3 instead.",
            },
        },
    )
    _zen_accepts_key(httpx_mock)
    assert detect_opencode_base_url("go-key") == OPENCODE_GO_BASE_URL


def test_detect_treats_a_bodyless_410_as_go(httpx_mock) -> None:
    """410 Gone is only served once auth passed, even with the error text trimmed."""
    _go_probe(httpx_mock, 410, text="")
    _zen_accepts_key(httpx_mock)
    assert detect_opencode_base_url("go-key") == OPENCODE_GO_BASE_URL


def test_detect_recognises_a_code_shaped_model_error(httpx_mock) -> None:
    """model_not_found carries no prose, so the code itself has to be understood."""
    _go_probe(httpx_mock, 400, {"error": {"type": "model_not_found"}})
    _zen_accepts_key(httpx_mock)
    assert detect_opencode_base_url("go-key") == OPENCODE_GO_BASE_URL


def test_detect_does_not_forgive_a_generic_bad_request(httpx_mock) -> None:
    """A 400 about the request itself still needs a recognizable model marker."""
    _go_probe(
        httpx_mock,
        400,
        {"error": {"type": "BadRequest", "message": "messages must not be empty"}},
    )
    with pytest.raises(RuntimeError, match="gateway detection failed"):
        detect_opencode_base_url("go-key")


def test_detect_is_not_fooled_by_an_echoed_request_body(httpx_mock) -> None:
    """An error body that echoes request.model is not a model-level rejection."""
    _go_probe(
        httpx_mock,
        400,
        {
            "error": {"type": "BadRequest", "message": "unknown parameter: max_tokens"},
            "request": {"model": _OPENCODE_PROBE_MODEL, "max_tokens": 1},
        },
    )
    with pytest.raises(RuntimeError, match="gateway detection failed"):
        detect_opencode_base_url("go-key")


def test_detect_model_level_error_with_a_dead_key_says_so(httpx_mock) -> None:
    """Model-level errors must still not bless a key Zen rejects."""
    _go_probe(httpx_mock, 410, {"error": {"type": "ModelDeprecated"}})
    httpx_mock.add_response(
        method="GET",
        url=f"{OPENCODE_ZEN_BASE_URL}/models",
        status_code=401,
        json={"error": {"type": "AuthError", "message": "Invalid API key."}},
    )
    with pytest.raises(ValueError, match="both the Go and Zen gateways"):
        detect_opencode_base_url("dead-key")


def test_detect_probe_uses_the_configured_model(httpx_mock) -> None:
    """A typo in the probe constant would otherwise never fail the suite."""
    _go_probe(httpx_mock, 200, {"choices": [{"message": {"content": "hi"}}]})
    detect_opencode_base_url("go-key")

    payload = json.loads(httpx_mock.get_requests()[0].content)
    assert payload["model"] == _OPENCODE_PROBE_MODEL
    assert payload["messages"]


def test_detect_redacts_a_key_echoed_in_the_error_body(httpx_mock) -> None:
    # Built at runtime so the fixture never looks like a live key to secret scanners.
    fake_key = "oc_sk" + "_fake_key_for_test"
    _go_probe(
        httpx_mock,
        502,
        text='{"error": {"message": "upstream rejected Authorization: Bearer ' + fake_key + '"}}',
    )
    with pytest.raises(RuntimeError) as excinfo:
        detect_opencode_base_url(fake_key)
    assert fake_key not in str(excinfo.value)
    assert "[redacted]" in str(excinfo.value)


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
