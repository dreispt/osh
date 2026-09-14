"""Tests for user plugin loading and subplugin discovery.

A user plugin directory can be a multi-plugin repository: each direct
subpackage declaring ``OSH_PLUGIN_MANIFEST`` is loaded as a plugin of its
own, so repos like osh-contrib need no aggregation code.
"""

import pytest

from osh.utils import plugin_loader


def _write_package(root, name, init_src):
    """Create a package dir with the given ``__init__.py`` source."""
    pkg = root / name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(init_src)
    return pkg


@pytest.fixture
def plugin_dir(tmp_path, monkeypatch):
    """A fake user plugin directory used by the plugin loader."""
    directory = tmp_path / "plugins"
    directory.mkdir()
    monkeypatch.setattr(plugin_loader, "user_plugin_dir", lambda: directory)
    return directory


def test_subpackages_load_as_plugins(plugin_dir):
    """Each subpackage with a manifest is a plugin; no root manifest needed."""
    repo = _write_package(plugin_dir, "repo_a", '"""Thin repo package."""\n')
    _write_package(
        repo,
        "osh_one",
        "import click\n\n"
        "@click.command(name='one-cmd')\n"
        "def one():\n"
        "    pass\n\n"
        "OSH_PLUGIN_MANIFEST = {'commands': [one]}\n",
    )
    _write_package(
        repo,
        "osh_two",
        "OSH_PLUGIN_MANIFEST = {'hooks': {'point': ['impl']}}\n",
    )

    commands = {cmd.name: src for src, cmd in plugin_loader.load_plugins()}
    assert commands["one-cmd"] == "osh-one"
    assert plugin_loader.load_hooks("point") == ["impl"]


def test_root_manifest_and_subplugins_both_load(plugin_dir):
    """A root package with its own manifest still contributes it."""
    repo = _write_package(
        plugin_dir,
        "repo_b",
        "OSH_PLUGIN_MANIFEST = {'hooks': {'point': ['root']}}\n",
    )
    _write_package(
        repo,
        "osh_sub",
        "OSH_PLUGIN_MANIFEST = {'hooks': {'point': ['sub']}}\n",
    )

    assert sorted(plugin_loader.load_hooks("point")) == ["root", "sub"]


def test_subplugin_relative_imports_work(plugin_dir):
    """Subplugin packages are real packages, so relative imports resolve."""
    repo = _write_package(plugin_dir, "repo_c", "")
    sub = _write_package(
        repo,
        "osh_rel",
        "from .helper import VALUE\n"
        "OSH_PLUGIN_MANIFEST = {'hooks': {'point': [VALUE]}}\n",
    )
    (sub / "helper.py").write_text("VALUE = 42\n")

    assert plugin_loader.load_hooks("point") == [42]


def test_broken_subplugin_warns_and_others_load(plugin_dir, capsys):
    """A subpackage failing to import warns; the rest still load."""
    repo = _write_package(plugin_dir, "repo_d", "")
    _write_package(repo, "osh_broken", "1/0\n")
    _write_package(
        repo,
        "osh_fine",
        "OSH_PLUGIN_MANIFEST = {'hooks': {'point': ['ok']}}\n",
    )

    assert plugin_loader.load_hooks("point") == ["ok"]
    assert "Could not load plugin" in capsys.readouterr().err


def test_non_package_dirs_ignored(plugin_dir, capsys):
    """Dirs without ``__init__.py`` are skipped silently."""
    repo = _write_package(plugin_dir, "repo_e", "")
    (repo / "docs").mkdir()
    (repo / "not-python-dir").mkdir()
    (repo / "not-python-dir" / "__init__.py").write_text("")
    _write_package(
        repo,
        "osh_ok",
        "OSH_PLUGIN_MANIFEST = {'hooks': {'point': ['ok']}}\n",
    )

    assert plugin_loader.load_hooks("point") == ["ok"]
    assert capsys.readouterr().err == ""


def test_single_file_plugin_still_loads(plugin_dir):
    """A ``osh_plugin.py`` file plugin keeps working (no subplugin scan)."""
    (plugin_dir / "repo_f").mkdir()
    (plugin_dir / "repo_f" / "osh_plugin.py").write_text(
        "OSH_PLUGIN_MANIFEST = {'hooks': {'point': ['single']}}\n"
    )

    assert plugin_loader.load_hooks("point") == ["single"]


def test_bare_repo_dir_loads_subplugins(plugin_dir):
    """A dir without ``__init__.py`` is an addons-style repo of plugins."""
    repo = plugin_dir / "repo_g"
    repo.mkdir()
    _write_package(
        repo,
        "osh_sub",
        "import click\n\n"
        "@click.command(name='sub-cmd')\n"
        "def sub():\n"
        "    pass\n\n"
        "OSH_PLUGIN_MANIFEST = {'commands': [sub], 'hooks': {'point': ['x']}}\n",
    )

    commands = {cmd.name: src for src, cmd in plugin_loader.load_plugins()}
    assert commands["sub-cmd"] == "osh-sub"
    assert plugin_loader.load_hooks("point") == ["x"]


