from __future__ import annotations

import socket
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from indir.agent.loop import AgentEvent, AgentSession, EventType
from indir.backends.model_catalog import list_models
from indir.backends.registry import create_backend
from indir.config import AppConfig, save_config

PROVIDERS = ("ollama", "openai", "grok", "anthropic", "cursor")
PROVIDER_LABELS = {
    "ollama": "Ollama (local)",
    "openai": "OpenAI",
    "grok": "Grok (xAI)",
    "anthropic": "Anthropic",
    "cursor": "Cursor",
}

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def _asset(name: str) -> str:
    return (ASSETS_DIR / name).as_posix()


class ModelFetchWorker(QThread):
    """Fetches available models for a backend off the UI thread."""

    succeeded = Signal(list)
    failed = Signal(str)

    def __init__(self, provider: str, base_url: str, api_key: str) -> None:
        super().__init__()
        self.provider = provider
        self.base_url = base_url
        self.api_key = api_key

    def run(self) -> None:
        try:
            models = list_models(self.provider, base_url=self.base_url, api_key=self.api_key)
            if not models:
                raise ValueError("No models were returned.")
            self.succeeded.emit(models)
        except Exception as exc:
            self.failed.emit(str(exc))


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


class MessageInput(QTextEdit):
    """Single-line-style input that grows with content; Enter sends."""

    submit = Signal()
    MIN_HEIGHT = 34
    MAX_HEIGHT = 120

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.document().setDocumentMargin(0)
        self.textChanged.connect(self._adjust_height)
        self.setFixedHeight(self.MIN_HEIGHT)
        self._single_line_margins = True
        self.setViewportMargins(0, 6, 0, 6)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._adjust_height()

    def _set_single_line_margins(self, single_line: bool) -> None:
        if single_line == self._single_line_margins:
            return
        self._single_line_margins = single_line
        if single_line:
            self.setViewportMargins(0, 6, 0, 6)
        else:
            self.setViewportMargins(0, 0, 0, 0)

    def _adjust_height(self) -> None:
        line_h = self.fontMetrics().lineSpacing()
        lines = max(1, self.document().lineCount())
        doc_height = lines * line_h
        frame = self.frameWidth() * 2
        margins = self.contentsMargins()
        inset = margins.top() + margins.bottom() + frame + 4
        height = max(self.MIN_HEIGHT, min(doc_height + inset, self.MAX_HEIGHT))
        if height != self.height():
            self.setFixedHeight(height)
        scrolling = doc_height + inset > self.MAX_HEIGHT
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if scrolling
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._set_single_line_margins(lines <= 1 and not scrolling)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submit.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ComposerField(QWidget):
    """Pill-shaped message field with an embedded send button."""

    submit = Signal()
    SEND_SIZE = 28

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("composerField")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.capsule = QFrame()
        self.capsule.setObjectName("composerCapsule")
        self.capsule.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        capsule_layout = QHBoxLayout(self.capsule)
        capsule_layout.setContentsMargins(14, 0, 5, 0)
        capsule_layout.setSpacing(4)
        capsule_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.input_box = MessageInput()
        self.input_box.setObjectName("composerInput")
        self.input_box.setPlaceholderText("Ask about this folder…")
        self.input_box.submit.connect(self.submit.emit)
        palette = self.input_box.palette()
        palette.setColor(QPalette.ColorRole.Base, Qt.GlobalColor.transparent)
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#5c6780"))
        self.input_box.setPalette(palette)
        self.input_box.viewport().setAutoFillBackground(False)
        self.input_box.viewport().setStyleSheet("background: transparent; border: none;")

        self.send_btn = QToolButton()
        self.send_btn.setObjectName("sendButton")
        self.send_btn.setIcon(QIcon(_asset("arrow_up.svg")))
        self.send_btn.setIconSize(QSize(15, 15))
        self.send_btn.setToolTip("Send")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setFixedSize(self.SEND_SIZE, self.SEND_SIZE)
        self.send_btn.clicked.connect(self.submit.emit)

        capsule_layout.addWidget(self.input_box, stretch=1, alignment=Qt.AlignmentFlag.AlignVCenter)
        capsule_layout.addWidget(self.send_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(self.capsule)
        self.input_box.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if obj is self.input_box:
            if event.type() == QEvent.Type.FocusIn:
                self._set_capsule_focused(True)
            elif event.type() == QEvent.Type.FocusOut:
                self._set_capsule_focused(False)
        return super().eventFilter(obj, event)

    def _set_capsule_focused(self, focused: bool) -> None:
        self.capsule.setProperty("focused", focused)
        self.capsule.style().unpolish(self.capsule)
        self.capsule.style().polish(self.capsule)


class SpinnerWidget(QWidget):
    """Small spinning arc used in the thinking bubble."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._rotate)
        self.setFixedSize(14, 14)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._angle = 0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#8b95ff"), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        rect = self.rect().adjusted(2, 2, -2, -2)
        painter.drawArc(rect, self._angle * 16, 100 * 16)

    def _rotate(self) -> None:
        self._angle = (self._angle + 18) % 360
        self.update()


class ThinkingBubble(QWidget):
    """Assistant-style bubble shown while waiting on the model."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("thinkingBubble")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 14, 8)
        layout.setSpacing(8)

        self.spinner = SpinnerWidget()
        layout.addWidget(self.spinner)

        label = QLabel("Thinking")
        label.setObjectName("thinkingBubbleLabel")
        layout.addWidget(label)

        self.hide()

    def start(self) -> None:
        self.spinner.start()
        self.show()

    def stop(self) -> None:
        self.spinner.stop()
        self.hide()


class SpeechBubble(QLabel):
    """Compact iMessage-style bubble for the last user or assistant line."""

    def __init__(self, role: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._role = role
        self.setObjectName("userBubble" if role == "user" else "assistantBubble")
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setMaximumWidth(300)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(180)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.hide()

    def set_text(self, text: str, *, error: bool = False) -> None:
        if not text.strip():
            self.clear()
            self.hide()
            return
        was_hidden = self.isHidden()
        self.setText(text.strip())
        if error:
            self.setProperty("error", True)
        else:
            self.setProperty("error", False)
        self.style().unpolish(self)
        self.style().polish(self)
        self.show()
        if was_hidden:
            self._fade.stop()
            self._fade.start()


class ContentTabButton(QPushButton):
    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(label, parent)
        self.setObjectName("contentTab")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._active = False

    def set_active(self, active: bool) -> None:
        self._active = active
        self.setChecked(active)
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)


class ConversationPanel(QFrame):
    """Chrome-style panel: optional thinking log up top, speech bubbles above the composer."""

    TAB_CHAT = 0
    TAB_THINKING = 1

    def __init__(
        self,
        directory_name: str,
        directory_path: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("contentPanel")

        self.tab_bar = QWidget()
        self.tab_bar.setObjectName("tabBar")
        self.tab_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tab_bar_layout = QHBoxLayout(self.tab_bar)
        tab_bar_layout.setContentsMargins(3, 3, 3, 3)
        tab_bar_layout.setSpacing(2)

        self.dir_tab = ContentTabButton(directory_name)
        self.dir_tab.setToolTip(directory_path)
        self.dir_tab.clicked.connect(lambda: self.set_active_tab(self.TAB_CHAT))
        tab_bar_layout.addWidget(self.dir_tab)

        self.thinking_tab = ContentTabButton("Thinking")
        self.thinking_tab.hide()
        self.thinking_tab.clicked.connect(lambda: self.set_active_tab(self.TAB_THINKING))
        tab_bar_layout.addWidget(self.thinking_tab)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.stack = QStackedWidget()
        self.stack.setObjectName("contentStack")

        chat_page = QWidget()
        chat_page.setObjectName("chatSpacer")
        chat_page_layout = QVBoxLayout(chat_page)
        chat_page_layout.setContentsMargins(0, 0, 0, 0)
        chat_page_layout.addStretch()
        self.stack.addWidget(chat_page)

        self.thinking_message = QTextEdit()
        self.thinking_message.setObjectName("thinkingMessage")
        self.thinking_message.setReadOnly(True)
        self.thinking_message.setFont(QFont("monospace", 9))
        self.stack.addWidget(self.thinking_message)
        layout.addWidget(self.stack, stretch=1)

        user_row = QHBoxLayout()
        user_row.setContentsMargins(10, 0, 10, 0)
        user_row.addStretch()
        self.user_bubble = SpeechBubble("user")
        user_row.addWidget(self.user_bubble)
        layout.addLayout(user_row)

        assistant_row = QHBoxLayout()
        assistant_row.setContentsMargins(10, 0, 10, 0)
        self.thinking_bubble = ThinkingBubble()
        self.assistant_bubble = SpeechBubble("assistant")
        assistant_row.addWidget(self.thinking_bubble)
        assistant_row.addWidget(self.assistant_bubble)
        assistant_row.addStretch()
        layout.addLayout(assistant_row)

        self.bottom_layout = QVBoxLayout()
        self.bottom_layout.setContentsMargins(10, 0, 10, 10)
        self.bottom_layout.setSpacing(8)
        layout.addLayout(self.bottom_layout)

        self.set_active_tab(self.TAB_CHAT)

    def set_active_tab(self, index: int) -> None:
        if index == self.TAB_THINKING and self.thinking_tab.isHidden():
            index = self.TAB_CHAT
        self.stack.setCurrentIndex(index)
        self.dir_tab.set_active(index == self.TAB_CHAT)
        self.thinking_tab.set_active(index == self.TAB_THINKING)

    def set_user_text(self, text: str) -> None:
        self.user_bubble.set_text(text)

    def set_assistant_text(self, text: str, *, error: bool = False) -> None:
        self.hide_thinking_indicator()
        self.assistant_bubble.set_text(text, error=error)

    def clear_assistant(self) -> None:
        self.hide_thinking_indicator()
        self.assistant_bubble.set_text("")

    def show_thinking_indicator(self) -> None:
        self.assistant_bubble.hide()
        self.thinking_bubble.start()

    def hide_thinking_indicator(self) -> None:
        self.thinking_bubble.stop()

    def append_thinking(self, text: str) -> None:
        chunk = text.strip()
        if not chunk:
            return
        existing = self.thinking_message.toPlainText().strip()
        combined = f"{existing}\n\n{chunk}" if existing else chunk
        self.thinking_message.setPlainText(combined)
        self.thinking_tab.show()

    def clear_thinking(self) -> None:
        self.thinking_message.clear()
        self.thinking_tab.hide()
        if self.stack.currentIndex() == self.TAB_THINKING:
            self.set_active_tab(self.TAB_CHAT)


class CommandPreviewWidget(QWidget):
    """Shows the proposed shell command while the composer offers approve/deny/type."""

    def __init__(self, command: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("commandCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        label = QLabel("PROPOSED COMMAND")
        label.setObjectName("commandTitle")
        layout.addWidget(label)

        self.command_edit = QTextEdit()
        self.command_edit.setPlainText(command)
        self.command_edit.setMinimumHeight(40)
        self.command_edit.setMaximumHeight(64)
        self.command_edit.setObjectName("commandInput")
        self.command_edit.setFont(QFont("monospace", 9))
        layout.addWidget(self.command_edit)

    def command_text(self) -> str:
        return self.command_edit.toPlainText()


class SettingsDialog(QDialog):
    """Lets the user pick a backend/model and enter API keys, saved to config.toml."""

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.fetch_worker: ModelFetchWorker | None = None
        self._fetch_debounce = QTimer(self)
        self._fetch_debounce.setSingleShot(True)
        self._fetch_debounce.setInterval(500)
        self._fetch_debounce.timeout.connect(self._maybe_auto_fetch)
        self.setWindowTitle("InDir settings")
        self.setMinimumWidth(400)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self.provider_combo = QComboBox()
        for provider in PROVIDERS:
            self.provider_combo.addItem(PROVIDER_LABELS[provider], provider)
        current_index = PROVIDERS.index(config.backend.provider) if config.backend.provider in PROVIDERS else 0
        self.provider_combo.setCurrentIndex(current_index)
        form.addRow("Backend", self.provider_combo)

        self.base_url_edit = QLineEdit()
        self.base_url_row_label = QLabel("Base URL")
        form.addRow(self.base_url_row_label, self.base_url_edit)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("Leave blank to keep using environment variable")
        self.api_key_row_label = QLabel("API key")

        self.reveal_btn = QToolButton()
        self.reveal_btn.setObjectName("revealButton")
        self.reveal_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reveal_btn.setText("Show")
        self.reveal_btn.setCheckable(True)
        self.reveal_btn.toggled.connect(self._toggle_reveal)
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key_edit)
        key_row.addWidget(self.reveal_btn)
        form.addRow(self.api_key_row_label, key_row)

        self.model_combo = QComboBox()
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.fetch_models_btn = QPushButton("Fetch models")
        self.fetch_models_btn.setObjectName("secondaryButton")
        self.fetch_models_btn.clicked.connect(self._on_fetch_models)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_combo, stretch=1)
        model_row.addWidget(self.fetch_models_btn)
        self.model_row_widget = QWidget()
        self.model_row_widget.setLayout(model_row)
        self.model_row_label = QLabel("Model")
        form.addRow(self.model_row_label, self.model_row_widget)

        layout.addLayout(form)

        self.fetch_status_label = QLabel("")
        self.fetch_status_label.setObjectName("status")
        self.fetch_status_label.setWordWrap(True)
        layout.addWidget(self.fetch_status_label)

        self.provider_combo.currentIndexChanged.connect(self._load_provider_fields)
        self.api_key_edit.textChanged.connect(self._on_credentials_changed)
        self.base_url_edit.textChanged.connect(self._on_credentials_changed)
        self._load_provider_fields()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _current_provider(self) -> str:
        return self.provider_combo.currentData()

    def _model_text(self) -> str:
        if not self.model_combo.isEnabled():
            return ""
        return self.model_combo.currentText().strip()

    def _has_credentials(self, provider: str) -> bool:
        if provider == "ollama":
            return bool(self.base_url_edit.text().strip())
        if provider in ("openai", "grok", "anthropic", "cursor"):
            return bool(self.api_key_edit.text().strip())
        return False

    def _reset_model_dropdown(self, saved_model: str = "") -> None:
        """Disable the model dropdown and show a placeholder until models are fetched."""
        self.model_combo.clear()
        if self._current_provider() == "ollama":
            placeholder = "Enter a base URL, then fetch models"
        else:
            placeholder = "Enter an API key to load models"
        self.model_combo.addItem(placeholder)
        self.model_combo.setEnabled(False)
        self._pending_model = saved_model

    def _load_provider_fields(self) -> None:
        provider = self._current_provider()
        backend = self.config.backend
        has_base_url = provider in ("ollama", "openai", "grok")
        has_api_key = provider in ("openai", "grok", "anthropic", "cursor")

        self.base_url_row_label.setVisible(has_base_url)
        self.base_url_edit.setVisible(has_base_url)
        self.api_key_row_label.setVisible(has_api_key)
        self.api_key_edit.setVisible(has_api_key)
        self.reveal_btn.setVisible(has_api_key)
        self.fetch_status_label.setText("")

        saved_model = ""
        if provider == "ollama":
            saved_model = backend.ollama.model
            self.base_url_edit.setText(backend.ollama.base_url)
        elif provider == "openai":
            saved_model = backend.openai.model
            self.base_url_edit.setText(backend.openai.base_url)
            self.api_key_edit.setText(backend.openai.api_key)
        elif provider == "grok":
            saved_model = backend.grok.model
            self.base_url_edit.setText(backend.grok.base_url)
            self.api_key_edit.setText(backend.grok.api_key)
        elif provider == "anthropic":
            saved_model = backend.anthropic.model
            self.api_key_edit.setText(backend.anthropic.api_key)
        elif provider == "cursor":
            saved_model = backend.cursor.model
            self.api_key_edit.setText(backend.cursor.api_key)

        self._reset_model_dropdown(saved_model)
        if self._has_credentials(provider):
            self._on_fetch_models()

    def _on_credentials_changed(self) -> None:
        self._reset_model_dropdown()
        self.fetch_status_label.setText("")
        self._fetch_debounce.start()

    def _maybe_auto_fetch(self) -> None:
        provider = self._current_provider()
        if self._has_credentials(provider):
            self._on_fetch_models()

    def _toggle_reveal(self, checked: bool) -> None:
        self.api_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self.reveal_btn.setText("Hide" if checked else "Show")

    def _on_fetch_models(self) -> None:
        if self.fetch_worker is not None and self.fetch_worker.isRunning():
            return
        provider = self._current_provider()
        if not self._has_credentials(provider):
            return
        base_url = self.base_url_edit.text().strip()
        api_key = self.api_key_edit.text().strip()

        self.fetch_models_btn.setEnabled(False)
        self.fetch_status_label.setStyleSheet("color: #8290a3;")
        self.fetch_status_label.setText("Fetching models…")

        self.fetch_worker = ModelFetchWorker(provider, base_url, api_key)
        self.fetch_worker.succeeded.connect(self._on_fetch_succeeded)
        self.fetch_worker.failed.connect(self._on_fetch_failed)
        self.fetch_worker.finished.connect(lambda: self.fetch_models_btn.setEnabled(True))
        self.fetch_worker.start()

    def _on_fetch_succeeded(self, models: list[str]) -> None:
        pending = getattr(self, "_pending_model", "")
        self.model_combo.clear()
        self.model_combo.addItems(models)
        self.model_combo.setEnabled(True)
        if pending and pending in models:
            self.model_combo.setCurrentText(pending)
        self._pending_model = ""
        self.fetch_status_label.setStyleSheet("color: #a8dfbb;")
        self.fetch_status_label.setText(f"Found {len(models)} model(s).")

    def _on_fetch_failed(self, message: str) -> None:
        self._reset_model_dropdown(getattr(self, "_pending_model", ""))
        self.fetch_status_label.setStyleSheet("color: #ffb4c0;")
        self.fetch_status_label.setText(f"Couldn't fetch models: {message}")

    def _on_save(self) -> None:
        provider = self._current_provider()
        model = self._model_text()
        if not model:
            QMessageBox.warning(self, "InDir", "Model is required.")
            return

        backend = self.config.backend
        backend.provider = provider
        if provider == "ollama":
            backend.ollama.model = model
            backend.ollama.base_url = self.base_url_edit.text().strip() or backend.ollama.base_url
        elif provider == "openai":
            backend.openai.model = model
            backend.openai.base_url = self.base_url_edit.text().strip() or backend.openai.base_url
            backend.openai.api_key = self.api_key_edit.text().strip()
        elif provider == "grok":
            backend.grok.model = model
            backend.grok.base_url = self.base_url_edit.text().strip() or backend.grok.base_url
            backend.grok.api_key = self.api_key_edit.text().strip()
        elif provider == "anthropic":
            backend.anthropic.model = model
            backend.anthropic.api_key = self.api_key_edit.text().strip()
        elif provider == "cursor":
            backend.cursor.model = model
            backend.cursor.api_key = self.api_key_edit.text().strip()

        try:
            save_config(self.config)
        except OSError as exc:
            QMessageBox.critical(self, "InDir", f"Failed to save config: {exc}")
            return

        self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self.fetch_worker is not None and self.fetch_worker.isRunning():
            self.fetch_worker.requestInterruption()
            self.fetch_worker.wait(2000)
        super().closeEvent(event)


class ChatWindow(QMainWindow):
    def __init__(self, directory: Path, config: AppConfig) -> None:
        super().__init__()
        self.directory = directory
        self.config = config
        self.backend = create_backend(config, directory)
        self.session = AgentSession(directory, config, self.backend)
        self.worker: SessionWorker | None = None
        self._pending_tool_call_id: str | None = None
        self._command_preview: CommandPreviewWidget | None = None

        self.setWindowTitle(f"InDir — {directory.name}")
        self.resize(430, 460)
        self.setMinimumSize(360, 320)
        self._apply_dark_theme()
        self._build_ui()

    def _apply_dark_theme(self) -> None:
        style = """
            QMainWindow, QDialog, QMessageBox { background: #0b0e14; }
            QWidget { color: #e2e8f4; font-size: 13px; background: transparent; }

            QToolTip {
                background: #1b2230; color: #cdd6e6;
                border: 1px solid #2b3548; border-radius: 6px;
                padding: 4px 8px;
            }

            QScrollBar:vertical {
                background: transparent; width: 8px; margin: 2px;
            }
            QScrollBar::handle:vertical {
                background: #2e3850; border-radius: 4px; min-height: 24px;
            }
            QScrollBar::handle:vertical:hover { background: #3d4a6a; }
            QScrollBar:horizontal {
                background: transparent; height: 8px; margin: 2px;
            }
            QScrollBar::handle:horizontal {
                background: #2e3850; border-radius: 4px; min-width: 24px;
            }
            QScrollBar::handle:horizontal:hover { background: #3d4a6a; }
            QScrollBar::add-line, QScrollBar::sub-line {
                width: 0; height: 0; border: none; background: transparent;
            }
            QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

            QTextEdit {
                background: #161c27; color: #e2e8f4;
                border: 1px solid #212938; border-radius: 10px;
                padding: 7px;
                selection-background-color: #3b4f8f;
            }
            QTextEdit:focus { border: 1px solid #6d7dff; }

            QPushButton {
                background: #1b2230; color: #cdd6e6;
                border: 1px solid #2b3548; border-radius: 8px;
                padding: 6px 14px; font-weight: 600;
            }
            QPushButton:hover { background: #232c3f; }
            QPushButton:pressed { background: #1a2130; }
            QPushButton:disabled {
                background: #151a25; color: #5f6a80; border-color: #212938;
            }
            QPushButton#primaryButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #5b7cfa, stop:1 #8f5ef2);
                color: #ffffff; border: none;
                padding: 7px 16px; border-radius: 8px; font-weight: 600;
            }
            QPushButton#primaryButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #6f8cff, stop:1 #a273f7);
            }
            QPushButton#primaryButton:disabled { background: #232c3f; color: #5f6a80; }
            QPushButton#secondaryButton {
                background: transparent; color: #98a2b8;
                border: 1px solid #2b3548;
            }
            QPushButton#secondaryButton:hover { background: #1b2230; color: #e2e8f4; }
            QPushButton#dangerButton {
                background: transparent; color: #ff9db0;
                border: 1px solid #4d2536;
            }
            QPushButton#dangerButton:hover { background: #2b1620; }

            QToolButton#iconButton {
                background: transparent; border: none; border-radius: 14px; padding: 0;
            }
            QToolButton#iconButton:hover { background: #1b2230; }
            QToolButton#revealButton {
                background: transparent; color: #98a2b8;
                border: 1px solid #2b3548; border-radius: 8px;
                padding: 6px 10px; font-weight: 600;
            }
            QToolButton#revealButton:hover { background: #1b2230; color: #e2e8f4; }
            QToolButton#revealButton:checked { color: #b9c4ff; border-color: #6d7dff; }

            QLabel#brand {
                color: #f5f8fd; font-size: 14px; font-weight: 700;
                letter-spacing: 0.5px; padding: 0 2px;
            }
            QLabel#status { color: #5f6a80; font-size: 11px; padding-top: 6px; }

            QWidget#tabBar {
                background: #10141c;
                border: 1px solid #212938;
                border-radius: 9px;
            }
            QPushButton#contentTab {
                background: transparent; color: #98a2b8;
                border: none; border-radius: 6px;
                padding: 3px 12px; font-size: 12px; font-weight: 600;
            }
            QPushButton#contentTab:hover { color: #e2e8f4; }
            QPushButton#contentTab[active="true"] {
                background: #232c3f; color: #b9c4ff;
            }

            QFrame#contentPanel {
                background: #10141c;
                border: 1px solid #212938;
                border-radius: 14px;
            }
            QStackedWidget#contentStack, QWidget#chatSpacer {
                background: transparent; border: none;
            }
            QTextEdit#thinkingMessage {
                background: transparent; color: #8d99b0;
                border: none; padding: 10px;
            }

            QWidget#thinkingBubble {
                background: #161c27;
                border: 1px solid #212938;
                border-radius: 15px;
                border-bottom-left-radius: 5px;
            }
            QLabel#thinkingBubbleLabel { color: #8d99b0; font-size: 12px; }
            QLabel#userBubble {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #5b7cfa, stop:1 #8f5ef2);
                color: #ffffff;
                padding: 8px 13px;
                border-radius: 16px;
                border-bottom-right-radius: 5px;
            }
            QLabel#assistantBubble {
                background: #161c27;
                color: #dde4f0;
                padding: 8px 13px;
                border: 1px solid #212938;
                border-radius: 16px;
                border-bottom-left-radius: 5px;
            }
            QLabel#assistantBubble[error="true"] {
                background: #2b1620;
                color: #ff9db0;
                border-color: #4d2536;
            }

            QFrame#composerCapsule {
                background: #161c27;
                border: 1px solid #2b3548;
                border-radius: 20px;
                min-height: 40px;
            }
            QFrame#composerCapsule[focused="true"] {
                border: 1px solid #6d7dff;
                background: #181f2c;
            }
            QWidget#composerField, QWidget#composerRow { background: transparent; }
            QTextEdit#composerInput {
                background: transparent; color: #e2e8f4;
                border: none; border-radius: 0; padding: 0;
            }
            QTextEdit#composerInput:focus { border: none; }
            QToolButton#sendButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #5b7cfa, stop:1 #8f5ef2);
                border: none; border-radius: 14px;
                min-width: 28px; max-width: 28px;
                min-height: 28px; max-height: 28px;
                padding: 0;
            }
            QToolButton#sendButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #6f8cff, stop:1 #a273f7);
            }
            QToolButton#sendButton:disabled { background: #232c3f; }

            QWidget#commandCard {
                background: #131926;
                border: 1px solid #2b3548;
                border-radius: 12px;
            }
            QLabel#commandTitle {
                color: #7d88a0; font-size: 10px;
                font-weight: 700; letter-spacing: 1px;
            }
            QTextEdit#commandInput {
                background: #0d1119;
                border: 1px solid #212938;
                border-radius: 8px;
                color: #c9d4e8;
                padding: 8px;
            }

            QLineEdit {
                background: #161c27; color: #e2e8f4;
                border: 1px solid #212938; border-radius: 8px;
                padding: 7px 10px;
                selection-background-color: #3b4f8f;
            }
            QLineEdit:focus { border: 1px solid #6d7dff; }
            QComboBox {
                background: #161c27; color: #e2e8f4;
                border: 1px solid #212938; border-radius: 8px;
                padding: 7px 10px;
            }
            QComboBox:hover { border-color: #2b3548; }
            QComboBox:focus { border: 1px solid #6d7dff; }
            QComboBox:disabled { color: #5f6a80; background: #131824; }
            QComboBox::drop-down { border: none; width: 26px; }
            QComboBox::down-arrow {
                image: url(@CHEVRON@);
                width: 14px; height: 14px;
            }
            QComboBox QAbstractItemView {
                background: #151b28; color: #dde4f0;
                border: 1px solid #2b3548; border-radius: 8px;
                selection-background-color: #26304a;
                selection-color: #ffffff;
                padding: 4px;
                outline: none;
            }
        """
        self.setStyleSheet(style.replace("@CHEVRON@", _asset("chevron_down.svg")))

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        dir_name = self.directory.name or str(self.directory)
        self.message_panel = ConversationPanel(dir_name, str(self.directory))

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        brand = QLabel("InDir")
        brand.setObjectName("brand")
        header_layout.addWidget(brand, alignment=Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(
            self.message_panel.tab_bar,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        header_layout.addStretch()

        settings_btn = QToolButton()
        settings_btn.setObjectName("iconButton")
        settings_btn.setIcon(QIcon(_asset("gear.svg")))
        settings_btn.setIconSize(QSize(15, 15))
        settings_btn.setFixedSize(28, 28)
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.setToolTip("Backend & model settings")
        settings_btn.clicked.connect(self._open_settings)
        header_layout.addWidget(settings_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header_layout)

        layout.addWidget(self.message_panel, stretch=1)

        self.approval_host = QWidget()
        self.approval_layout = QVBoxLayout(self.approval_host)
        self.approval_layout.setContentsMargins(0, 0, 0, 0)
        self.approval_layout.setSpacing(0)
        self.approval_host.hide()
        self.message_panel.bottom_layout.addWidget(self.approval_host)

        self.composer = ComposerField()
        self.composer.submit.connect(self._on_send)
        self.input_box = self.composer.input_box
        self.send_btn = self.composer.send_btn

        self.approve_btn = QPushButton("Approve")
        self.approve_btn.setObjectName("primaryButton")
        self.approve_btn.clicked.connect(self._on_approve_command)
        self.deny_btn = QPushButton("Deny")
        self.deny_btn.setObjectName("dangerButton")
        self.deny_btn.clicked.connect(self._on_deny_command)
        self.type_btn = QPushButton("Type")
        self.type_btn.setObjectName("secondaryButton")
        self.type_btn.clicked.connect(self._on_type_mode)

        self.approval_actions = QWidget()
        approval_layout = QHBoxLayout(self.approval_actions)
        approval_layout.setContentsMargins(0, 0, 0, 0)
        approval_layout.setSpacing(6)
        approval_layout.addWidget(self.approve_btn, stretch=1)
        approval_layout.addWidget(self.deny_btn, stretch=1)
        approval_layout.addWidget(self.type_btn, stretch=1)
        self.approval_actions.hide()

        composer_row = QWidget()
        composer_row.setObjectName("composerRow")
        composer_row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        composer_layout = QHBoxLayout(composer_row)
        composer_layout.setContentsMargins(0, 0, 0, 0)
        composer_layout.setSpacing(6)
        composer_layout.addWidget(self.composer, stretch=1)
        composer_layout.addWidget(self.approval_actions, stretch=1)
        self.message_panel.bottom_layout.addWidget(composer_row)

        self.status_label = QLabel("Enter to send · Shift+Enter for newline")
        self.status_label.setObjectName("status")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.status_label)
        self.input_box.setFocus()

    def _append_background(self, heading: str, body: str) -> None:
        self.message_panel.append_thinking(f"{heading}\n{body.strip()}")

    def _show_composer_input(self) -> None:
        self.composer.show()
        self.approval_actions.hide()
        self.input_box.setFocus()

    def _show_composer_approval(self) -> None:
        self.composer.hide()
        self.approval_actions.show()

    def _clear_approval(self) -> None:
        while self.approval_layout.count():
            item = self.approval_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.approval_host.hide()
        self._command_preview = None
        self._pending_tool_call_id = None
        self._show_composer_input()

    def _show_command_preview(self, tool_call_id: str, command: str) -> None:
        while self.approval_layout.count():
            item = self.approval_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._pending_tool_call_id = tool_call_id
        self._command_preview = CommandPreviewWidget(command)
        self.approval_layout.addWidget(self._command_preview)
        self.approval_host.show()
        self._show_composer_approval()

    def _open_settings(self) -> None:
        if self._worker_running():
            QMessageBox.information(self, "InDir", "Wait for the current task to finish first.")
            return
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.backend = create_backend(self.config, self.directory)
            except Exception as exc:
                QMessageBox.critical(self, "InDir", f"Failed to apply new backend: {exc}")
                return
            self.session = AgentSession(self.directory, self.config, self.backend)
            provider = PROVIDER_LABELS.get(self.config.backend.provider, self.config.backend.provider)
            self.status_label.setText(f"Switched to {provider}.")

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.send_btn.setEnabled(not busy)
        self.input_box.setEnabled(not busy)
        self.approve_btn.setEnabled(not busy)
        self.deny_btn.setEnabled(not busy)
        self.type_btn.setEnabled(not busy)
        if self._command_preview is not None:
            self._command_preview.command_edit.setReadOnly(busy)
        if busy:
            self.message_panel.show_thinking_indicator()
        else:
            self.message_panel.hide_thinking_indicator()
        self.status_label.setText(message or "Enter to send · Shift+Enter for newline")

    def _worker_running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def _start_worker(self, task: str, busy_message: str, **kwargs) -> None:
        if self._worker_running():
            return
        self._set_busy(True, busy_message)
        self.worker = SessionWorker(task, self.session, **kwargs)
        self.worker.finished_events.connect(self._on_worker_events)
        self.worker.error.connect(self._on_worker_error)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def _on_worker_finished(self) -> None:
        self._set_busy(False, "")

    def _on_send(self) -> None:
        text = self.input_box.toPlainText().strip()
        if not text or self._worker_running():
            return
        self.input_box.clear()
        self.input_box.setFixedHeight(MessageInput.MIN_HEIGHT)
        self.message_panel.set_user_text(text)
        self.message_panel.clear_assistant()
        self.message_panel.clear_thinking()
        self._start_worker("message", "Thinking…", text=text)

    def _on_worker_error(self, msg: str) -> None:
        self.message_panel.set_assistant_text(msg, error=True)

    def _on_worker_events(self, events: list) -> None:
        for event in events:
            self._handle_event(event)

    def _on_error(self, msg: str) -> None:
        self._on_worker_error(msg)

    def _on_events(self, events: list) -> None:
        self._on_worker_events(events)

    def _handle_event(self, event: AgentEvent) -> None:
        if event.type == EventType.ASSISTANT_TEXT:
            self.message_panel.set_assistant_text(event.content)
        elif event.type == EventType.THINKING:
            self._append_background("Reasoning", event.content)
        elif event.type == EventType.TOOL_RESULT:
            self._append_background("Tool result", event.content)
        elif event.type == EventType.COMMAND_RESULT:
            self._append_background("Command output", event.content)
        elif event.type == EventType.COMMAND_PENDING:
            self._show_command_preview(event.tool_call_id or "", event.command or "")
            self.message_panel.set_assistant_text(event.content)
        elif event.type == EventType.ERROR:
            self.message_panel.set_assistant_text(event.content, error=True)

    def _on_approve_command(self) -> None:
        if not self._pending_tool_call_id or self._worker_running():
            return
        command = self._command_preview.command_text() if self._command_preview else ""
        tool_call_id = self._pending_tool_call_id
        self._clear_approval()
        self._start_worker(
            "approve",
            "Running command…",
            tool_call_id=tool_call_id,
            command=command,
        )

    def _on_deny_command(self) -> None:
        if not self._pending_tool_call_id or self._worker_running():
            return
        tool_call_id = self._pending_tool_call_id
        self._clear_approval()
        self._start_worker("reject", "Thinking…", tool_call_id=tool_call_id)

    def _on_type_mode(self) -> None:
        if self._pending_tool_call_id is None:
            self._show_composer_input()
            return
        self._show_composer_input()
        self.status_label.setText("Type a message, or edit the command above and approve.")


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
    font = app.font()
    font.setFamilies(
        ["Inter", "SF Pro Text", "Segoe UI", "Ubuntu", "Cantarell", "Noto Sans", "DejaVu Sans"]
    )
    app.setFont(font)
    try:
        window = ChatWindow(directory, config)
    except Exception as exc:
        QMessageBox.critical(None, "InDir", f"Failed to start: {exc}")
        return 1

    _start_focus_server(window, directory)
    window.show()
    return app.exec()
