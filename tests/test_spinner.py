from __future__ import annotations

import io
import time

from indir.ui.spinner import Spinner, supports_spinner


class FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_frames_are_drawn_and_the_line_is_erased() -> None:
    stream = FakeTTY()
    spinner = Spinner(stream, interval=0.01)

    with spinner:
        time.sleep(0.08)

    output = stream.getvalue()
    assert "thinking" in output
    assert output.count("\r\x1b[2K") >= 2
    # Ephemeral: the last thing written wipes the line, leaving nothing behind.
    assert output.endswith("\r\x1b[2K")


def test_a_plain_stream_gets_no_escape_codes() -> None:
    stream = io.StringIO()
    spinner = Spinner.maybe(stream)

    assert spinner.enabled is False
    with spinner:
        time.sleep(0.02)

    assert stream.getvalue() == ""


def test_term_dumb_disables_it(monkeypatch) -> None:
    monkeypatch.setenv("TERM", "dumb")
    assert supports_spinner(FakeTTY()) is False


def test_env_kill_switch_disables_it(monkeypatch) -> None:
    monkeypatch.setenv("INDIR_NO_SPINNER", "1")
    assert supports_spinner(FakeTTY()) is False


def test_first_tty_stream_wins() -> None:
    plain, tty = io.StringIO(), FakeTTY()
    spam = Spinner.maybe(plain, tty)
    assert spam.enabled is True
    assert spam._stream is tty


def test_labels_are_collapsed_and_shortened() -> None:
    spinner = Spinner(FakeTTY())
    spinner.set_label("ffmpeg -y -i\n  clip.mp4   -c:v libvpx-vp9 -crf 32 -b:v 0 out.webm")

    label = spinner._label
    assert "\n" not in label
    assert len(label) <= 48
    assert label.endswith("…")


def test_elapsed_time_only_after_a_couple_of_seconds() -> None:
    spinner = Spinner(FakeTTY())
    spinner._started = time.monotonic()

    assert "s" not in spinner._line(0)  # a fast turn stays quiet

    spinner._started = time.monotonic() - 4
    assert "4s" in spinner._line(0)


def test_stop_is_safe_when_never_started() -> None:
    spinner = Spinner.maybe(FakeTTY())
    spinner.stop()  # must not raise or write frames


def test_can_restart_for_each_blocking_call() -> None:
    """A run wraps several waits in one spinner; each must draw again."""
    stream = FakeTTY()
    spinner = Spinner(stream, interval=0.01)

    for _ in range(2):
        with spinner:
            time.sleep(0.05)

    frames = stream.getvalue().count("thinking")
    assert frames >= 4  # both waits drew, not just the first
