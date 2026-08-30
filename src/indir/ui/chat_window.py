from __future__ import annotations

import socket
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from indir.agent.loop import AgentEvent, AgentSession, EventType
from indir.backends.registry import create_backend
from indir.config import AppConfig


class SessionWorker(QThread):
    """Runs agent session work off the UI thread."""

    finished_events = Signal(list)
    error = Signal(str)

    def __init__(self, task: str, session: AgentSession, **kwargs) -> None:
        super().__init__()
        self.task = task
        self.session = session
        self.kwargs = kwargs

    def run(self) -> None:
        try:
            if self.task == "message":
                events = self.session.send_user_message(self.kwargs["text"])
            elif self.task == "approve":
                events = self.session.approve_command(
                    self.kwargs["tool_call_id"],
                    self.kwargs.get("command"),
                )
            elif self.task == "reject":
                events = self.session.reject_command(self.kwargs["tool_call_id"])
            else:
                raise ValueError(f"Unknown worker task: {self.task}")
            self.finished_events.emit(events)
        except Exception as exc:
            self.error.emit(str(exc))


class AgentWorker(SessionWorker):
    """Backward-compatible alias for sending a user message."""

    def __init__(self, session: AgentSession, message: str) -> None:
        super().__init__("message", session, text=message)


class CommandApprovalWidget(QWidget):
    approved = Signal(str, str)  # tool_call_id, command
    rejected = Signal(str)  # tool_call_id

    def __init__(self, tool_call_id: str, command: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tool_call_id = tool_call_id

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        label = QLabel("Proposed command:")
        label.setStyleSheet("color: #aaa;")
        layout.addWidget(label)

        self.command_edit = QTextEdit()
        self.command_edit.setPlainText(command)
        self.command_edit.setMaximumHeight(80)
        self.command_edit.setFont(QFont("monospace", 10))
        layout.addWidget(self.command_edit)

        btn_layout = QHBoxLayout()
        run_btn = QPushButton("Run")
        run_btn.clicked.connect(self._on_run)
        edit_run_btn = QPushButton("Edit && Run")
        edit_run_btn.clicked.connect(self._on_edit_run)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self._on_cancel)
        btn_layout.addWidget(run_btn)
        btn_layout.addWidget(edit_run_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        self.setStyleSheet(
            "CommandApprovalWidget { background: #2a2a1a; border: 1px solid #665500; border-radius: 4px; }"
        )

    def _on_run(self) -> None:
        self._set_enabled(False)
        self.approved.emit(self.tool_call_id, self.command_edit.toPlainText())

    def _on_edit_run(self) -> None:
        self._set_enabled(False)
        self.approved.emit(self.tool_call_id, self.command_edit.toPlainText())

    def _on_cancel(self) -> None:
        self._set_enabled(False)
        self.rejected.emit(self.tool_call_id)

    def _set_enabled(self, enabled: bool) -> None:
        for btn in self.findChildren(QPushButton):
            btn.setEnabled(enabled)
        self.command_edit.setReadOnly(not enabled)


class ChatWindow(QMainWindow):
    def __init__(self, directory: Path, config: AppConfig) -> None:
        super().__init__()
        self.directory = directory
        self.config = config
        self.backend = create_backend(config, directory)
        self.session = AgentSession(directory, config, self.backend)
        self.worker: SessionWorker | None = None

        self.setWindowTitle(f"InDir — {directory.name}")
        self.resize(480, 600)
        self._apply_dark_theme()
        self._build_ui()

    def _apply_dark_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #1e1e1e; color: #d4d4d4; }
            QTextEdit {
                background: #252526; color: #d4d4d4;
                border: 1px solid #3c3c3c; border-radius: 4px;
                padding: 6px;
            }
            QPushButton {
                background: #0e639c; color: white; border: none;
                padding: 6px 14px; border-radius: 4px;
            }
            QPushButton:hover { background: #1177bb; }
            QPushButton:disabled { background: #3c3c3c; color: #888; }
            QLabel#header { color: #888; font-size: 11px; }
            """
        )

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QLabel(str(self.directory))
        header.setObjectName("header")
        header.setWordWrap(True)
        layout.addWidget(header)

        self.chat_area = QVBoxLayout()
        self.chat_container = QWidget()
        self.chat_container.setLayout(self.chat_area)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.chat_container)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        layout.addWidget(scroll, stretch=1)

        self.input_box = QTextEdit()
        self.input_box.setPlaceholderText("Ask anything about this directory… (Enter to send, Shift+Enter for newline)")
        self.input_box.setMaximumHeight(100)
        self.input_box.setFont(QFont("monospace", 10))
        layout.addWidget(self.input_box)

        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self._on_send)
        layout.addWidget(send_btn)

        self.send_btn = send_btn
        self.status_label = QLabel("")
        self.status_label.setObjectName("header")
        layout.addWidget(self.status_label)
        self._add_system_message(
            f"Ready. Working directory:\n{self.directory}\n\n"
            "Try: \"convert webm to mp4\" or \"list all video files\""
        )

    def keyPressEvent(self, event) -> None:
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            and self.input_box.hasFocus()
        ):
            self._on_send()
            event.accept()
            return
        super().keyPressEvent(event)

    def _add_bubble(self, text: str, role: str) -> None:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setFont(QFont("monospace", 10))
        if role == "user":
            label.setStyleSheet(
                "background: #264f78; padding: 8px; border-radius: 6px; margin: 4px 40px 4px 0;"
            )
        elif role == "assistant":
            label.setStyleSheet(
                "background: #2d2d30; padding: 8px; border-radius: 6px; margin: 4px 0 4px 40px;"
            )
        elif role == "system":
            label.setStyleSheet(
                "background: #1a1a1a; padding: 8px; border-radius: 6px; margin: 4px 0; color: #888;"
            )
        elif role == "error":
            label.setStyleSheet(
                "background: #4a1a1a; padding: 8px; border-radius: 6px; margin: 4px 0; color: #ff8888;"
            )
        else:
            label.setStyleSheet(
                "background: #1a2a1a; padding: 8px; border-radius: 6px; margin: 4px 0; color: #aaffaa;"
            )
        self.chat_area.addWidget(label)

    def _add_system_message(self, text: str) -> None:
        self._add_bubble(text, "system")

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.send_btn.setEnabled(not busy)
        self.input_box.setEnabled(not busy)
        self.status_label.setText(message)

    def _worker_running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def _start_worker(self, task: str, busy_message: str, **kwargs) -> None:
        if self._worker_running():
            return
        self._set_busy(True, busy_message)
        self.worker = SessionWorker(task, self.session, **kwargs)
        self.worker.finished_events.connect(self._on_worker_events)
        self.worker.error.connect(self._on_worker_error)
        self.worker.finished.connect(lambda: self._set_busy(False, ""))
        self.worker.start()

    def _on_send(self) -> None:
        text = self.input_box.toPlainText().strip()
        if not text or self._worker_running():
            return
        self.input_box.clear()
        self._add_bubble(text, "user")
        self._start_worker("message", "Thinking…", text=text)

    def _on_worker_error(self, msg: str) -> None:
        self._add_bubble(msg, "error")

    def _on_worker_events(self, events: list) -> None:
        for event in events:
            self._handle_event(event)

    def _on_error(self, msg: str) -> None:
        self._on_worker_error(msg)

    def _on_events(self, events: list) -> None:
        self._on_worker_events(events)

    def _handle_event(self, event: AgentEvent) -> None:
        if event.type == EventType.ASSISTANT_TEXT:
            self._add_bubble(event.content, "assistant")
        elif event.type == EventType.TOOL_RESULT:
            self._add_bubble(event.content, "tool")
        elif event.type == EventType.COMMAND_PENDING:
            widget = CommandApprovalWidget(event.tool_call_id or "", event.command or "")
            widget.approved.connect(self._on_command_approved)
            widget.rejected.connect(self._on_command_rejected)
            self.chat_area.addWidget(widget)
        elif event.type == EventType.COMMAND_RESULT:
            self._add_bubble(event.content, "tool")
        elif event.type == EventType.ERROR:
            self._add_bubble(event.content, "error")

    def _on_command_approved(self, tool_call_id: str, command: str) -> None:
        self._add_bubble(f"Running: {command}", "system")
        self._start_worker(
            "approve",
            f"Running command…",
            tool_call_id=tool_call_id,
            command=command,
        )

    def _on_command_rejected(self, tool_call_id: str) -> None:
        self._start_worker("reject", "Thinking…", tool_call_id=tool_call_id)


def _try_focus_existing(directory: Path) -> bool:
    """Try to signal an existing window for this directory via a local socket."""
    lock_dir = Path.home() / ".local" / "state" / "indir" / "sockets"
    lock_dir.mkdir(parents=True, exist_ok=True)
    safe_name = str(directory).replace("/", "_")
    socket_path = lock_dir / f"{safe_name}.sock"

    if not socket_path.exists():
        return False

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(str(socket_path))
        sock.sendall(b"focus")
        sock.close()
        return True
    except OSError:
        socket_path.unlink(missing_ok=True)
        return False


def _start_focus_server(window: ChatWindow, directory: Path) -> None:
    lock_dir = Path.home() / ".local" / "state" / "indir" / "sockets"
    lock_dir.mkdir(parents=True, exist_ok=True)
    safe_name = str(directory).replace("/", "_")
    socket_path = lock_dir / f"{safe_name}.sock"
    socket_path.unlink(missing_ok=True)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.listen(1)
    server.setblocking(False)

    from PySide6.QtCore import QTimer

    def check_socket() -> None:
        try:
            conn, _ = server.accept()
            conn.recv(64)
            conn.close()
            window.raise_()
            window.activateWindow()
        except BlockingIOError:
            pass

    timer = QTimer(window)
    timer.timeout.connect(check_socket)
    timer.start(200)

    def cleanup() -> None:
        server.close()
        socket_path.unlink(missing_ok=True)

    window.destroyed.connect(cleanup)


def run_qt_ui(directory: Path, config: AppConfig) -> int:
    if _try_focus_existing(directory):
        return 0

    app = QApplication.instance() or QApplication([])
    try:
        window = ChatWindow(directory, config)
    except Exception as exc:
        QMessageBox.critical(None, "InDir", f"Failed to start: {exc}")
        return 1

    _start_focus_server(window, directory)
    window.show()
    return app.exec()
