"""Tests for user plugin loading and subplugin discovery.

A user plugin directory can be a multi-plugin repository: each direct
subpackage marked with ``osh-plugin.toml`` registers as a plugin of its
own, so repos like osh-contrib need no aggregation code.

Fake plugin trees are static fixtures under ``tests/plugins/`` — each
test copies the ones it needs into a temporary user plugin dir.
"""

import shutil
from pathlib import Path

import pytest

from osh.backup_sources import BackupSource
from osh.utils import plugin_loader, plugin_registry

PLUGINS_DATA = Path(__file__).parent / "plugins"


def _copy_plugin(plugin_dir, name, dest=None):
    """Copy the static *name* plugin tree into the fake user plugin dir."""
    shutil.copytree(PLUGINS_DATA / name, plugin_dir / (dest or name))


@pytest.fixture
def plugin_dir(tmp_path, monkeypatch):
    """A fake user plugin directory used by the plugin loader."""
    directory = tmp_path / "plugins"
    directory.mkdir()
    monkeypatch.setattr(plugin_registry, "user_plugin_dir", lambda: directory)
    return directory


def test_subpackages_load_as_plugins(plugin_dir):
    """Each marked subpackage is a plugin; no root marker needed."""
    _copy_plugin(plugin_dir, "repo_a")

    specs = plugin_registry.plugin_registry().specs
    assert specs["osh-one"].lazy and specs["osh-two"].lazy
    commands = {cmd.name: src for src, cmd in plugin_loader.load_plugins()}
    assert commands["one_cmd"] == "osh-one"


def test_root_plugin_and_subplugins_both_load(plugin_dir):
    """A root package marked with its own toml still registers."""
    _copy_plugin(plugin_dir, "repo_b")

    specs = plugin_registry.plugin_registry().specs
    assert specs["repo-b"].lazy and specs["osh-sub"].lazy


def test_subplugin_relative_imports_work(plugin_dir):
    """Subplugin packages are real packages, so relative imports resolve."""
    _copy_plugin(plugin_dir, "repo_c")

    module = plugin_registry.plugin_registry().specs["osh-rel"].load()
    assert module.Ext.v == 42


def test_broken_subplugin_errors_and_others_load(plugin_dir):
    """A marked subpackage failing to import errors on use; the rest load."""
    from click.testing import CliRunner

    _copy_plugin(plugin_dir, "repo_d")

    commands = {cmd.name: cmd for _src, cmd in plugin_loader.load_plugins()}
    result = CliRunner().invoke(commands["bad"], [])
    assert result.exit_code != 0
    assert "Could not load plugin 'osh-broken' command 'bad'" in result.output

    spec = plugin_registry.plugin_registry().specs["osh-fine"]
    assert spec.resolve_command(None, "fine") is not None


def test_non_package_dirs_ignored(plugin_dir, capsys):
    """Dirs without ``__init__.py`` or a marker are skipped silently."""
    _copy_plugin(plugin_dir, "repo_e")

    specs = plugin_registry.plugin_registry().specs
    assert "osh-ok" in specs
    assert "docs" not in specs
    assert "not-python-dir" not in specs
    assert capsys.readouterr().err == ""


def test_single_file_plugin_still_loads(plugin_dir):
    """A bare ``osh_plugin.py`` file plugin loads eagerly, unmarked."""
    _copy_plugin(plugin_dir, "repo_f")

    commands = {cmd.name: src for src, cmd in plugin_loader.load_plugins()}
    assert commands["single-cmd"] == "repo-f"


def test_bare_repo_dir_loads_subplugins(plugin_dir):
    """A dir without ``__init__.py`` is an addons-style repo of plugins."""
    _copy_plugin(plugin_dir, "repo_g")

    commands = {cmd.name: src for src, cmd in plugin_loader.load_plugins()}
    assert commands["sub_cmd"] == "osh-sub"

    # Composing `osh db list`'s handler imports the `extends` declarer.
    from osh.commands.db_cmd import db

    effective = db.commands["list"]._handler_cls()
    assert "Ext" in [c.__name__ for c in effective.__mro__]


def test_bare_repo_ignores_non_packages(plugin_dir, capsys):
    """Non-package dirs inside a bare repo are skipped without warnings."""
    _copy_plugin(plugin_dir, "repo_h")

    specs = plugin_registry.plugin_registry().specs
    assert "osh-ok" in specs
    assert "my-addon" not in specs and "my_addon" not in specs
    assert capsys.readouterr().err == ""


