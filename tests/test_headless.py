from __future__ import annotations

import io
from pathlib import Path

import pytest

from indir.backends.base import ChatMessage, ChatResponse, ToolCall
from indir.config import AppConfig
from indir.ui.headless import run_request


class FakeTTY(io.StringIO):
    """A stream that claims to be a terminal, so the indicator turns on."""

    def isatty(self) -> bool:
        return True


def _tool_call(command: str) -> dict:
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "run_command", "arguments": {"command": command}}}],
        }
    }


def _text(content: str) -> dict:
    return {"message": {"role": "assistant", "content": content}}


def _run(tmp_path: Path, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    kwargs.setdefault("stdin", io.StringIO())
    kwargs.setdefault("interactive", False)
    code = run_request(
        tmp_path,
        AppConfig(),
        kwargs.pop("request", "do the thing"),
        out=out,
        err=err,
        **kwargs,
    )
    return code, out.getvalue(), err.getvalue()


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_text_answer_goes_to_stdout(httpx_mock, tmp_path: Path) -> None:
    httpx_mock.add_response(
        method="POST", url="http://localhost:11434/api/chat", json=_text("Two files here.")
    )

    code, out, err = _run(tmp_path)

    assert code == 0
    assert "Two files here." in out
    assert err == ""


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_yes_runs_the_command_and_reports_it(httpx_mock, tmp_path: Path) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:11434/api/chat",
        json=_tool_call("echo hello > made.txt"),
    )

    code, out, err = _run(tmp_path, assume_yes=True)

    assert code == 0
    assert (tmp_path / "made.txt").read_text(encoding="utf-8").strip() == "hello"
    assert "$ echo hello > made.txt" in out
    assert not err


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_without_a_terminal_nothing_runs_without_yes(httpx_mock, tmp_path: Path) -> None:
    """Piped stdout (a script, cron, another tool) must not approve by default."""
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:11434/api/chat",
        json=_tool_call("echo hello > made.txt"),
    )

    code, out, err = _run(tmp_path)

    assert code == 1
    assert not (tmp_path / "made.txt").exists()
    assert "echo hello > made.txt" in err
    assert "--yes" in err


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_declining_skips_the_command(httpx_mock, tmp_path: Path) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:11434/api/chat",
        json=_tool_call("echo hello > made.txt"),
    )
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:11434/api/chat",
        json=_text("Left it alone."),
    )

    code, out, err = _run(tmp_path, ask=lambda _cmd: "n")

    assert code == 0
    assert not (tmp_path / "made.txt").exists()
    assert "command skipped" in err
    assert "Left it alone." in out


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_failing_command_exits_nonzero(httpx_mock, tmp_path: Path) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:11434/api/chat",
        json=_tool_call("exit 7"),
    )
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:11434/api/chat",
        json=_text("That failed."),
    )

    code, out, err = _run(tmp_path, assume_yes=True)

    assert code == 1
    assert "status 7" in err
    assert "That failed." in out


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_blocklisted_command_is_not_executed(httpx_mock, tmp_path: Path) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:11434/api/chat",
        json=_tool_call("rm -rf /"),
    )
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:11434/api/chat",
        json=_text("Refused."),
    )

    code, _out, err = _run(tmp_path, assume_yes=True)

    assert code == 1
    assert "blocked" in err.lower()


def test_missing_backend_is_explained(tmp_path: Path) -> None:
    config = AppConfig()
    config.backend.provider = "openai"  # no API key anywhere

    out, err = io.StringIO(), io.StringIO()
    code = run_request(
        tmp_path, config, "hello", out=out, err=err, stdin=io.StringIO(), interactive=False
    )

    assert code == 1
    assert "backend not ready" in err.getvalue()
    assert "settings" in err.getvalue()


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_prompt_reads_a_choice_from_stdin(httpx_mock, tmp_path: Path) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:11434/api/chat",
        json=_tool_call("echo hello > made.txt"),
    )

    out, err = io.StringIO(), io.StringIO()
    code = run_request(
        tmp_path,
        AppConfig(),
        "make the file",
        out=out,
        err=err,
        stdin=io.StringIO("y\n"),
        interactive=True,
    )

    assert code == 0
    assert (tmp_path / "made.txt").exists()
    assert "Run it? [y]es / [N]o / [a]lways:" in out.getvalue()


class SlowBackend:
    """Stands in for a model that takes a moment and wants a command run."""

    def __init__(self, command: str = "sleep 0.2; echo hi", delay: float = 0.25) -> None:
        self.command = command
        self.delay = delay
        self.turns = 0

    def chat(self, messages, tools=None):
        import time

        self.turns += 1
        time.sleep(self.delay)
        if self.turns == 1:
            return ChatResponse(
                message=ChatMessage(
                    role="assistant",
                    content="",
                    tool_calls=[ToolCall(id="call_1", name="run_command",
                                         arguments={"command": self.command})],
                )
            )
        return ChatResponse(message=ChatMessage(role="assistant", content="All done."))


def test_a_piped_run_gets_no_escapes(httpx_mock, tmp_path: Path) -> None:
    httpx_mock.add_response(
        method="POST", url="http://localhost:11434/api/chat", json=_text("plain text")
    )

    _code, out, err = _run(tmp_path)

    assert "\x1b" not in out + err
    assert out == "plain text\n"


def test_a_tty_run_shows_the_indicator_and_clears_it(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "indir.ui.headless.create_backend", lambda config, directory: SlowBackend()
    )
    out, err = FakeTTY(), FakeTTY()

    code = run_request(
        tmp_path,
        AppConfig(),
        "clean up",
        out=out,
        err=err,
        stdin=FakeTTY(),
        interactive=True,
        ask=lambda _cmd: "y",
    )

    assert code == 0
    spinner_text = err.getvalue()
    assert "thinking" in spinner_text
    assert "running sleep 0.2" in spinner_text  # label follows the real phase
    assert spinner_text.endswith("\r\x1b[2K")  # erased, nothing left behind
    assert "Done." in out.getvalue()  # a clean command ends the turn early
