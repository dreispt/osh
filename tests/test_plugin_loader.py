"""Tests for plugin loading via installed Python distributions.

Plugins are ordinary Python packages: ``pip install`` places the package
and a ``.dist-info`` directory on ``sys.path``, and the distribution
declares its plugins as ``osh.plugins`` entry points. These tests run
the real thing — fixture projects under ``tests/plugins/`` are built
into wheels and ``pip install``ed into the test's site dir — so the
``importlib.metadata`` discovery path is exercised end to end.
"""

import pytest

from osh.backup_sources import BackupSource
from osh.utils import plugin_loader, plugin_registry

from .helpers import PLUGINS_DATA


def test_each_entry_point_is_a_plugin(pip_install):
    """One distribution can ship several plugins — one per entry point."""
    pip_install("repo_a")

    specs = plugin_registry.plugin_registry().specs
    assert specs["osh-one"].lazy and specs["osh-two"].lazy
    commands = {cmd.name: src for src, cmd in plugin_loader.load_plugins()}
    assert commands["one_cmd"] == "osh-one"


def test_builtin_plugins_inherit_osh_version():
    """Builtin specs inherit the osh package version — no toml needed."""
    import osh

    spec = plugin_registry.plugin_registry().specs["osh-backup"]
    assert spec.kind == "builtin"
    assert spec.version == osh.__version__


def test_plugin_version_comes_from_distribution(pip_install):
    """A plugin's spec version is the installed distribution's version."""
    pip_install("plug_src")

    spec = plugin_registry.plugin_registry().specs["my-plugin"]
    assert spec.version == "1.2.3"


def test_min_osh_blocks_incompatible_plugin(pip_install):
    """A plugin declaring ``min_osh`` newer than osh fails to load."""
    pip_install("repo_min")

    spec = plugin_registry.plugin_registry().specs["osh-future"]
    with pytest.raises(RuntimeError, match="requires osh >= 99.0"):
        spec.load()


def test_min_osh_satisfied_plugin_loads(pip_install):
    """A plugin whose ``min_osh`` is met loads and resolves commands."""
    pip_install("repo_min")

    spec = plugin_registry.plugin_registry().specs["osh-okmin"]
    assert spec.resolve_command(None, "okmin_cmd") is not None


def test_min_osh_warns_once(pip_install, capsys):
    """An unmet ``min_osh`` warns at startup without importing the plugin."""
    pip_install("repo_min")

    plugin_loader.warn_unresolved_meta()
    err = capsys.readouterr().err
    assert "plugin 'osh-future' requires osh >= 99.0" in err
    assert "osh-okmin" not in err
    assert not plugin_registry.plugin_registry().specs["osh-future"].loaded


def test_min_osh_unparsable_warns_not_blocks(pip_install, capsys):
    """An unparsable ``min_osh`` warns but does not block the plugin."""
    pip_install("repo_badmin")

    plugin_loader.warn_unresolved_meta()
    err = capsys.readouterr().err
    assert "plugin 'osh-badmin' declares min_osh='banana'" in err
    spec = plugin_registry.plugin_registry().specs["osh-badmin"]
    assert spec.resolve_command(None, "bad_cmd") is not None


def test_relative_imports_resolve_in_installed_packages(pip_install):
    """Installed plugins are real packages, so relative imports resolve."""
    pip_install("repo_c")

    module = plugin_registry.plugin_registry().specs["osh-rel"].load()
    assert module.Ext.v == 42


def test_broken_plugin_errors_and_others_load(pip_install):
    """A plugin failing to import errors on use; the rest still load."""
    from click.testing import CliRunner

    pip_install("repo_d")

    commands = {cmd.name: cmd for _src, cmd in plugin_loader.load_plugins()}
    result = CliRunner().invoke(commands["bad"], [])
    assert result.exit_code != 0
    assert "Could not load plugin 'osh-broken' command 'bad'" in result.output

    spec = plugin_registry.plugin_registry().specs["osh-fine"]
    assert spec.resolve_command(None, "fine") is not None


