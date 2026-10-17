from __future__ import annotations

import pytest

from indir.backends.base import ChatMessage
from indir.backends.model_catalog import OPENCODE_GO_BASE_URL, OPENCODE_SESSION_HEADER
from indir.backends.openai_compat import OpenAICompatBackend
from indir.config import OpenAIConfig

_REPLY = {"choices": [{"message": {"content": "hi"}}]}


def _opencode_config(**overrides) -> OpenAIConfig:
    values = {
        "api_key": "oc_sk_test",
        "base_url": OPENCODE_GO_BASE_URL,
        "model": "kimi-k3",
    }
    values.update(overrides)
    return OpenAIConfig(**values)


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_opencode_chat_sends_one_stable_session_id(httpx_mock) -> None:
    """Without x-opencode-session the Go gateway answers 400 MissingSessionID."""
    httpx_mock.add_response(json=_REPLY)
    httpx_mock.add_response(json=_REPLY)

    backend = OpenAICompatBackend(_opencode_config())
    backend.chat([ChatMessage(role="user", content="hi")])
    backend.chat([ChatMessage(role="user", content="again")])

    requests = httpx_mock.get_requests()
    sessions = {request.headers.get(OPENCODE_SESSION_HEADER) for request in requests}
    assert len(sessions) == 1
    session = sessions.pop()
    assert session and len(session) >= 32


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_non_opencode_providers_send_no_session_header(httpx_mock) -> None:
    httpx_mock.add_response(json=_REPLY)

    backend = OpenAICompatBackend(_opencode_config(base_url="https://api.openai.com/v1"))
    backend.chat([ChatMessage(role="user", content="hi")])

    assert OPENCODE_SESSION_HEADER not in httpx_mock.get_requests()[0].headers
