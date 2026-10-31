"""The enforcement protocol: a turn is only done when the filesystem says so."""

from __future__ import annotations

from pathlib import Path

from indir.agent.loop import AgentSession, EventType
from indir.agent.verify import (
    analyze_command,
    classify_request,
    extract_claims,
    snapshot_directory,
    diff_snapshots,
)
from indir.backends.base import ChatBackend, ChatMessage, ChatResponse, ToolCall
from indir.config import AppConfig


def _calls(session: AgentSession) -> int:
    from typing import cast

    return cast(ScriptedBackend, session.backend).calls


class ScriptedBackend(ChatBackend):
    """Replays a fixed list of assistant messages, one per model call."""

    def __init__(self, script: list[ChatMessage]) -> None:
        self.script = script
        self.calls = 0
        self.seen: list[list[ChatMessage]] = []

    def chat(self, messages, tools=None):
        self.seen.append(list(messages))
        index = min(self.calls, len(self.script) - 1)
        self.calls += 1
        return ChatResponse(message=self.script[index])


def _command(call_id: str, command: str) -> ChatMessage:
    return ChatMessage(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id=call_id, name="run_command", arguments={"command": command})],
    )


def _text(content: str) -> ChatMessage:
    return ChatMessage(role="assistant", content=content)


def _session(tmp_path: Path, script: list[ChatMessage], mode: str = "auto") -> AgentSession:
    config = AppConfig()
    config.execution.mode = mode
    return AgentSession(tmp_path, config, ScriptedBackend(script))


def _verifications(events):
    return [event for event in events if event.type == EventType.VERIFICATION]


# ---------------------------------------------------------------------------
# The core failure: a command that succeeds and does nothing
# ---------------------------------------------------------------------------


def test_exit_zero_without_a_change_is_not_done(tmp_path: Path) -> None:
    session = _session(
        tmp_path,
        [
            _command("c1", "for f in *.jpeg; do mv -- \"$f\" \"${f%.jpeg}.jpg\"; done"),
            _text("Done."),
        ],
    )
    (tmp_path / "notes.txt").write_text("unrelated")

    events = session.send_user_message("rename the jpegs to jpg")

    checks = _verifications(events)
    assert checks and checks[0].ok is False
    assert "nothing in" in checks[0].content
    # The model's own "Done." is retried, never shown as the answer.
    assert not any(e.content == "Done." for e in events)
    correction = [m for m in session.messages if m.role == "user"][-1].content
    assert "NOT done" in (correction or "")


def test_a_verified_command_finishes_without_a_correction(tmp_path: Path) -> None:
    session = _session(
        tmp_path,
        [_command("c1", "printf 'video' > out.webm"), _text("Converted it.")],
    )
    (tmp_path / "in.mp4").write_text("source")

    events = session.send_user_message("convert in.mp4 to webm")

    checks = _verifications(events)
    assert checks and checks[0].ok is True
    assert any(e.content == "Done." for e in events)
    assert _calls(session) == 1  # no retry needed


# ---------------------------------------------------------------------------
# The other failure: claiming work that was never attempted
# ---------------------------------------------------------------------------


def test_a_reply_that_claims_a_file_is_not_trusted(tmp_path: Path) -> None:
    session = _session(
        tmp_path,
        [_text("I converted clip.mp4 to clip.webm.")],
        mode="confirm",
    )
    (tmp_path / "clip.mp4").write_text("source")

    events = session.send_user_message("convert clip.mp4 to webm")

    checks = _verifications(events)
    assert checks and checks[0].ok is False
    assert "clip.webm" in checks[0].content
    # Never claimed as done, even after the retries are spent.
    assert not any(e.content == "Done." for e in events)
    assert session.messages[-1].role == "assistant"
    assert "Not done" in (session.messages[-1].content or "")
    assert _calls(session) == 3  # first try + two corrections


def test_the_retry_budget_is_bounded(tmp_path: Path) -> None:
    session = _session(tmp_path, [_text("All finished!")], mode="confirm")

    session.send_user_message("delete the old files")

    assert _calls(session) == 3
    assert all(m.content != "Done." for m in session.messages)


def test_an_unverified_claim_never_reaches_the_transcript(tmp_path: Path) -> None:
    session = _session(tmp_path, [_text("Converted it — saved clip.webm.")], mode="confirm")
    (tmp_path / "clip.mp4").write_text("source")

    events = session.send_user_message("convert clip.mp4 to webm")

    assert not [e for e in events if e.type == EventType.ASSISTANT_TEXT]
    assert [e for e in events if e.type == EventType.VERIFICATION][0].ok is False


