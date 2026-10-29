from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_DIR / "scripts" / "nautilus_extension.py"
TEMPLATE = PROJECT_DIR / "nautilus" / "indir_nautilus.py"

spec = importlib.util.spec_from_file_location("nautilus_extension", SCRIPT)
assert spec is not None and spec.loader is not None
nautilus_extension = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nautilus_extension)


# ── fake PyGObject/Nautilus so the rendered extension can be exercised ────────


class FakeLocation:
    def __init__(self, path):
        self._path = path

    def get_path(self):
        return self._path


class FakeFileInfo:
    def __init__(self, path=None):
        self._path = path

    def get_location(self):
        return FakeLocation(self._path) if self._path else None


class FakeMenuItem:
    def __init__(self, name, label, tip=None, icon=None):
        self.props = {"name": name, "label": label, "tip": tip, "icon": icon}
        self._handler = None
        self._args = ()

    def connect(self, signal, handler, *args):
        if signal != "activate":
            raise AssertionError(f"unexpected signal {signal!r}")
        self._handler = handler
        self._args = args

    def activate(self):
        self._handler(self, *self._args)


class FakeSubprocess:
    calls: list[list[str]] = []

    @classmethod
    def new(cls, argv, flags=None):
        cls.calls.append(list(argv))
        return object()


def _fake_modules():
    GObject = types.ModuleType("gi.repository.GObject")
    GObject.GObject = type("GObject", (), {})
    Gio = types.ModuleType("gi.repository.Gio")
    Gio.Subprocess = FakeSubprocess
    Gio.SubprocessFlags = types.SimpleNamespace(NONE=0)
    Nautilus = types.ModuleType("gi.repository.Nautilus")
    Nautilus.MenuProvider = type("MenuProvider", (), {})
    Nautilus.MenuItem = FakeMenuItem
    Nautilus.FileInfo = FakeFileInfo

    repository = types.ModuleType("gi.repository")
    repository.GObject = GObject
    repository.Gio = Gio
    repository.Nautilus = Nautilus

    gi = types.ModuleType("gi")
    gi.require_version = lambda *a, **k: None
    gi.repository = repository
    return {"gi": gi, "gi.repository": repository}


REAL_CLI = sys.executable  # stands in for the installed indir binary


def _load_extension(monkeypatch, cli=REAL_CLI):
    for name, module in _fake_modules().items():
        monkeypatch.setitem(sys.modules, name, module)
    FakeSubprocess.calls = []
    source = nautilus_extension.render_extension(cli, "indir")
    module = types.ModuleType("indir_nautilus")
    module.__dict__["__file__"] = str(TEMPLATE)
    exec(compile(source, str(TEMPLATE), "exec"), module.__dict__)
    return module


# ── template rendering ───────────────────────────────────────────────────────


def test_render_substitutes_cli_and_icon() -> None:
    source = nautilus_extension.render_extension("/opt/indir/.venv/bin/indir", "indir")
    assert 'CLI_PATH = "/opt/indir/.venv/bin/indir"' in source
    assert 'ICON = "indir"' in source
    # The docstring still names the placeholders; the assignments must be filled.
    assert f'CLI_PATH = "{nautilus_extension.CLI_PLACEHOLDER}"' not in source
    assert f'ICON = "{nautilus_extension.ICON_PLACEHOLDER}"' not in source


def test_render_stays_valid_python_for_awkward_paths() -> None:
    weird = '/home/me/My "Projects"/indir\\bin/indir'
    source = nautilus_extension.render_extension(weird, "indir")
    compile(source, "<rendered>", "exec")
    assert "My \\\"Projects\\\"" in source


def test_render_rejects_broken_template(tmp_path: Path) -> None:
    template = tmp_path / "broken.py.in"
    template.write_text('CLI_PATH = "__INDIR_CLI__"\ndef broken(:\n', encoding="utf-8")
    with pytest.raises(SyntaxError):
        nautilus_extension.render_extension("/opt/indir", "indir", template)


def test_shipped_template_keeps_placeholders() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert nautilus_extension.CLI_PLACEHOLDER in text
    assert "class InDirMenuProvider" in text


# ── install / remove ─────────────────────────────────────────────────────────


