"""Approval policy: ask about what matters, not about everything."""

from __future__ import annotations

from pathlib import Path

from indir.agent.loop import AgentSession, EventType
from indir.agent.policy import ApprovalPolicy, Risk
from indir.backends.base import ChatBackend, ChatMessage, ChatResponse, ToolCall
from indir.config import AppConfig


class ScriptedBackend(ChatBackend):
    def __init__(self, script: list[ChatMessage] | None = None) -> None:
        self.script = script or [ChatMessage(role="assistant", content="Done.")]
        self.calls = 0

    def chat(self, messages, tools=None):
        index = min(self.calls, len(self.script) - 1)
        self.calls += 1
        return ChatResponse(message=self.script[index])


def _command(call_id: str, command: str) -> ChatMessage:
    return ChatMessage(
        role="assistant",
        content="",
        tool_calls=[
            ToolCall(id=call_id, name="run_command", arguments={"command": command})
        ],
    )


def _decide(command: str, directory: Path, policy: ApprovalPolicy | None = None):
    return (policy or ApprovalPolicy()).decide(command, directory)


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def test_inspection_never_asks(tmp_path: Path) -> None:
    for command in (
        "ls -la",
        'file "clip.mp4"',
        "ffprobe -hide_banner clip.mp4",
        "command -v ffmpeg",
        "grep -n foo notes.txt",
        "wc -l *.txt",
    ):
        decision = _decide(command, tmp_path)
        assert decision.risk is Risk.READ_ONLY, command
        assert decision.runs_without_asking


def test_a_conversion_asks_once_and_can_be_remembered(tmp_path: Path) -> None:
    decision = _decide(
        'command -v ffmpeg && ffmpeg -y -i "clip.mp4" -c:a libopus "clip.webm"', tmp_path
    )
    assert decision.risk is Risk.CONTAINED
    assert decision.can_remember
    assert decision.kind.startswith("ffmpeg:")


def test_a_grant_does_not_cover_a_different_kind(tmp_path: Path) -> None:
    policy = ApprovalPolicy()
    policy.grant(_decide('ffmpeg -i a.mp4 a.webm', tmp_path).kind)

    assert policy.granted(_decide('ffmpeg -i b.mp4 b.webm', tmp_path).kind)
    assert not policy.granted(_decide("rm -- a.webm", tmp_path).kind)
    assert not _decide("rm -- a.webm", tmp_path).can_remember


def test_deletions_always_ask(tmp_path: Path) -> None:
    for command in ("rm -- old.mp4", "rm -rf build/", "shred -u secret.txt"):
        decision = _decide(command, tmp_path)
        assert decision.risk is Risk.GUARDED, command
        assert not decision.can_remember


def test_writing_outside_the_folder_always_asks(tmp_path: Path) -> None:
    decision = _decide(f'cp "notes.txt" "/etc/notes.txt"', tmp_path)
    assert decision.risk is Risk.GUARDED
    assert "outside" in decision.reason


def test_replacing_an_existing_file_always_asks(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("important")
    decision = _decide("mv -- a.txt b.txt", tmp_path)
    assert decision.risk is Risk.GUARDED
    assert "replace" in decision.reason


def test_a_fresh_write_is_contained(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x")
    decision = _decide("mv -- a.txt b.txt", tmp_path)
    assert decision.risk is Risk.CONTAINED


def test_shells_interpreters_and_downloads_always_ask(tmp_path: Path) -> None:
    for command in (
        "python3 -c 'print(1)'",
        "bash -c 'rm -rf build'",
        "curl -O https://example.com/x.mp4",
        "git status",
        'ls "$(pwd)"',
    ):
        assert _decide(command, tmp_path).risk in (Risk.GUARDED, Risk.GUARDED), command


def test_find_only_asks_when_it_deletes(tmp_path: Path) -> None:
    assert _decide("find . -name '*.mp4'", tmp_path).risk is Risk.READ_ONLY
    assert _decide("find . -name '*.tmp' -delete", tmp_path).risk is Risk.GUARDED


def test_the_blocklist_still_wins(tmp_path: Path) -> None:
    policy = ApprovalPolicy(blocklist=["rm -rf /"])
    decision = policy.decide("rm -rf /", tmp_path)
    assert decision.risk is Risk.BLOCKED
    # Refused by the runner: no prompt, and no way to approve it.
    assert decision.runs_without_asking
    assert not decision.can_remember


def test_overwriting_a_conversion_output_is_flagged_not_blocked(tmp_path: Path) -> None:
    (tmp_path / "clip.webm").write_text("old")
    decision = _decide('ffmpeg -y -i "clip.mp4" "clip.webm"', tmp_path)
    assert decision.risk is Risk.CONTAINED
    assert any("overwrites" in warning for warning in decision.warnings)


# ---------------------------------------------------------------------------
# In the session
# ---------------------------------------------------------------------------


def _session(tmp_path: Path, script: list[ChatMessage]) -> AgentSession:
    config = AppConfig()
    config.execution.mode = "smart"
    return AgentSession(tmp_path, config, ScriptedBackend(script))


def test_a_read_only_command_runs_without_a_pending_prompt(tmp_path: Path) -> None:
    session = _session(tmp_path, [_command("c1", "ls -la")])
    (tmp_path / "a.txt").write_text("x")

    events = session.send_user_message("what is in here")

    assert not session.pending_commands
    results = [e for e in events if e.type == EventType.COMMAND_RESULT]
    assert results and results[0].note and "read-only" in results[0].note


def test_a_conversion_waits_for_approval(tmp_path: Path) -> None:
    session = _session(
        tmp_path, [_command("c1", 'cp -- "in.mp4" "out.webm"')]
    )
    (tmp_path / "in.mp4").write_text("source")

    events = session.send_user_message("convert in.mp4 to webm")

    assert [e for e in events if e.type == EventType.COMMAND_PENDING]
    assert len(session.pending_commands) == 1
    pending = session.pending_commands[0]
    assert pending.grantable and pending.kind
    assert not (tmp_path / "out.webm").exists()


def test_always_remembers_the_kind_for_the_chat(tmp_path: Path) -> None:
    session = _session(
        tmp_path,
        [_command("c1", 'cp -- "in.mp4" "out.webm"'), _command("c2", "cp -- in.mp4 two.webm")],
    )
    (tmp_path / "in.mp4").write_text("source")

    events = session.send_user_message("convert in.mp4 to webm")
    pending = session.pending_commands[0]
    session.approve_command(pending.tool_call_id, always=True)
    assert (tmp_path / "out.webm").exists()

    # The same kind of write no longer asks.
    session.send_user_message("also make two.webm")
    assert (tmp_path / "two.webm").exists()
    assert not session.pending_commands


def test_always_is_not_offered_for_a_deletion(tmp_path: Path) -> None:
    session = _session(tmp_path, [_command("c1", "rm -- old.mp4")])
    (tmp_path / "old.mp4").write_text("video")

    session.send_user_message("delete old.mp4")

    pending = session.pending_commands[0]
    assert not pending.grantable
    assert not pending.kind