def test_bare_repo_ignores_non_packages(plugin_dir, capsys):
    """Non-package dirs inside a bare repo are skipped without warnings."""
    repo = plugin_dir / "repo_h"
    repo.mkdir()
    (repo / "docs").mkdir()
    (repo / "my-addon").mkdir()  # not a valid module name
    (repo / "my-addon" / "__init__.py").write_text("")
    _write_package(repo, "osh_ok", "OSH_PLUGIN_MANIFEST = {'hooks': {'p': ['y']}}\n")

    assert plugin_loader.load_hooks("p") == ["y"]
    assert capsys.readouterr().err == ""


def test_bare_repo_without_plugins_loads_nothing(plugin_dir, capsys):
    """A dir that is neither a plugin nor a repo of plugins is ignored."""
    (plugin_dir / "repo_i").mkdir()
    (plugin_dir / "repo_i" / "README.md").write_text("# not a plugin\n")

    assert plugin_loader.load_hooks("point") == []
    assert capsys.readouterr().err == ""


def test_disabled_subplugins_are_not_imported(plugin_dir, monkeypatch, tmp_path):
    """Disabled subplugins are filtered before import — code never runs."""
    marker = tmp_path / "imported"
    repo = _write_package(
        plugin_dir,
        "repo_j",
        "OSH_PLUGIN_MANIFEST = {'hooks': {'point': ['root']}}\n",
    )
    _write_package(
        repo,
        "osh_on",
        "OSH_PLUGIN_MANIFEST = {'hooks': {'point': ['on']}}\n",
    )
    _write_package(
        repo,
        "osh_off",
        f"import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('ran')\n"
        f"OSH_PLUGIN_MANIFEST = {{'hooks': {{'point': ['off']}}}}\n",
    )
    monkeypatch.setattr(
        plugin_loader,
        "get_enabled_plugins",
        lambda source: ["repo-j", "osh-on"] if source == "repo_j" else None,
    )

    assert sorted(plugin_loader.load_hooks("point")) == ["on", "root"]
    assert not marker.exists()


def test_broken_plugin_leaves_no_sys_modules_entry(plugin_dir, capsys):
    """A plugin that fails to import is removed from ``sys.modules``."""
    import sys

    _write_package(plugin_dir, "repo_k", "1/0\n")

    assert plugin_loader.load_hooks("point") == []
    assert "Could not load user plugin" in capsys.readouterr().err
    assert "osh_user_plugin_repo_k" not in sys.modules


def test_user_plugin_backends_and_sources(plugin_dir, capsys):
    """User plugins can contribute backends, sources and group commands."""
    _write_package(
        plugin_dir,
        "repo_l",
        "import click\n"
        "from osh.backends import Backend\n"
        "from osh.backup_sources import BackupSource\n\n"
        "class MyBackend(Backend):\n"
        "    name = 'mybackend'\n"
        "    backend_type = 'backend'\n\n"
        "class MySource(BackupSource):\n"
        "    scheme = 'myscheme'\n\n"
        "@click.command(name='mysub')\n"
        "def mysub():\n"
        "    pass\n\n"
        "OSH_PLUGIN_MANIFEST = {\n"
        "    'backends': [MyBackend],\n"
        "    'hooks': {'osh_db_get.sources': [MySource]},\n"
        "    'group_commands': {'db': [mysub]},\n"
        "}\n",
    )

    assert "mybackend" in plugin_loader.load_backends()
    entries = plugin_loader.load_hook_entries("osh_db_get.sources")
    assert any(
        s == "repo-l" and getattr(i, "scheme", None) == "myscheme" for s, i in entries
    )
    groups = plugin_loader.load_group_commands()
    assert any(c.name == "mysub" for _s, c in groups["db"])
    assert capsys.readouterr().err == ""


def test_backend_name_collision_is_skipped(plugin_dir, capsys):
    """A second backend with the same name is skipped with an error."""
    src = (
        "from osh.backends import Backend\n\n"
        "class NoneAgain(Backend):\n"
        "    name = 'none'\n"
        "    backend_type = 'backend'\n\n"
        "OSH_PLUGIN_MANIFEST = {'backends': [NoneAgain]}\n"
    )
    _write_package(plugin_dir, "repo_m", src)

    backends = plugin_loader.load_backends()
    assert backends["none"].__module__ == "osh.backends"
    assert "conflicts" in capsys.readouterr().err


def test_backup_source_scheme_collision_is_skipped(plugin_dir, capsys):
    """A second backup source with the same scheme is skipped with an error."""
    src = (
        "from osh.backup_sources import BackupSource\n\n"
        "class DbAgain(BackupSource):\n"
        "    scheme = 'db'\n\n"
        "OSH_PLUGIN_MANIFEST = {'hooks': {'osh_db_get.sources': [DbAgain]}}\n"
    )
    _write_package(plugin_dir, "repo_n", src)

    from osh.plugins.osh_db_get import registry

    registry._SOURCE_REGISTRY = None
    try:
        sources = registry._source_registry()
    finally:
        registry._SOURCE_REGISTRY = None

    assert sources["db"].__module__.startswith("osh.plugins.osh_db_get.")
    assert "conflicts" in capsys.readouterr().err
