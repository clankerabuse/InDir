from __future__ import annotations

from unittest.mock import patch

import pytest

from indir import cli


def test_doctor_flag_runs_without_paths() -> None:
    """`indir --doctor` is documented in the README; it must not need a path."""
    with patch.object(cli, "_run_doctor", return_value=0) as doctor:
        assert cli.main(["--doctor"]) == 0
    doctor.assert_called_once_with()


def test_doctor_subcommand_runs_without_paths() -> None:
    with patch.object(cli, "_run_doctor", return_value=0) as doctor:
        assert cli.main(["doctor"]) == 0
    doctor.assert_called_once_with()


def test_missing_path_is_an_error() -> None:
    with pytest.raises(SystemExit):
        cli.main([])


def test_multiple_paths_are_rejected() -> None:
    with patch.object(cli, "ensure_config_permissions"), patch.object(
        cli, "resolve_launch_target"
    ), patch.object(cli, "load_config"):
        assert cli.main(["/tmp/a", "/tmp/b"]) == 1