def test_an_explanation_is_still_shown_when_the_work_cannot_be_done(
    tmp_path: Path,
) -> None:
    session = _session(
        tmp_path, [_text("ffmpeg is not installed, so nothing was converted.")]
    )
    (tmp_path / "clip.mp4").write_text("source")

    events = session.send_user_message("convert clip.mp4 to webm")

    texts = [e.content for e in events if e.type == EventType.ASSISTANT_TEXT]
    assert any("not installed" in text for text in texts)
    assert [e for e in events if e.type == EventType.VERIFICATION][0].ok is False


def test_a_retry_that_does_the_work_closes_the_turn(tmp_path: Path) -> None:
    session = _session(
        tmp_path,
        [
            _text("Renamed a.jpeg to a.jpg."),
            _command("c1", "mv -- a.jpeg a.jpg"),
            _text("Renamed a.jpeg to a.jpg."),
        ],
    )
    (tmp_path / "a.jpeg").write_text("image")

    events = session.send_user_message("rename a.jpeg to a.jpg")

    checks = _verifications(events)
    assert checks[-1].ok is True
    assert (tmp_path / "a.jpg").exists()
    # The retry ran the command, which closed the turn without a third call.
    assert _calls(session) == 2
    assert any(e.content == "Done." for e in events)


# ---------------------------------------------------------------------------
# Deletion and empty output
# ---------------------------------------------------------------------------


def test_a_deletion_claim_must_be_true(tmp_path: Path) -> None:
    session = _session(tmp_path, [_text("Removed old.mp4.")], mode="confirm")
    (tmp_path / "old.mp4").write_text("video")

    events = session.send_user_message("delete old.mp4")

    checks = _verifications(events)
    assert checks[0].ok is False
    assert "still there" in checks[0].content


def test_a_real_deletion_verifies(tmp_path: Path) -> None:
    session = _session(
        tmp_path, [_command("c1", "rm -- old.mp4"), _text("Removed old.mp4.")]
    )
    (tmp_path / "old.mp4").write_text("video")

    events = session.send_user_message("delete old.mp4")

    checks = _verifications(events)
    assert checks and checks[0].ok is True
    assert "deleted old.mp4" in checks[0].content


def test_a_deletion_outside_the_folder_is_verified_by_its_operand(
    tmp_path: Path,
) -> None:
    """A path the user asked to remove may live outside the chat's folder."""
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("temp")

    config = AppConfig()
    config.execution.mode = "auto"
    backend = ScriptedBackend(
        [_command("c1", f'rm -- "{outside}"'), _text("Removed outside.txt.")]
    )
    session = AgentSession(work, config, backend)

    events = session.send_user_message(f"delete {outside}")

    assert [e for e in events if e.type == EventType.VERIFICATION][0].ok is True
    assert not outside.exists()


def test_a_failed_deletion_proves_nothing(tmp_path: Path) -> None:
    session = _session(tmp_path, [_command("c1", "rm -- missing.txt"), _text("Deleted missing.txt.")])

    events = session.send_user_message("delete missing.txt")

    checks = _verifications(events)
    assert checks and checks[0].ok is False
    assert "failed" in checks[0].content


def test_an_empty_output_file_is_a_failure(tmp_path: Path) -> None:
    session = _session(
        tmp_path,
        [_command("c1", "touch clip.webm"), _text("Converted clip.mp4 to clip.webm.")],
    )
    (tmp_path / "clip.mp4").write_text("source")

    events = session.send_user_message("convert clip.mp4 to webm")

    checks = _verifications(events)
    assert checks[0].ok is False
    assert "empty" in checks[0].content


# ---------------------------------------------------------------------------
# Information requests stay quiet
# ---------------------------------------------------------------------------


def test_a_question_needs_no_filesystem_change(tmp_path: Path) -> None:
    session = _session(tmp_path, [_text("There are two files here.")])
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "b.txt").write_text("")

    events = session.send_user_message("what files are in here")

    assert not _verifications(events)
    assert any(e.type == EventType.ASSISTANT_TEXT for e in events)


def test_verification_can_be_switched_off(tmp_path: Path) -> None:
    config = AppConfig()
    config.execution.mode = "confirm"
    config.execution.verify = False
    backend = ScriptedBackend([_text("I converted clip.mp4 to clip.webm.")])
    session = AgentSession(tmp_path, config, backend)
    (tmp_path / "clip.mp4").write_text("source")

    events = session.send_user_message("convert clip.mp4 to webm")

    assert not _verifications(events)
    assert backend.calls == 1


# ---------------------------------------------------------------------------
# The building blocks
# ---------------------------------------------------------------------------


