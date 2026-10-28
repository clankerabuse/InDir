"""Tests for the Nautilus glue module and the installer script.

The glue needs ``gi`` (PyGObject) and the Nautilus typelib, which the project
virtualenv does not carry — Nautilus loads it inside its own interpreter. So it
is validated structurally here (compiles, right API version, right class shape)
and exercised for real by loading it from the extensions directory.
"""

from __future__ import annotations

import ast
import importlib.util
import py_compile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GLUE = REPO_ROOT / "nautilus" / "indir.py"
SUPPORT = REPO_ROOT / "src" / "indir" / "nautilus_support.py"

spec = importlib.util.spec_from_file_location(
    "nautilus_ext", REPO_ROOT / "scripts" / "nautilus_ext.py"
)
assert spec is not None and spec.loader is not None
nautilus_ext = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nautilus_ext)


# ── glue module ──────────────────────────────────────────────────────────────


def test_glue_compiles(tmp_path: Path) -> None:
    py_compile.compile(str(GLUE), cfile=str(tmp_path / "indir.pyc"), doraise=True)


def test_glue_targets_installed_nautilus_api() -> None:
    source = GLUE.read_text(encoding="utf-8")
    assert 'require_version("Nautilus", "4.1")' in source


def test_glue_defines_menu_provider() -> None:
    tree = ast.parse(GLUE.read_text(encoding="utf-8"))
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    assert "InDirExtension" in classes
    bases = [
        base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
        for base in classes["InDirExtension"].bases
    ]
    assert "MenuProvider" in bases
    methods = {
        node.name
        for node in classes["InDirExtension"].body
        if isinstance(node, ast.FunctionDef)
    }
    assert {"get_file_items", "get_background_items"} <= methods


def test_glue_locates_support_in_both_layouts() -> None:
    source = GLUE.read_text(encoding="utf-8")
    assert "indir_nautilus_support.py" in source
    assert '"src" / "indir" / "nautilus_support.py"' in source


def test_support_module_has_no_gi_dependency() -> None:
    source = SUPPORT.read_text(encoding="utf-8")
    assert "gi.repository" not in source
    assert "require_version" not in source


# ── installer ────────────────────────────────────────────────────────────────


def test_install_links_checkout_files(tmp_path: Path) -> None:
    ext_dir = tmp_path / "nautilus-python" / "extensions"
    written = nautilus_ext.install(ext_dir)
    assert written == [
        ext_dir / "indir_nautilus.py",
        tmp_path / "nautilus-python" / "indir_nautilus_support.py",
    ]
    assert written[0].is_symlink()
    assert written[0].resolve() == GLUE.resolve()
    assert written[1].read_text(encoding="utf-8") == SUPPORT.read_text(encoding="utf-8")
    assert nautilus_ext.status(ext_dir) == "installed"


def test_install_can_copy_instead_of_link(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    written = nautilus_ext.install(ext_dir, link=False)
    assert not written[0].is_symlink()
    assert written[0].read_text(encoding="utf-8") == GLUE.read_text(encoding="utf-8")


def test_support_sits_outside_scanned_extensions_dir(tmp_path: Path) -> None:
    ext_dir = tmp_path / "nautilus-python" / "extensions"
    nautilus_ext.install(ext_dir)
    scanned = {path.name for path in ext_dir.iterdir()}
    assert scanned == {"indir_nautilus.py"}


def test_install_is_idempotent(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    nautilus_ext.install(ext_dir)
    nautilus_ext.install(ext_dir)
    assert nautilus_ext.status(ext_dir) == "installed"


def test_remove_cleans_up(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    nautilus_ext.install(ext_dir)
    removed = nautilus_ext.remove(ext_dir)
    assert len(removed) == 2
    assert nautilus_ext.status(ext_dir) == "missing"


def test_remove_when_absent(tmp_path: Path) -> None:
    assert nautilus_ext.remove(tmp_path / "extensions") == []


def test_status_detects_partial_install(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    nautilus_ext.install(ext_dir)
    nautilus_ext.support_path(ext_dir).unlink()
    assert nautilus_ext.status(ext_dir) == "partial"


def test_install_fails_on_missing_source(tmp_path: Path) -> None:
    try:
        nautilus_ext.install(
            tmp_path / "extensions", ext_source=tmp_path / "nope.py"
        )
    except FileNotFoundError as exc:
        assert "nope.py" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected FileNotFoundError")
