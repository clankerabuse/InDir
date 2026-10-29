from __future__ import annotations

import os
import shutil
import threading
import time
from typing import TextIO

# Braille spinner: one cell wide, so the line never jumps sideways.
_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Seconds of silence before the elapsed clock appears: fast turns stay quiet.
_ELAPSED_AFTER = 2.0


def supports_spinner(stream: TextIO | None) -> bool:
    """A live indicator only makes sense on a real terminal."""
    if stream is None:
        return False
    if os.environ.get("INDIR_NO_SPINNER"):
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def _one_line(text: str, limit: int = 48) -> str:
    """Collapse to a single short line: the indicator must not wrap."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


class Spinner:
    """Ephemeral one-line status for a slow call, e.g. `⠹ thinking… 4s`.

    Draws on its own thread while the caller blocks, then erases itself, so the
    stream ends up exactly as it would have without the spinner. Disabled (and
    therefore silent) when no stream is a terminal.
    """

    def __init__(self, stream: TextIO | None, *, interval: float = 0.12) -> None:
        self._stream = stream
        self._interval = interval
        self._label = "thinking"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0

    @classmethod
    def maybe(cls, *streams: TextIO | None, interval: float = 0.12) -> "Spinner":
        """Use the first stream that is a terminal, or stay silent."""
        for stream in streams:
            if supports_spinner(stream):
                return cls(stream, interval=interval)
        return cls(None, interval=interval)

    @property
    def enabled(self) -> bool:
        return self._stream is not None

    def set_label(self, label: str) -> None:
        """Safe to call from the thread that owns the blocking work."""
        self._label = _one_line(label)

    def start(self) -> "Spinner":
        if not self.enabled or self._thread is not None:
            return self
        # Reusable: one indicator wraps several blocking calls in a run.
        self._stop.clear()
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if not self.enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._erase()

    def __enter__(self) -> "Spinner":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    def _run(self) -> None:
        index = 0
        while not self._stop.is_set():
            self._draw(self._line(index))
            index += 1
            self._stop.wait(self._interval)

    def _line(self, index: int) -> str:
        elapsed = time.monotonic() - self._started
        frame = _FRAMES[index % len(_FRAMES)]
        line = f"{frame} {self._label}…"
        if elapsed >= _ELAPSED_AFTER:
            line += f" {elapsed:3.0f}s"
        return line

    def _columns(self) -> int:
        try:
            return shutil.get_terminal_size((80, 24)).columns
        except OSError:
            return 80

    def _draw(self, line: str) -> None:
        stream = self._stream
        if stream is None:
            return
        # \r rewinds, \x1b[2K wipes the line: no residue if the label shrinks.
        stream.write("\r\x1b[2K" + line[: max(1, self._columns() - 1)])
        stream.flush()

    def _erase(self) -> None:
        stream = self._stream
        if stream is None:
            return
        stream.write("\r\x1b[2K")
        stream.flush()