def test_bare_repo_without_plugins_loads_nothing(plugin_dir, capsys):
    """A dir that is neither a plugin nor a repo of plugins is ignored."""
    _copy_plugin(plugin_dir, "repo_i")

    plugin_loader.load_plugins()
    assert "repo-i" not in plugin_registry.plugin_registry().specs
    assert capsys.readouterr().err == ""


def test_disabled_subplugins_are_not_imported(plugin_dir, monkeypatch):
    """Disabled subplugins are filtered at discovery — code never runs."""
    _copy_plugin(plugin_dir, "repo_j")
    monkeypatch.setattr(
        plugin_registry,
        "get_enabled_plugins",
        lambda source: ["repo-j", "osh-on"] if source == "repo_j" else None,
    )

    specs = plugin_registry.plugin_registry().specs
    assert "repo-j" in specs and "osh-on" in specs
    assert "osh-off" not in specs

    specs["repo-j"].load()
    specs["osh-on"].load()
    assert not (plugin_dir / "repo_j" / "osh_off" / "imported").exists()


def test_broken_plugin_leaves_no_sys_modules_entry(plugin_dir, capsys):
    """A plugin that fails to import is removed from ``sys.modules``."""
    import sys

    _copy_plugin(plugin_dir, "repo_k")

    plugin_loader.load_plugins()
    assert "Could not load plugin 'repo-k'" in capsys.readouterr().err
    assert "osh_user_plugin_repo_k" not in sys.modules


def test_user_plugin_backends_and_sources(plugin_dir, capsys):
    """User plugins can contribute backends, sources and group commands."""
    _copy_plugin(plugin_dir, "repo_l")

    assert "mybackend" in plugin_loader.load_backends()
    entries = plugin_loader.iter_plugin_subclasses(BackupSource)
    assert any(
        s == "repo-l" and getattr(i, "scheme", None) == "myscheme" for s, i in entries
    )
    groups = plugin_loader.load_group_commands()
    assert any(c.name == "mysub" for _s, c in groups["db"])

    # Backend lifecycle commands are ordinary group subcommands — a lazy
    # stub until resolved.
    from osh.cli_utils import LazyCommand

    ((src, stop),) = (e for e in groups["mybackend"] if e[1].name == "stop")
    assert src == "repo-l" and isinstance(stop, LazyCommand)

    spec = plugin_registry.plugin_registry().specs["repo-l"]
    assert spec.resolve_command("db", "mysub").name == "mysub"
    assert spec.resolve_command("mybackend", "stop").name == "stop"
    assert capsys.readouterr().err == ""


def test_backend_name_collision_is_skipped(plugin_dir, capsys):
    """A second backend with the same name is skipped with an error."""
    _copy_plugin(plugin_dir, "repo_m")

    backends = plugin_loader.load_backends()
    assert backends["none"].__module__ == "osh.backends"
    assert "conflicts" in capsys.readouterr().err


def test_backup_source_scheme_collision_is_skipped(plugin_dir, capsys):
    """A second backup source with the same scheme is skipped with an error."""
    _copy_plugin(plugin_dir, "repo_n")

    from osh.plugins.osh_db_get import registry

    registry._SOURCE_REGISTRY = None
    try:
        sources = registry._source_registry()
    finally:
        registry._SOURCE_REGISTRY = None

    assert sources["db"].__module__.startswith("osh.plugins.osh_db_get.")
    assert "conflicts" in capsys.readouterr().err


# Two-stage lazy loading ----------------------------------------------------


def test_load_plugins_registers_lazy_stubs_without_import(plugin_dir):
    """Declared commands register as stubs — the module is never imported."""
    _copy_plugin(plugin_dir, "repo_lazy")

    commands = {cmd.name: cmd for _src, cmd in plugin_loader.load_plugins()}

    from osh.cli_utils import LazyCommand

    assert isinstance(commands["marked"], LazyCommand)
    spec = plugin_loader.plugin_registry().specs["osh-marked"]
    assert spec.lazy
    assert not spec.loaded