def test_unmarked_plugin_loads_eagerly(pip_install):
    """An entry point with no declarations and no ``:attr`` imports eagerly."""
    pip_install("fake_unique")

    commands = {cmd.name: src for src, cmd in plugin_loader.load_plugins()}
    assert commands["unique"] == "fake"


def test_tool_osh_in_pyproject_declares_commands(pip_install):
    """``[tool.osh]`` in the package's ``pyproject.toml`` declares lazily."""
    pip_install("repo_scan")

    commands = {cmd.name: cmd for _src, cmd in plugin_loader.load_plugins()}

    from osh.cli_utils import LazyCommand

    assert isinstance(commands["scan"], LazyCommand)
    spec = plugin_loader.plugin_registry().specs["osh-scan"]
    assert spec.lazy
    assert not spec.loaded


def test_tool_osh_takes_precedence_over_marker(pip_install):
    """``[tool.osh]`` wins over a legacy ``osh-plugin.toml`` marker."""
    pip_install("repo_prec")

    spec = plugin_registry.plugin_registry().specs["osh-prec"]
    assert spec.declared_commands() == {"helped": "From pyproject."}


def test_tool_osh_found_in_project_root(pip_install, monkeypatch):
    """Editable installs: ``pyproject.toml`` stays beside the package.

    ``pip install -e`` leaves the source tree on ``sys.path`` — only
    ``.dist-info`` lands in site-packages — so the project file sits one
    level up from the package. That is a wheel install for the
    ``.dist-info`` plus the fixture checkout prepended to ``sys.path``.
    """
    pip_install("repo_flat")
    monkeypatch.syspath_prepend(str(PLUGINS_DATA / "repo_flat"))

    spec = plugin_loader.plugin_registry().specs["osh-marked"]
    assert spec.lazy
    assert spec.declared_commands() == {"marked": "A marked command."}


def test_marker_fallback_loads_with_deprecation_warning(pip_install, capsys):
    """A marker-only plugin still loads, but startup warns it is deprecated."""
    pip_install("repo_legacy")

    spec = plugin_registry.plugin_registry().specs["osh-old"]
    assert spec.declared_commands() == {"old_cmd": "Old school."}

    plugin_loader.warn_unresolved_meta()
    assert "osh-plugin.toml" in capsys.readouterr().err


def test_installed_plugin_runtimes_and_sources(pip_install, capsys):
    """Plugins can contribute runtimes, sources and group commands."""
    pip_install("repo_l")

    assert "mybackend" in plugin_loader.load_runtimes()
    entries = plugin_loader.iter_plugin_subclasses(BackupSource)
    assert any(
        s == "repo-l" and getattr(i, "scheme", None) == "myscheme" for s, i in entries
    )
    groups = plugin_loader.load_group_commands()
    assert any(c.name == "mysub" for _s, c in groups["db"])

    # Runtime lifecycle commands are ordinary group subcommands — a lazy
    # stub until resolved.
    from osh.cli_utils import LazyCommand

    ((src, stop),) = (e for e in groups["mybackend"] if e[1].name == "stop")
    assert src == "repo-l" and isinstance(stop, LazyCommand)

    spec = plugin_registry.plugin_registry().specs["repo-l"]
    assert spec.resolve_command("db", "mysub").name == "mysub"
    assert spec.resolve_command("mybackend", "stop").name == "stop"
    assert capsys.readouterr().err == ""


def test_runtime_name_collision_is_skipped(pip_install, capsys):
    """A runtime named like the built-in host runtime is skipped."""
    pip_install("repo_m")

    runtimes = plugin_loader.load_runtimes()
    assert runtimes["host"].__module__ == "osh.runtimes"
    assert "none" not in runtimes
    assert "conflicts" in capsys.readouterr().err


