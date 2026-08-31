from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from indir.backends.model_catalog import list_cursor_models, list_models


def test_list_cursor_models_requires_api_key() -> None:
    with pytest.raises(ValueError, match="API key"):
        list_cursor_models("")


def test_list_cursor_models_returns_sorted_ids() -> None:
    mock_models = [
        MagicMock(id="composer-2.5"),
        MagicMock(id="auto"),
        MagicMock(id=""),
    ]
    with patch("cursor_sdk.Cursor.models.list", return_value=mock_models):
        assert list_cursor_models("cursor_test_key") == ["auto", "composer-2.5"]


def test_list_models_cursor_delegates() -> None:
    with patch(
        "indir.backends.model_catalog.list_cursor_models",
        return_value=["composer-2.5"],
    ) as list_cursor:
        assert list_models("cursor", api_key="key") == ["composer-2.5"]
        list_cursor.assert_called_once_with("key")
