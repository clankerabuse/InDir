from __future__ import annotations

import os
import shutil
import socket
import stat
import tempfile
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

import indir.ui.chat_window as chat_window

# ~/Downloads name that used to kill the launch: the old scheme built a
# 118-byte socket path, so `bind()` died with "AF_UNIX path too long" before
# the window was ever shown.
LONG_DOWNLOAD_NAME = "Arrested Development - Charlie Brown moments [oabcM9SOF-E].webm"


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _long_scope(tmp_path: Path) -> Path:
    return tmp_path / "Downloads" / LONG_DOWNLOAD_NAME


@pytest.fixture
def short_socket_dir(monkeypatch):
    """A short, hermetic socket dir.

    pytest's tmp_path is long enough on its own that no socket name fits under
    the 107-byte cap, so the bind test needs a deliberately short root.
    """
    root = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    if not (Path(root).is_dir() and os.access(root, os.W_OK)):
        pytest.skip(f"no short writable socket root ({root})")
    path = Path(tempfile.mkdtemp(prefix="indir-test-", dir=root))
    monkeypatch.setattr(chat_window, "_socket_dir", lambda: path)
    yield path
    shutil.rmtree(path, ignore_errors=True)


def test_default_socket_path_fits_the_af_unix_limit() -> None:
    """The real install must survive a real long Downloads path."""
    focus = Path.home() / "Downloads" / LONG_DOWNLOAD_NAME

    path = chat_window._scope_socket_path(focus.parent, focus)

    # sun_path is 108 bytes including the terminating NUL.
    assert len(os.fsencode(path)) <= 107
    assert path.name.startswith("scope-")


def test_scope_socket_path_is_stable_and_scope_specific(
    short_socket_dir, tmp_path
) -> None:
    folder = tmp_path / "Downloads"
    focus = _long_scope(tmp_path)
    sibling = folder / "other.webm"

    scope = chat_window._scope_socket_path
    assert scope(folder, focus) == scope(folder, focus)
    assert scope(folder, focus) != scope(folder, sibling)
    assert scope(folder, focus) != scope(folder, None)


def test_focus_server_opens_for_a_long_file_path(short_socket_dir, tmp_path) -> None:
    _app()
    focus = _long_scope(tmp_path)

    window = QWidget()
    chat_window._start_focus_server(window, focus.parent, focus)

    socket_path = chat_window._scope_socket_path(focus.parent, focus)
    assert socket_path.exists()
    # A second right-click on the same file focuses it instead of opening another.
    assert chat_window._try_focus_existing(focus.parent, focus) is True

    window.deleteLater()


def test_focus_socket_is_private(short_socket_dir, tmp_path) -> None:
    """Another local user must not be able to reach the socket."""
    _app()
    focus = _long_scope(tmp_path)

    window = QWidget()
    chat_window._start_focus_server(window, focus.parent, focus)

    socket_path = chat_window._scope_socket_path(focus.parent, focus)
    assert stat.S_IMODE(socket_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600

    window.deleteLater()


class _FocusRecorder(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.raised = 0
        self.activated = 0

    def raise_(self) -> None:  # noqa: N802 (Qt override)
        self.raised += 1

    def activateWindow(self) -> None:  # noqa: N802 (Qt override)
        self.activated += 1


def _pump(seconds: float = 1.0) -> None:
    app = _app()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)


def test_silent_peer_neither_focuses_nor_hangs(short_socket_dir, tmp_path) -> None:
    """An accepted socket is blocking; a peer that sends nothing must not hang us."""
    _app()
    focus = _long_scope(tmp_path)
    window = _FocusRecorder()
    chat_window._start_focus_server(window, focus.parent, focus)
    socket_path = chat_window._scope_socket_path(focus.parent, focus)

    rude = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    rude.connect(str(socket_path))  # connected, never sends, never closes
    _pump()
    assert window.raised == 0
    assert window.activated == 0

    polite = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    polite.connect(str(socket_path))
    polite.sendall(b"focus")
    polite.close()
    _pump()
    assert window.raised == 1

    rude.close()
    window.deleteLater()
