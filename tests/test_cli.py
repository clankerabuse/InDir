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


def test_multiple_paths_are_rejected(monkeypatch, tmp_path, capsys) -> None:
    """Two real targets is a file-manager drop, not a request: refuse clearly."""
    monkeypatch.setattr(cli, "resolve_launch_target", lambda _p: pytest.fail("should not resolve"))
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    assert cli.main([str(first), str(second)]) == 1
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
    assert "a request or a path is required" in err
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

def _stub_headless(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []

    def fake_run_request(directory, config, request, **kwargs):
        calls.append((directory, request, kwargs))
        return 0

    monkeypatch.setattr("indir.ui.headless.run_request", fake_run_request)
    return calls


def test_bare_words_are_a_request_for_the_current_directory(
    monkeypatch, tmp_path, capsys
) -> None:
    """`indir convert the mp4s to webm` should not need a path or a flag."""
    calls = _stub_headless(monkeypatch)
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "c.toml"

    assert cli.main(["convert", "the", "mp4s", "to", "webm", "--config", str(config)]) == 0

    directory, request, kwargs = calls[0]
    assert directory.resolve() == tmp_path.resolve()
    assert request == "convert the mp4s to webm"
    assert kwargs["assume_yes"] is False


def test_a_quoted_request_stays_one_string(monkeypatch, tmp_path) -> None:
    calls = _stub_headless(monkeypatch)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["rename every .jpeg to .jpg", "--config", str(tmp_path / "c.toml")]) == 0

    assert calls[0][1] == "rename every .jpeg to .jpg"


def test_prompt_flag_targets_another_folder(monkeypatch, tmp_path) -> None:
    calls = _stub_headless(monkeypatch)
    folder = tmp_path / "work"
    folder.mkdir()

    code = cli.main(
        [
            "--prompt",
            "tidy this up",
            str(folder),
            "--yes",
            "--config",
            str(tmp_path / "c.toml"),
        ]
    )

    assert code == 0
    directory, request, kwargs = calls[0]
    assert directory == folder
    assert request == "tidy this up"
    assert kwargs["assume_yes"] is True


def test_prompt_flag_rejects_two_folders(monkeypatch, tmp_path, capsys) -> None:
    _stub_headless(monkeypatch)
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()

    assert cli.main(["-p", "x", str(first), str(second)]) == 1
    assert "single folder" in capsys.readouterr().err


def test_an_existing_path_still_opens_the_window(monkeypatch, tmp_path) -> None:
    """A path from the file manager must never be read as a request."""
    monkeypatch.setattr(
        "indir.ui.headless.run_request", lambda *a, **k: pytest.fail("headless must not run")
    )
    launched: list[Path] = []
    monkeypatch.setattr(
        "indir.ui.chat_window.run_qt_ui",
        lambda directory, config, focus_file=None: launched.append(directory) or 0,
    )
    folder = tmp_path / "work"
    folder.mkdir()

    assert cli.main([str(folder), "--config", str(tmp_path / "c.toml")]) == 0
    assert launched == [folder]


def test_tui_opens_with_the_request_prefilled(monkeypatch, tmp_path) -> None:
    seen: list[str | None] = []
    monkeypatch.setattr(
        "indir.ui.tui.run_tui",
        lambda directory, config, focus_file=None, initial_request=None: seen.append(
            initial_request
        )
        or 0,
    )

    assert cli.main(["--tui", "list", "videos", "--config", str(tmp_path / "c.toml")]) == 0
    assert seen == ["list videos"]