def test_effects_report_what_really_happened(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("a")
    before = snapshot_directory(tmp_path)

    (tmp_path / "keep.txt").write_text("much longer content")
    (tmp_path / "new.txt").write_text("b")
    (tmp_path / "gone.txt").write_text("c")
    (tmp_path / "gone.txt").unlink()

    effects = diff_snapshots(before, snapshot_directory(tmp_path))
    assert "new.txt" in effects.created
    assert "keep.txt" in effects.modified
    assert effects.changed


def test_snapshot_skips_git_and_numbers_nothing(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref")
    (tmp_path / "file.txt").write_text("x")

    snapshot = snapshot_directory(tmp_path)
    assert "file.txt" in snapshot
    assert not any(key.startswith(".git") for key in snapshot)


def test_declared_outputs_come_from_the_command() -> None:
    assert analyze_command(
        'ffmpeg -y -i "in.mp4" -c:v libx264 -c:a aac "out.webm"'
    ).outputs == ("out.webm",)
    assert analyze_command("mv -- a.txt b.txt").outputs == ("b.txt",)
    assert analyze_command("mkdir -p a/b").outputs == ("a/b",)
    # Extraction reads its archive; the new files show up in the diff.
    assert analyze_command("tar -xf a.tar -C out").outputs == ("out",)
    assert analyze_command("ls -la").mutating is False


def test_claims_are_read_from_the_reply(tmp_path: Path) -> None:
    claims = extract_claims(
        "Converted `in.mp4` to `out.webm`. Also removed old.mkv.",
        tmp_path,
        before={"in.mp4"},
    )
    kinds = {(claim.kind, claim.raw) for claim in claims}
    assert ("created", "out.webm") in kinds
    assert ("deleted", "old.mkv") in kinds


def test_claims_ignore_questions_and_hypotheticals(tmp_path: Path) -> None:
    assert extract_claims("Should I create `out.webm`?", tmp_path, before=set()) == []
    assert extract_claims("If you want, I can create `out.webm`.", tmp_path, before=set()) == []


def test_headless_exits_nonzero_when_the_work_is_not_verified(
    monkeypatch, tmp_path: Path
) -> None:
    from io import StringIO

    from indir.ui.headless import run_request

    backend = ScriptedBackend([_text("Converted clip.mp4 to clip.webm.")])
    monkeypatch.setattr(
        "indir.ui.headless.create_backend", lambda config, directory: backend
    )
    (tmp_path / "clip.mp4").write_text("source")
    config = AppConfig()
    out, err = StringIO(), StringIO()

    code = run_request(
        tmp_path,
        config,
        "convert clip.mp4 to webm",
        out=out,
        err=err,
        stdin=StringIO(),
        interactive=False,
        ask=lambda _command: "y",
    )

    assert code == 1
    assert "Not verified" in err.getvalue()
    # The false claim is stated once, not once per retry.
    assert (out.getvalue() + err.getvalue()).count("was created, but") == 1
    assert not (tmp_path / "clip.webm").exists()


def test_headless_exits_zero_when_the_work_is_verified(
    monkeypatch, tmp_path: Path
) -> None:
    from io import StringIO

    from indir.ui.headless import run_request

    backend = ScriptedBackend(
        [_command("c1", 'cp -- "clip.mp4" "clip.webm"'), _text("Copied it.")]
    )
    monkeypatch.setattr(
        "indir.ui.headless.create_backend", lambda config, directory: backend
    )
    (tmp_path / "clip.mp4").write_text("source")
    out, err = StringIO(), StringIO()

    code = run_request(
        tmp_path,
        AppConfig(),
        "convert clip.mp4 to webm",
        out=out,
        err=err,
        stdin=StringIO(),
        interactive=False,
        ask=lambda _command: "y",
    )

    assert code == 0
    assert "Not done" not in out.getvalue() + err.getvalue()
    assert (tmp_path / "clip.webm").exists()


def test_each_turn_starts_from_a_fresh_baseline(tmp_path: Path) -> None:
    """Effects and claims belong to one turn; the next one starts clean."""
    session = _session(
        tmp_path,
        [
            _command("c1", 'cp -- "in.mp4" "out.webm"'),
            _text("Copied in.mp4 to out.webm."),
            _text("Deleted out.webm."),
        ],
    )
    (tmp_path / "in.mp4").write_text("source")

    first = session.send_user_message("convert in.mp4 to webm")
    assert [e for e in first if e.type == EventType.VERIFICATION][0].ok is True

    # The second turn claims a deletion it never did; turn one's effects must
    # not count as evidence for it.
    second = session.send_user_message("delete out.webm")
    checks = [e for e in second if e.type == EventType.VERIFICATION]
    assert checks and checks[0].ok is False
    assert "still there" in checks[0].content


def test_intent_separates_requests_from_questions() -> None:
    assert classify_request("convert the mp4 to webm").is_action
    assert classify_request("can you convert this").is_action
    assert not classify_request("how do I convert a video").is_action
    assert not classify_request("what files are here").is_action