def test_backup_source_scheme_collision_is_skipped(pip_install, capsys):
    """A second backup source with the same scheme is skipped with an error."""
    pip_install("repo_n")

    from osh.plugins.osh_backup import registry

    registry._SOURCE_REGISTRY = None
    try:
        sources = registry._source_registry()
    finally:
        registry._SOURCE_REGISTRY = None

    assert sources["db"].__module__.startswith("osh.plugins.osh_backup.")
    assert "conflicts" in capsys.readouterr().err


# Two-stage lazy loading ----------------------------------------------------


def test_load_plugins_registers_lazy_stubs_without_import(pip_install):
    """Declared commands register as stubs — the module is never imported."""
    pip_install("repo_lazy")

    commands = {cmd.name: cmd for _src, cmd in plugin_loader.load_plugins()}

    from osh.cli_utils import LazyCommand

    assert isinstance(commands["marked"], LazyCommand)
    spec = plugin_loader.plugin_registry().specs["osh-marked"]
    assert spec.lazy
    assert not spec.loaded


def test_lazy_command_loads_and_delegates_args(site_dir, pip_install):
    """Invoking a lazy command imports the plugin and runs it with the args."""
    pip_install("repo_echo")

    from click.testing import CliRunner

    commands = {cmd.name: cmd for _src, cmd in plugin_loader.load_plugins()}
    spec = plugin_loader.plugin_registry().specs["osh-echo"]
    assert not spec.loaded

    result = CliRunner().invoke(commands["echo"], ["hello"])

    assert result.exit_code == 0, result.output
    ran = site_dir / "osh_echo" / "ran.txt"
    assert ran.read_text() == "hello"
    assert spec.loaded


def test_spec_load_not_called_for_help(pip_install):
    """Rendering help lists the command without importing the plugin."""
    pip_install("repo_help")

    import click
    from click.testing import CliRunner

    spec = plugin_loader.plugin_registry().specs["osh-helped"]

    group = click.Group()
    for _src, cmd in plugin_loader.load_plugins():
        group.add_command(cmd)

    result = CliRunner().invoke(group, ["--help"])

    assert "helped" in result.output
    assert "Helpy command." in result.output
    assert not spec.loaded


def test_hidden_declared_command_is_not_listed(pip_install):
    """``hidden = true`` declarations register hidden lazy stubs."""
    pip_install("repo_help")

    from click.testing import CliRunner

    commands = {cmd.name: cmd for _src, cmd in plugin_loader.load_plugins()}

    assert commands["secret"].hidden
    assert not commands["helped"].hidden

    import click

    group = click.Group()
    for cmd in commands.values():
        group.add_command(cmd)
    result = CliRunner().invoke(group, ["--help"])
    assert "helped" in result.output
    assert "secret" not in result.output


def test_lazy_command_import_error_is_reported(pip_install):
    """A plugin failing to import reports a clean error at invocation time."""
    pip_install("repo_bad")

    from click.testing import CliRunner

    commands = {cmd.name: cmd for _src, cmd in plugin_loader.load_plugins()}
    result = CliRunner().invoke(commands["bad"], [])

    assert result.exit_code != 0
    assert "Could not load plugin 'osh-bad' command 'bad'" in result.output
    assert "nonexistent_package_xyz" in result.output


def test_lazy_runtime_imports_only_its_plugin(pip_install):
    """``get_runtime_class`` imports only the plugin declaring the runtime."""
    pip_install("repo_backends")

    cls = plugin_loader.get_runtime_class("a-backend")

    assert cls.__name__ == "A"
    specs = plugin_loader.plugin_registry().specs
    assert specs["osh-backend-a"].loaded
    assert not specs["osh-backend-b"].loaded


def test_entry_point_attr_target_registers_without_load(pip_install):
    """A ``module:attr`` entry point resolves lazily — no import at listing."""
    pip_install("repo_attr")

    spec = plugin_registry.plugin_registry().specs["echo"]
    assert spec.lazy
    assert spec.target_ref == "osh_attr:main"
    assert spec.declared_commands() == {"echo": ""}
    assert not spec.loaded

    # Invocation triggers stage 2 and resolves the module attribute.
    from click.testing import CliRunner

    commands = {cmd.name: cmd for _src, cmd in plugin_loader.load_plugins()}
    result = CliRunner().invoke(commands["echo"], [])

    assert result.exit_code == 0, result.output
    import sys

    assert sys.modules["osh_attr"].seen == ["called"]


