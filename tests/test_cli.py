from __future__ import annotations

import pytest

from indir import cli


def test_doctor_flag_runs_without_a_path(monkeypatch) -> None:
    """README documents `indir --doctor`; it must not demand a path first."""
    calls: list[int] = []
    monkeypatch.setattr(cli, "_run_doctor", lambda: calls.append(1) or 0)

    assert cli.main(["--doctor"]) == 0
    assert calls == [1]


def test_doctor_subcommand_runs(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(cli, "_run_doctor", lambda: calls.append(1) or 0)

    assert cli.main(["doctor"]) == 0
    assert calls == [1]


def test_missing_path_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])
    assert excinfo.value.code == 2


def test_multiple_paths_are_rejected(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "resolve_launch_target", lambda _p: pytest.fail("should not resolve"))
    assert cli.main(["/tmp/a", "/tmp/b"]) == 1
    assert "Select a single file or folder" in capsys.readouterr().err


def test_doctor_rejects_an_extra_path(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_run_doctor", lambda: pytest.fail("doctor must not run"))
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--doctor", "/tmp"])
    assert excinfo.value.code == 2


def test_no_path_message_is_the_explicit_one(capsys) -> None:
    """argparse must not say 'the following arguments are required: paths'."""
    with pytest.raises(SystemExit):
        cli.main([])
    err = capsys.readouterr().err
    assert "a file or directory path is required" in err
    assert "the following arguments are required" not in err


def test_tui_paths_and_file_uri(monkeypatch, tmp_path) -> None:
    """`indir <path>`, `indir --tui <path>` (either order) and file:// URIs."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        "indir.ui.tui.run_tui",
        lambda directory, config, focus_file=None: calls.append((directory, focus_file)) or 0,
    )
    config = tmp_path / "config.toml"
    folder = tmp_path / "work"
    folder.mkdir()
    single = folder / "clip.webm"
    single.write_text("x", encoding="utf-8")

    assert cli.main(["--tui", str(folder), "--config", str(config)]) == 0
    assert cli.main([str(single), "--tui", "--config", str(config)]) == 0
    assert cli.main([f"file://{folder}", "--tui", "--config", str(config)]) == 0

    assert calls[0] == (folder, None)
    assert calls[1] == (folder, single)
    assert calls[2] == (folder, None)


def test_missing_path_reports_an_error(capsys, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "indir.ui.tui.run_tui", lambda *a, **k: pytest.fail("must not launch a UI")
    )
    missing = tmp_path / "nope"
    assert cli.main([str(missing), "--tui", "--config", str(tmp_path / "c.toml")]) == 1
    assert "does not exist" in capsys.readouterr().err
