from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from indir.ui.chat_window import ModelComboBox, filter_model_names


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_filter_model_names_matches_any_provider_id() -> None:
    models = ["gpt-4o", "gpt-4o-mini", "claude-sonnet", "grok-3"]

    assert filter_model_names(models, "") == models
    assert filter_model_names(models, "   ") == models
    assert filter_model_names(models, "GPT") == ["gpt-4o", "gpt-4o-mini"]
    assert filter_model_names(models, "sonnet") == ["claude-sonnet"]
    assert filter_model_names(models, "missing") == []


def test_model_dropdown_search_filters_rows() -> None:
    _app()
    combo = ModelComboBox()
    combo.addItems(["gpt-4o", "gpt-4o-mini", "claude-sonnet"])
    combo.resize(280, 32)
    combo.show()

    combo.showPopup()
    search = combo._search
    assert search.isVisible()
    assert combo._search_host is not None
    assert combo._search_host.height() < 64
    screen = combo.screen().availableGeometry()
    assert combo.view().parentWidget().height() <= screen.height()
    assert search.placeholderText() == "Search models…"
    container = combo.view().parentWidget()
    assert container is not None
    assert combo._search_host is not None
    assert combo._search_host.parentWidget() is container

    search.setText("claude")
    view = combo.view()
    assert view.isRowHidden(0)
    assert view.isRowHidden(1)
    assert not view.isRowHidden(2)
    assert view.currentIndex().row() == 2

    QTest.keyClick(search, Qt.Key.Key_Return)
    assert combo.currentText() == "claude-sonnet"
    assert not combo.view().isVisible()
    assert search.text() == ""
    assert not any(view.isRowHidden(index) for index in range(combo.count()))

    combo.hide()
    combo.deleteLater()


def test_model_dropdown_stays_shorter_than_the_screen() -> None:
    _app()
    combo = ModelComboBox()
    combo.addItems([f"model-{index:03d}" for index in range(80)])
    combo.resize(280, 32)
    combo.show()

    combo.showPopup()
    container = combo.view().parentWidget()
    assert container is not None
    screen = combo.screen().availableGeometry()
    assert container.height() <= screen.height() // 2
    assert container.height() < 480

    combo.hide()
    combo.deleteLater()


def test_model_dropdown_stays_shorter_than_the_screen() -> None:
    _app()
    combo = ModelComboBox()
    combo.addItems([f"model-{index:03d}" for index in range(80)])
    combo.resize(280, 32)
    combo.show()

    combo.showPopup()
    container = combo.view().parentWidget()
    assert container is not None
    screen = combo.screen().availableGeometry()
    assert container.height() <= screen.height() // 2
    assert container.height() < 480

    combo.hide()
    combo.deleteLater()
