from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Static

from indir.agent.loop import AgentEvent, AgentSession, EventType
from indir.backends.registry import create_backend
from indir.config import AppConfig


class MessageStatic(Static):
    pass


class CommandPanel(Static):
    def __init__(self, tool_call_id: str, command: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.tool_call_id = tool_call_id
        self.command = command


class DirectoryAITApp(App):
    CSS = """
    Screen { background: #1e1e1e; }
    #chat-scroll { height: 1fr; border: solid #333; }
    .user-msg { background: #264f78; padding: 1; margin: 1 0; }
    .assistant-msg { background: #2d2d30; padding: 1; margin: 1 0; }
    .tool-msg { background: #1a2a1a; padding: 1; margin: 1 0; color: #aaffaa; }
    .error-msg { background: #4a1a1a; padding: 1; margin: 1 0; color: #ff8888; }
    .system-msg { background: #1a1a1a; padding: 1; margin: 1 0; color: #888; }
    #command-panel { background: #2a2a1a; border: solid #665500; padding: 1; margin: 1 0; }
    #input-row { height: 3; }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self, directory: Path, config: AppConfig) -> None:
        super().__init__()
        self.directory = directory
        self.config = config
        self.backend = None
        self.session: AgentSession | None = None
        self._pending_tool_call_id: str | None = None
        self._backend_error: str | None = None
        try:
            self.backend = create_backend(config, directory)
            self.session = AgentSession(directory, config, self.backend)
        except Exception as exc:
            self._backend_error = str(exc)

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="chat-scroll"):
            if self._backend_error:
                yield Static(
                    f"Backend not ready: {self._backend_error}\n"
                    "Configure provider/model/API key in the Qt settings UI "
                    "(launch without --tui), then retry.",
                    classes="error-msg",
                )
            else:
                yield Static(
                    f"Working directory: {self.directory}\nTry: \"convert webm to mp4\"",
                    classes="system-msg",
                )
        with Vertical(id="input-row"):
            yield Input(placeholder="Ask anything about this directory…", id="user-input")
        yield Footer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "user-input":
            return
        text = event.value.strip()
        if not text:
            return
        if self.session is None:
            self._add_message(
                self._backend_error
                or "Backend not configured. Use the Qt settings UI, then retry.",
                "error-msg",
            )
            return
        event.input.value = ""
        self._add_message(text, "user-msg")
        self.run_worker(self._process_message(text), exclusive=True)

    async def _process_message(self, text: str) -> None:
        try:
            events = self.session.send_user_message(text)
            for event in events:
                self._handle_event(event)
        except Exception as exc:
            self._add_message(f"Error: {exc}", "error-msg")

    def _handle_event(self, event: AgentEvent) -> None:
        if event.type == EventType.ASSISTANT_TEXT:
            self._add_message(event.content, "assistant-msg")
        elif event.type == EventType.TOOL_RESULT:
            self._add_message(event.content, "tool-msg")
        elif event.type == EventType.COMMAND_PENDING:
            self._show_command_panel(event.tool_call_id or "", event.command or "")
        elif event.type == EventType.COMMAND_RESULT:
            self._add_message(event.content, "tool-msg")
        elif event.type == EventType.ERROR:
            self._add_message(event.content, "error-msg")

    def _add_message(self, text: str, css_class: str) -> None:
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.mount(Static(text, classes=css_class))
        scroll.scroll_end(animate=False)

    def _show_command_panel(self, tool_call_id: str, command: str) -> None:
        self._pending_tool_call_id = tool_call_id
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        panel = Vertical(id="command-panel")
        panel.mount(Static(f"Proposed command:\n{command}"))
        run_btn = Button("Run", id="run-cmd", variant="primary")
        cancel_btn = Button("Cancel", id="cancel-cmd")
        panel.mount(run_btn, cancel_btn)
        scroll.mount(panel)
        scroll.scroll_end(animate=False)
        self._pending_command = command

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if not self._pending_tool_call_id:
            return
        tool_call_id = self._pending_tool_call_id
        command = getattr(self, "_pending_command", "")

        if event.button.id == "run-cmd":
            self._pending_tool_call_id = None
            panel = self.query_one("#command-panel", Vertical)
            panel.remove()
            self._add_message(f"Running: {command}", "system-msg")
            self.run_worker(
                self._approve_command(tool_call_id, command),
                exclusive=True,
            )
        elif event.button.id == "cancel-cmd":
            self._pending_tool_call_id = None
            panel = self.query_one("#command-panel", Vertical)
            panel.remove()
            self.run_worker(
                self._reject_command(tool_call_id),
                exclusive=True,
            )

    async def _approve_command(self, tool_call_id: str, command: str) -> None:
        try:
            events = self.session.approve_command(tool_call_id, command)
            for ev in events:
                self._handle_event(ev)
        except Exception as exc:
            self._add_message(f"Error: {exc}", "error-msg")

    async def _reject_command(self, tool_call_id: str) -> None:
        try:
            events = self.session.reject_command(tool_call_id)
            for ev in events:
                self._handle_event(ev)
        except Exception as exc:
            self._add_message(f"Error: {exc}", "error-msg")


def run_tui(directory: Path, config: AppConfig) -> int:
    app = DirectoryAITApp(directory, config)
    app.run()
    return 0