def test_lazy_command_loads_and_delegates_args(plugin_dir):
    """Invoking a lazy command imports the plugin and runs it with the args."""
    _copy_plugin(plugin_dir, "repo_echo")

    from click.testing import CliRunner

    commands = {cmd.name: cmd for _src, cmd in plugin_loader.load_plugins()}
    spec = plugin_loader.plugin_registry().specs["osh-echo"]
    assert not spec.loaded

    result = CliRunner().invoke(commands["echo"], ["hello"])

    assert result.exit_code == 0, result.output
    ran = plugin_dir / "repo_echo" / "osh_echo" / "ran.txt"
    assert ran.read_text() == "hello"
    assert spec.loaded


def test_spec_load_not_called_for_help(plugin_dir, monkeypatch):
    """Rendering help lists the command without calling ``spec.load()``."""
    _copy_plugin(plugin_dir, "repo_help")

    from click.testing import CliRunner

    spec = plugin_loader.plugin_registry().specs["osh-helped"]
    calls = []
    monkeypatch.setattr(spec, "load", lambda: calls.append(1))

    group = plugin_loader.click.Group()
    for _src, cmd in plugin_loader.load_plugins():
        group.add_command(cmd)

    result = CliRunner().invoke(group, ["--help"])

    assert "helped" in result.output
    assert "Helpy command." in result.output
    assert calls == []
    assert not spec.loaded


def test_hidden_declared_command_is_not_listed(plugin_dir):
    """``hidden = true`` declarations register hidden lazy stubs."""
    _copy_plugin(plugin_dir, "repo_help")

    from click.testing import CliRunner

    commands = {cmd.name: cmd for _src, cmd in plugin_loader.load_plugins()}

    assert commands["secret"].hidden
    assert not commands["helped"].hidden

    group = plugin_loader.click.Group()
    for cmd in commands.values():
        group.add_command(cmd)
    result = CliRunner().invoke(group, ["--help"])
    assert "helped" in result.output
    assert "secret" not in result.output


def test_lazy_command_import_error_is_reported(plugin_dir):
    """A plugin failing to import reports a clean error at invocation time."""
    _copy_plugin(plugin_dir, "repo_bad")

    from click.testing import CliRunner

    commands = {cmd.name: cmd for _src, cmd in plugin_loader.load_plugins()}
    result = CliRunner().invoke(commands["bad"], [])

    assert result.exit_code != 0
    assert "Could not load plugin 'osh-bad' command 'bad'" in result.output
    assert "nonexistent_package_xyz" in result.output


def test_lazy_backend_imports_only_its_plugin(plugin_dir):
    """``get_backend_class`` imports only the plugin declaring the backend."""
    _copy_plugin(plugin_dir, "repo_backends")

    cls = plugin_loader.get_backend_class("a-backend")

    assert cls.__name__ == "A"
    specs = plugin_loader.plugin_registry().specs
    assert specs["osh-backend-a"].loaded
    assert not specs["osh-backend-b"].loaded


def test_entry_point_spec_registers_without_load(plugin_dir, monkeypatch):
    """Entry-point plugins register specs from metadata — no ``ep.load()``."""
    import sys
    import types

    fake_module = types.ModuleType("fake_lazy_plugin")
    seen = []

    def main(argv):
        seen.extend(argv)

    fake_module.main = main
    monkeypatch.setitem(sys.modules, "fake_lazy_plugin", fake_module)

    ep = types.SimpleNamespace(name="echo", value="fake_lazy_plugin:main")
    monkeypatch.setattr(plugin_registry, "_iter_entry_points", lambda *a, **kw: [ep])

    spec = plugin_loader.plugin_registry().specs["echo"]
    assert spec.lazy
    assert spec.target_ref == "fake_lazy_plugin:main"
    assert spec.declared_commands() == {"echo": ""}
    assert not spec.loaded

    # Invocation triggers stage 2 and delegates the remaining argv.
    from click.testing import CliRunner

    commands = {cmd.name: cmd for _src, cmd in plugin_loader.load_plugins()}
    result = CliRunner().invoke(commands["echo"], ["one", "two"])

    assert result.exit_code == 0, result.output
    assert seen == ["one", "two"]