def test_install_writes_extension(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    dest = nautilus_extension.install_extension("/opt/indir", ext_dir=ext_dir)
    assert dest == ext_dir / nautilus_extension.EXTENSION_NAME
    text = dest.read_text(encoding="utf-8")
    assert 'CLI_PATH = "/opt/indir"' in text
    assert nautilus_extension.extension_installed(ext_dir) is True


def test_install_overwrites_stale_cli_path(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    nautilus_extension.install_extension("/old/indir", ext_dir=ext_dir)
    dest = nautilus_extension.install_extension("/new/indir", ext_dir=ext_dir)
    text = dest.read_text(encoding="utf-8")
    assert "/new/indir" in text
    assert "/old/indir" not in text
    assert len(list(ext_dir.glob("*.py"))) == 1


def test_remove_extension(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    nautilus_extension.install_extension("/opt/indir", ext_dir=ext_dir)
    assert nautilus_extension.remove_extension(ext_dir) is True
    assert nautilus_extension.extension_installed(ext_dir) is False
    assert nautilus_extension.remove_extension(ext_dir) is False


# ── menu provider behaviour ──────────────────────────────────────────────────


def test_single_selection_offers_indir(monkeypatch) -> None:
    ext = _load_extension(monkeypatch)
    provider = ext.InDirMenuProvider()
    items = provider.get_file_items([FakeFileInfo("/tmp/clip.webm")])
    assert len(items) == 1
    assert items[0].props["label"] == "InDir"
    assert items[0].props["name"] == ext.MENU_ITEM_ID
    assert items[0].props["icon"] == "indir"


def test_activate_launches_cli_with_path(monkeypatch) -> None:
    ext = _load_extension(monkeypatch, REAL_CLI)
    item = ext.InDirMenuProvider().get_file_items([FakeFileInfo("/tmp/a b")])[0]
    item.activate()
    assert FakeSubprocess.calls == [[REAL_CLI, "/tmp/a b"]]


def test_multi_selection_is_ignored(monkeypatch) -> None:
    ext = _load_extension(monkeypatch)
    provider = ext.InDirMenuProvider()
    assert provider.get_file_items([FakeFileInfo("/tmp/a"), FakeFileInfo("/tmp/b")]) == []


def test_remote_file_without_local_path_is_ignored(monkeypatch) -> None:
    ext = _load_extension(monkeypatch)
    provider = ext.InDirMenuProvider()
    assert provider.get_file_items([FakeFileInfo(None)]) == []


def test_background_selection_uses_current_folder(monkeypatch) -> None:
    ext = _load_extension(monkeypatch, REAL_CLI)
    items = ext.InDirMenuProvider().get_background_items(FakeFileInfo("/home/me/Downloads"))
    assert len(items) == 1
    items[0].activate()
    assert FakeSubprocess.calls == [[REAL_CLI, "/home/me/Downloads"]]


def test_missing_cli_hides_menu(monkeypatch) -> None:
    ext = _load_extension(monkeypatch, "/nonexistent/indir")
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("os.access", lambda path, mode: False)
    provider = ext.InDirMenuProvider()
    assert provider.get_file_items([FakeFileInfo("/tmp/a")]) == []
    assert provider.get_background_items(FakeFileInfo("/tmp")) == []


def test_cli_falls_back_to_path_lookup(monkeypatch) -> None:
    ext = _load_extension(monkeypatch, "/nonexistent/indir")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/indir" if name == "indir" else None)
    monkeypatch.setattr(
        "os.access", lambda path, mode: str(path) == "/usr/local/bin/indir"
    )
    items = ext.InDirMenuProvider().get_file_items([FakeFileInfo("/tmp/a")])
    assert len(items) == 1
    items[0].activate()
    assert FakeSubprocess.calls == [["/usr/local/bin/indir", "/tmp/a"]]


def test_legacy_window_and_files_call_shape(monkeypatch) -> None:
    """Nautilus < 43 passes (window, files); both shapes must work."""
    ext = _load_extension(monkeypatch, REAL_CLI)
    provider = ext.InDirMenuProvider()
    assert len(provider.get_file_items(object(), [FakeFileInfo("/tmp/a")])) == 1
    assert len(provider.get_background_items(object(), FakeFileInfo("/tmp"))) == 1


def test_zero_argument_call_is_total(monkeypatch) -> None:
    ext = _load_extension(monkeypatch, REAL_CLI)
    provider = ext.InDirMenuProvider()
    assert provider.get_file_items() == []
    assert provider.get_background_items() == []


def test_non_executable_baked_cli_hides_the_menu(monkeypatch, tmp_path: Path) -> None:
    stale = tmp_path / "indir"
    stale.write_text("#!/bin/sh\n", encoding="utf-8")
    stale.chmod(0o644)
    ext = _load_extension(monkeypatch, str(stale))
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert ext.InDirMenuProvider().get_file_items([FakeFileInfo("/tmp/a")]) == []


def test_relative_path_hit_is_refused(monkeypatch) -> None:
    """A bare ./indir from a relative PATH entry must never be executed."""
    ext = _load_extension(monkeypatch, "/nonexistent/indir")
    monkeypatch.setattr("os.access", lambda path, mode: False)
    monkeypatch.setattr("shutil.which", lambda name: "indir")
    assert ext.InDirMenuProvider().get_file_items([FakeFileInfo("/tmp/a")]) == []


def test_launch_failure_does_not_escape_the_handler(monkeypatch) -> None:
    """A deleted CLI must not raise out of the activate signal handler."""
    ext = _load_extension(monkeypatch, REAL_CLI)

    def boom(argv, flags=None):
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(ext.Gio.Subprocess, "new", boom)
    item = ext.InDirMenuProvider().get_file_items([FakeFileInfo("/tmp/a")])[0]
    item.activate()  # must not raise


def test_install_does_not_follow_a_planted_symlink(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("do not touch\n", encoding="utf-8")
    (ext_dir / nautilus_extension.EXTENSION_NAME).symlink_to(victim)

    dest = nautilus_extension.install_extension("/opt/indir", ext_dir=ext_dir)

    assert victim.read_text(encoding="utf-8") == "do not touch\n"
    assert dest.is_symlink() is False
    assert 'CLI_PATH = "/opt/indir"' in dest.read_text(encoding="utf-8")


def test_main_exit_codes(tmp_path: Path, capsys) -> None:
    ext_dir = tmp_path / "extensions"
    assert nautilus_extension.main(["check", "--dir", str(ext_dir)]) == 1
    assert nautilus_extension.main(["install", "--dir", str(ext_dir)]) == 2  # --cli missing
    assert "required" in capsys.readouterr().err
    assert nautilus_extension.main(["install", "--cli", "/opt/indir", "--dir", str(ext_dir)]) == 0
    assert nautilus_extension.main(["check", "--dir", str(ext_dir)]) == 0
    assert nautilus_extension.main(["remove", "--dir", str(ext_dir)]) == 0
    assert nautilus_extension.main(["remove", "--dir", str(ext_dir)]) == 0
