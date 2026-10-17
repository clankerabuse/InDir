from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

from indir.config import AppConfig, load_config
from indir.ui.chat_window import ChatWindow, SettingsDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _themed_window() -> QMainWindow:
    """A window carrying the exact stylesheet the app applies at startup."""
    _app()
    window = QMainWindow()
    ChatWindow._apply_dark_theme(window)
    window.resize(600, 400)
    window.show()
    return window


def _quiet_config() -> AppConfig:
    """No credentials, so the dialog does not start a model fetch thread."""
    config = load_config(Path("/nonexistent/config.toml"))
    config.backend.provider = "anthropic"
    config.backend.anthropic.api_key = ""
    return config


def _settings_dialog(host: QMainWindow) -> SettingsDialog:
    dialog = SettingsDialog(_quiet_config(), host)
    dialog.resize(560, 460)
    dialog.show()
    _app().processEvents()
    return dialog


def test_settings_dialog_background_is_opaque() -> None:
    """Transparent container backgrounds let the desktop show through the dialog."""
    host = _themed_window()
    dialog = _settings_dialog(host)
    image = dialog.grab().toImage()

    # Margins inside the dialog hold no widget, so they expose its background.
    for point in ((10, 10), (10, 300), (540, 300)):
        assert image.pixelColor(*point).alpha() == 255, f"transparent at {point}"

    dialog.deleteLater()
    host.deleteLater()


def test_message_box_background_is_opaque() -> None:
    host = _themed_window()
    box = QMessageBox(QMessageBox.Icon.Information, "InDir", "hello", parent=host)
    box.show()
    _app().processEvents()
    image = box.grab().toImage()

    for point in ((5, 5), (image.width() // 2, 8)):
        assert image.pixelColor(*point).alpha() == 255, f"transparent at {point}"

    box.deleteLater()
    host.deleteLater()


def test_main_window_background_is_opaque() -> None:
    window = _themed_window()
    image = window.grab().toImage()

    assert image.pixelColor(2, 2).alpha() == 255
    window.deleteLater()