def test_entry_point_with_tool_osh_declares_no_implicit_command(pip_install):
    """An entry-point plugin declaring only ``extends`` gets no stub."""
    pip_install("repo_extonly")

    spec = plugin_registry.plugin_registry().specs["osh-extonly"]
    assert spec.lazy
    assert spec.declared_commands() == {}


def test_lazy_subclass_extends_parent_handler(pip_install):
    """A subclass without ``_cli_name`` extends its nearest named ancestor.

    The ``extends = ["db.list"]`` declaration is the lazy trigger —
    composing the ``db list`` command's handler imports the plugin.
    """
    pip_install("repo_sub")

    from osh.commands.db_cmd import db

    effective = db.commands["list"]._handler_cls()
    mro_names = [c.__name__ for c in effective.__mro__]
    assert "Filestores" in mro_names
    specs = plugin_registry.plugin_registry().specs
    assert specs["osh-subext"].loaded
    assert not specs["osh-unrelated"].loaded


def test_lazy_handlers_key_resolves_named_handler(pip_install):
    """``handlers`` in toml lets ``resolve()`` find non-CLI named handlers."""
    pip_install("repo_handlers")

    from osh.handlers import resolve

    cls = resolve("_util.fmt")
    assert cls.__name__ == "Fmt"
    assert plugin_registry.plugin_registry().specs["osh-helper"].loaded


def test_derived_handler_is_a_new_command(pip_install):
    """A subclass with its own ``_cli_name`` is a command, not an extender."""
    pip_install("repo_derived")

    from osh.commands.db_cmd import Db
    from osh.handlers import resolve

    # The derived handler resolves to itself as a distinct named handler…
    cls = resolve("db.smart_list")
    assert cls.__name__ == "SmartList"
    # …and does not extend its parent once loaded.
    assert not issubclass(Db.effective(), cls)


def test_depends_imports_dependency_first(pip_install):
    """``depends`` plugins are imported before the dependent's module."""
    pip_install("repo_deps")

    spec = plugin_registry.plugin_registry().specs["osh-needy"]
    spec.load()

    specs = plugin_registry.plugin_registry().specs
    assert specs["osh-base"].loaded
    assert specs["osh-needy"].loaded


def test_depends_missing_plugin_fails_load(pip_install):
    """A ``depends`` entry naming an unknown plugin fails the load clearly."""
    pip_install("repo_dep_missing")

    spec = plugin_registry.plugin_registry().specs["osh-needy"]
    with pytest.raises(RuntimeError, match="osh-absent"):
        spec.load()
    assert spec.loaded


def test_depends_cycle_is_reported(pip_install):
    """Circular ``depends`` declarations fail instead of recursing forever."""
    pip_install("repo_dep_cycle")

    spec = plugin_registry.plugin_registry().specs["osh-cyc-a"]
    with pytest.raises(RuntimeError, match="circular"):
        spec.load()


def test_warn_unresolved_meta_reports_missing_refs(pip_install, capsys):
    """Startup validation warns about extends/depends nothing provides."""
    pip_install("repo_meta_bad")

    plugin_loader.warn_unresolved_meta()

    err = capsys.readouterr().err
    assert "osh-orphan" in err
    assert "no.such" in err
    assert "osh-absent" in err
    # Runs once per registry.
    plugin_loader.warn_unresolved_meta()
    assert capsys.readouterr().err == ""


def test_warn_unresolved_meta_quiet_when_refs_exist(pip_install, capsys):
    """Declared commands satisfy ``extends``; installed plugins ``depends``."""
    pip_install("repo_meta_ok")

    plugin_loader.warn_unresolved_meta()

    err = capsys.readouterr().err
    assert "osh-extok" not in err
    assert "osh-prov" not in err