def test_entry_point_with_marker_declares_no_implicit_command(plugin_dir, monkeypatch):
    """A marked entry-point plugin without ``[commands]`` gets no stub."""
    import sys
    import types

    fake_module = types.ModuleType("fake_marked_plugin")
    monkeypatch.setitem(sys.modules, "fake_marked_plugin", fake_module)
    monkeypatch.setattr(
        plugin_registry, "_ep_meta", lambda _name: {"extends": ["odoo"]}
    )

    ep = types.SimpleNamespace(name="echo", value="fake_marked_plugin")
    monkeypatch.setattr(plugin_registry, "_iter_entry_points", lambda *a, **kw: [ep])

    spec = plugin_loader.plugin_registry().specs["echo"]
    assert spec.lazy
    assert spec.declared_commands() == {}


def test_lazy_subclass_extends_parent_handler(plugin_dir):
    """A subclass without ``_cli_name`` extends its nearest named ancestor.

    The ``extends = ["db.list"]`` declaration is the lazy trigger —
    composing the ``db list`` command's handler imports the plugin.
    """
    _copy_plugin(plugin_dir, "repo_sub")

    from osh.commands.db_cmd import db

    effective = db.commands["list"]._handler_cls()
    mro_names = [c.__name__ for c in effective.__mro__]
    assert "Filestores" in mro_names
    specs = plugin_registry.plugin_registry().specs
    assert specs["osh-subext"].loaded
    assert not specs["osh-unrelated"].loaded


def test_lazy_handlers_key_resolves_named_handler(plugin_dir):
    """``handlers`` in toml lets ``resolve()`` find non-CLI named handlers."""
    _copy_plugin(plugin_dir, "repo_handlers")

    from osh.handlers import resolve

    cls = resolve("_util.fmt")
    assert cls.__name__ == "Fmt"
    assert plugin_registry.plugin_registry().specs["osh-helper"].loaded


def test_derived_handler_is_a_new_command(plugin_dir):
    """A subclass with its own ``_cli_name`` is a command, not an extender."""
    _copy_plugin(plugin_dir, "repo_derived")

    from osh.commands.db_cmd import Db
    from osh.handlers import resolve

    # The derived handler resolves to itself as a distinct named handler…
    cls = resolve("db.smart_list")
    assert cls.__name__ == "SmartList"
    # …and does not extend its parent once loaded.
    assert not issubclass(Db.effective(), cls)


def test_depends_imports_dependency_first(plugin_dir):
    """``depends`` plugins are imported before the dependent's module."""
    _copy_plugin(plugin_dir, "repo_deps")

    spec = plugin_registry.plugin_registry().specs["osh-needy"]
    spec.load()

    specs = plugin_registry.plugin_registry().specs
    assert specs["osh-base"].loaded
    assert specs["osh-needy"].loaded


def test_depends_missing_plugin_fails_load(plugin_dir):
    """A ``depends`` entry naming an unknown plugin fails the load clearly."""
    _copy_plugin(plugin_dir, "repo_dep_missing")

    spec = plugin_registry.plugin_registry().specs["osh-needy"]
    with pytest.raises(RuntimeError, match="osh-absent"):
        spec.load()
    assert spec.loaded


def test_depends_cycle_is_reported(plugin_dir):
    """Circular ``depends`` declarations fail instead of recursing forever."""
    _copy_plugin(plugin_dir, "repo_dep_cycle")

    spec = plugin_registry.plugin_registry().specs["osh-cyc-a"]
    with pytest.raises(RuntimeError, match="circular"):
        spec.load()


def test_warn_unresolved_meta_reports_missing_refs(plugin_dir, capsys):
    """Startup validation warns about extends/depends nothing provides."""
    _copy_plugin(plugin_dir, "repo_meta_bad")

    plugin_loader.warn_unresolved_meta()

    err = capsys.readouterr().err
    assert "osh-orphan" in err
    assert "no.such" in err
    assert "osh-absent" in err
    # Runs once per registry.
    plugin_loader.warn_unresolved_meta()
    assert capsys.readouterr().err == ""


def test_warn_unresolved_meta_quiet_when_refs_exist(plugin_dir, capsys):
    """Declared commands satisfy ``extends``; registered plugins ``depends``."""
    _copy_plugin(plugin_dir, "repo_meta_ok")

    plugin_loader.warn_unresolved_meta()

    err = capsys.readouterr().err
    assert "osh-extok" not in err
    assert "osh-prov" not in err
