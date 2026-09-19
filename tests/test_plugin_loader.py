"""Tests for user plugin loading and subplugin discovery.

A user plugin directory can be a multi-plugin repository: each direct
subpackage declaring ``OSH_PLUGIN_MANIFEST`` is loaded as a plugin of its
own, so repos like osh-contrib need no aggregation code.
"""

import pytest

from osh.backup_sources import BackupSource
from osh.utils import plugin_loader, plugin_registry


def _write_package(root, name, init_src):
    """Create a package dir with the given ``__init__.py`` source."""
    pkg = root / name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(init_src)
    return pkg


def _ext_src(cls_name="Ext"):
    """Build a plugin ``__init__.py`` source exposing a marked extension class."""
    return f"class {cls_name}:\n    _extends = 'point'\n\n" "OSH_PLUGIN_MANIFEST = {}\n"


@pytest.fixture
def plugin_dir(tmp_path, monkeypatch):
    """A fake user plugin directory used by the plugin loader."""
    directory = tmp_path / "plugins"
    directory.mkdir()
    monkeypatch.setattr(plugin_registry, "user_plugin_dir", lambda: directory)
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
    _write_package(repo, "osh_two", _ext_src())

    commands = {cmd.name: src for src, cmd in plugin_loader.load_plugins()}
    assert commands["one-cmd"] == "osh-one"
    assert [c.__name__ for c in plugin_loader.load_extensions("point")] == ["Ext"]


def test_root_manifest_and_subplugins_both_load(plugin_dir):
    """A root package with its own manifest still contributes it."""
    repo = _write_package(plugin_dir, "repo_b", _ext_src("RootExt"))
    _write_package(repo, "osh_sub", _ext_src("SubExt"))

    assert sorted(c.__name__ for c in plugin_loader.load_extensions("point")) == [
        "RootExt",
        "SubExt",
    ]


def test_subplugin_relative_imports_work(plugin_dir):
    """Subplugin packages are real packages, so relative imports resolve."""
    repo = _write_package(plugin_dir, "repo_c", "")
    sub = _write_package(
        repo,
        "osh_rel",
        "from .helper import VALUE\n\n"
        "class Ext:\n    _extends = 'point'\n    v = VALUE\n\n"
        "OSH_PLUGIN_MANIFEST = {'commands': []}\n",
    )
    (sub / "helper.py").write_text("VALUE = 42\n")

    assert [c.v for c in plugin_loader.load_extensions("point")] == [42]


def test_broken_subplugin_warns_and_others_load(plugin_dir, capsys):
    """A marked subpackage failing to import warns; the rest still load."""
    repo = _write_package(plugin_dir, "repo_d", "")
    _write_package(repo, "osh_broken", "1/0\n\nOSH_PLUGIN_MANIFEST = {}\n")
    _write_package(repo, "osh_fine", _ext_src())

    assert [c.__name__ for c in plugin_loader.load_extensions("point")] == ["Ext"]
    assert "Could not load plugin" in capsys.readouterr().err


def test_non_package_dirs_ignored(plugin_dir, capsys):
    """Dirs without ``__init__.py`` are skipped silently."""
    repo = _write_package(plugin_dir, "repo_e", "")
    (repo / "docs").mkdir()
    (repo / "not-python-dir").mkdir()
    (repo / "not-python-dir" / "__init__.py").write_text("")
    _write_package(repo, "osh_ok", _ext_src())

    assert [c.__name__ for c in plugin_loader.load_extensions("point")] == ["Ext"]
    assert capsys.readouterr().err == ""


def test_single_file_plugin_still_loads(plugin_dir):
    """A ``osh_plugin.py`` file plugin keeps working (no subplugin scan)."""
    (plugin_dir / "repo_f").mkdir()
    (plugin_dir / "repo_f" / "osh_plugin.py").write_text(_ext_src())

    assert [c.__name__ for c in plugin_loader.load_extensions("point")] == ["Ext"]


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
        "class Ext:\n    _extends = 'point'\n\n"
        "OSH_PLUGIN_MANIFEST = {'commands': [sub]}\n",
    )

    commands = {cmd.name: src for src, cmd in plugin_loader.load_plugins()}
    assert commands["sub-cmd"] == "osh-sub"
    assert [c.__name__ for c in plugin_loader.load_extensions("point")] == ["Ext"]


def test_bare_repo_ignores_non_packages(plugin_dir, capsys):
    """Non-package dirs inside a bare repo are skipped without warnings."""
    repo = plugin_dir / "repo_h"
    repo.mkdir()
    (repo / "docs").mkdir()
    (repo / "my-addon").mkdir()  # not a valid module name
    (repo / "my-addon" / "__init__.py").write_text("")
    _write_package(repo, "osh_ok", _ext_src())

    assert [c.__name__ for c in plugin_loader.load_extensions("point")] == ["Ext"]
    assert capsys.readouterr().err == ""


def test_bare_repo_without_plugins_loads_nothing(plugin_dir, capsys):
    """A dir that is neither a plugin nor a repo of plugins is ignored."""
    (plugin_dir / "repo_i").mkdir()
    (plugin_dir / "repo_i" / "README.md").write_text("# not a plugin\n")

    assert plugin_loader.load_extensions("point") == []
    assert capsys.readouterr().err == ""


def test_disabled_subplugins_are_not_imported(plugin_dir, monkeypatch, tmp_path):
    """Disabled subplugins are filtered before import — code never runs."""
    marker = tmp_path / "imported"
    repo = _write_package(plugin_dir, "repo_j", _ext_src("RootExt"))
    _write_package(repo, "osh_on", _ext_src("OnExt"))
    _write_package(
        repo,
        "osh_off",
        f"import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('ran')\n"
        f"class OffExt:\n    _extends = 'point'\n\n"
        f"OSH_PLUGIN_MANIFEST = {{'commands': []}}\n",
    )
    monkeypatch.setattr(
        plugin_registry,
        "get_enabled_plugins",
        lambda source: ["repo-j", "osh-on"] if source == "repo_j" else None,
    )

    assert sorted(c.__name__ for c in plugin_loader.load_extensions("point")) == [
        "OnExt",
        "RootExt",
    ]
    assert not marker.exists()


def test_broken_plugin_leaves_no_sys_modules_entry(plugin_dir, capsys):
    """A plugin that fails to import is removed from ``sys.modules``."""
    import sys

    _write_package(plugin_dir, "repo_k", "1/0\n")

    assert plugin_loader.load_extensions("point") == []
    assert "Could not load plugin 'repo-k'" in capsys.readouterr().err
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
        "    'group_commands': {'db': [mysub]},\n"
        "}\n",
    )

    assert "mybackend" in plugin_loader.load_backends()
    entries = plugin_loader.iter_plugin_subclasses(BackupSource)
    assert any(
        s == "repo-l" and getattr(i, "scheme", None) == "myscheme" for s, i in entries
    )
    groups = plugin_loader.load_group_commands()
    assert any(c.name == "mysub" for _s, c in groups["db"])
    assert capsys.readouterr().err == ""


def test_user_plugin_extends_contributes_classes(plugin_dir):
    """A user plugin's ``@extends`` mixin contributes operation extensions."""
    src = (
        "from osh.operations import extends\n\n"
        "@extends('db.list')\n"
        "class Ext:\n"
        "    pass\n"
    )
    _write_package(plugin_dir, "repo_ext", src)

    entries = plugin_loader.load_extension_entries("db.list")
    assert any(s == "repo-ext" and i.__name__ == "Ext" for s, i in entries)


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
        "    scheme = 'db'\n"
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


# Two-stage lazy loading ----------------------------------------------------


def _write_lazy_plugin(repo, name, toml, init_src):
    """Create a marked plugin package inside *repo*."""
    sub = _write_package(repo, name, init_src)
    (sub / "osh-plugin.toml").write_text(toml)
    return sub


def test_load_plugins_registers_lazy_stubs_without_import(plugin_dir):
    """Declared commands register as stubs — the module is never imported."""
    repo = _write_package(plugin_dir, "repo_lazy", "")
    _write_lazy_plugin(
        repo,
        "osh_marked",
        '[commands]\nmarked = "A marked command."\n',
        "raise RuntimeError('plugin evaluated too early')\n",
    )

    commands = {cmd.name: cmd for _src, cmd in plugin_loader.load_plugins()}

    from osh.cli_utils import LazyCommand

    assert isinstance(commands["marked"], LazyCommand)
    spec = plugin_loader.plugin_registry().specs["osh-marked"]
    assert spec.lazy
    assert not spec.loaded


def test_lazy_command_loads_and_delegates_args(plugin_dir, tmp_path):
    """Invoking a lazy command imports the plugin and runs it with the args."""
    ran = tmp_path / "ran"
    repo = _write_package(plugin_dir, "repo_echo", "")
    _write_lazy_plugin(
        repo,
        "osh_echo",
        '[commands]\necho = "Echo a word."\n',
        "import click\n"
        "from osh.handlers import CommandHandler\n\n"
        "class Echo(CommandHandler):\n"
        "    _cli_name = 'echo'\n"
        "    word = None\n"
        "    @classmethod\n"
        "    def get_options(cls):\n"
        "        return [click.Argument(['word'])]\n"
        "    def run(self):\n"
        f"        import pathlib; pathlib.Path({str(ran)!r}).write_text(self.word)\n",
    )

    from click.testing import CliRunner

    commands = {cmd.name: cmd for _src, cmd in plugin_loader.load_plugins()}
    spec = plugin_loader.plugin_registry().specs["osh-echo"]
    assert not spec.loaded

    result = CliRunner().invoke(commands["echo"], ["hello"])

    assert result.exit_code == 0, result.output
    assert ran.read_text() == "hello"
    assert spec.loaded


def test_spec_load_not_called_for_help(plugin_dir, monkeypatch):
    """Rendering help lists the command without calling ``spec.load()``."""
    repo = _write_package(plugin_dir, "repo_help", "")
    _write_lazy_plugin(
        repo,
        "osh_helped",
        '[commands]\nhelped = "Helpy command."\n',
        "pass\n",
    )

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


def test_lazy_command_import_error_is_reported(plugin_dir):
    """A plugin failing to import reports a clean error at invocation time."""
    repo = _write_package(plugin_dir, "repo_bad", "")
    _write_lazy_plugin(
        repo,
        "osh_bad",
        '[commands]\nbad = "A broken command."\n',
        "import nonexistent_package_xyz\n",
    )

    from click.testing import CliRunner

    commands = {cmd.name: cmd for _src, cmd in plugin_loader.load_plugins()}
    result = CliRunner().invoke(commands["bad"], [])

    assert result.exit_code != 0
    assert "Could not load plugin 'osh-bad' command 'bad'" in result.output
    assert "nonexistent_package_xyz" in result.output


def test_lazy_extends_imports_only_declarer(plugin_dir):
    """Composing an op imports only plugins declaring it under ``extends``."""
    repo = _write_package(plugin_dir, "repo_ext", "")
    _write_lazy_plugin(
        repo, "osh_ext", 'extends = ["point"]\n', "class Ext:\n    _extends = 'point'\n"
    )
    _write_lazy_plugin(
        repo,
        "osh_other",
        'extends = ["other"]\n',
        "class Other:\n    _extends = 'other'\n",
    )

    assert [c.__name__ for c in plugin_loader.load_extensions("point")] == ["Ext"]
    specs = plugin_loader.plugin_registry().specs
    assert specs["osh-ext"].loaded
    assert not specs["osh-other"].loaded


def test_lazy_backend_imports_only_its_plugin(plugin_dir):
    """``get_backend_class`` imports only the plugin declaring the backend."""
    repo = _write_package(plugin_dir, "repo_backends", "")
    for name, cls_name in (("osh_backend_a", "A"), ("osh_backend_b", "B")):
        backend = cls_name.lower() + "-backend"
        _write_lazy_plugin(
            repo,
            name,
            f'[backends]\n{backend} = "{cls_name} backend."\n',
            "from osh.backends import Backend\n\n"
            f"class {cls_name}(Backend):\n"
            f"    name = '{backend}'\n"
            "    backend_type = 'backend'\n",
        )

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


def test_lazy_subclass_extends_parent_handler(plugin_dir):
    """A subclass without ``_cli_name`` extends its nearest named ancestor."""
    repo = _write_package(plugin_dir, "repo_sub", "")
    _write_lazy_plugin(
        repo,
        "osh_subext",
        'extends = ["db.list"]\n',
        "from osh.commands.db_cmd import DbList\n\n"
        "class Filestores(DbList):\n"
        "    def extra_sections(self):\n"
        "        return [*super().extra_sections(), 'extra']\n",
    )
    _write_lazy_plugin(
        repo,
        "osh_unrelated",
        'extends = ["other"]\n',
        "class Other:\n    _extends = 'other'\n",
    )

    from osh.commands.db_cmd import DbList

    effective = DbList.effective()
    mro_names = [c.__name__ for c in effective.__mro__]
    assert "Filestores" in mro_names
    specs = plugin_registry.plugin_registry().specs
    assert specs["osh-subext"].loaded
    assert not specs["osh-unrelated"].loaded


def test_lazy_handlers_key_resolves_named_handler(plugin_dir):
    """``handlers`` in toml lets ``resolve()`` find non-CLI named handlers."""
    repo = _write_package(plugin_dir, "repo_handlers", "")
    _write_lazy_plugin(
        repo,
        "osh_helper",
        'handlers = ["_util.fmt"]\n',
        "from osh.handlers import CommandHandler\n\n"
        "class Fmt(CommandHandler):\n"
        "    _cli_name = '_util.fmt'\n",
    )

    from osh.handlers import resolve

    cls = resolve("_util.fmt")
    assert cls.__name__ == "Fmt"
    assert plugin_registry.plugin_registry().specs["osh-helper"].loaded


def test_derived_handler_is_a_new_command(plugin_dir):
    """A subclass with its own ``_cli_name`` is a command, not an extender."""
    repo = _write_package(plugin_dir, "repo_derived", "")
    _write_lazy_plugin(
        repo,
        "osh_derived",
        '[group_commands.db]\nsmart_list = "Smart listing."\n',
        "from osh.commands.db_cmd import DbList\n\n"
        "class SmartList(DbList):\n"
        "    _cli_name = 'db.smart_list'\n",
    )

    from osh.commands.db_cmd import DbList
    from osh.handlers import resolve

    # The derived handler resolves to itself as a distinct named handler…
    cls = resolve("db.smart_list")
    assert cls.__name__ == "SmartList"
    # …and does not extend its parent once loaded.
    assert not issubclass(DbList.effective(), cls)


def test_depends_imports_dependency_first(plugin_dir, tmp_path):
    """``depends`` plugins are imported before the dependent's module."""
    repo = _write_package(plugin_dir, "repo_deps", "")
    marker = tmp_path / "base_loaded"
    _write_lazy_plugin(
        repo,
        "osh_base",
        "",
        f"import pathlib\npathlib.Path({str(marker)!r}).write_text('base')\n",
    )
    _write_lazy_plugin(
        repo,
        "osh_needy",
        'depends = ["osh-base"]\n',
        f"import pathlib\n"
        f"assert pathlib.Path({str(marker)!r}).exists(), 'base not loaded'\n",
    )

    spec = plugin_registry.plugin_registry().specs["osh-needy"]
    spec.load()

    specs = plugin_registry.plugin_registry().specs
    assert specs["osh-base"].loaded
    assert specs["osh-needy"].loaded


def test_depends_missing_plugin_fails_load(plugin_dir):
    """A ``depends`` entry naming an unknown plugin fails the load clearly."""
    repo = _write_package(plugin_dir, "repo_dep_missing", "")
    _write_lazy_plugin(repo, "osh_needy", 'depends = ["osh-absent"]\n', "pass\n")

    spec = plugin_registry.plugin_registry().specs["osh-needy"]
    with pytest.raises(RuntimeError, match="osh-absent"):
        spec.load()
    assert spec.loaded


def test_depends_cycle_is_reported(plugin_dir):
    """Circular ``depends`` declarations fail instead of recursing forever."""
    repo = _write_package(plugin_dir, "repo_dep_cycle", "")
    _write_lazy_plugin(repo, "osh_cyc_a", 'depends = ["osh-cyc-b"]\n', "pass\n")
    _write_lazy_plugin(repo, "osh_cyc_b", 'depends = ["osh-cyc-a"]\n', "pass\n")

    spec = plugin_registry.plugin_registry().specs["osh-cyc-a"]
    with pytest.raises(RuntimeError, match="circular"):
        spec.load()


def test_warn_unresolved_meta_reports_missing_refs(plugin_dir, capsys):
    """Startup validation warns about extends/depends nothing provides."""
    repo = _write_package(plugin_dir, "repo_meta_bad", "")
    _write_lazy_plugin(
        repo,
        "osh_orphan",
        'extends = ["no.such"]\ndepends = ["osh-absent"]\n',
        "pass\n",
    )

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
    repo = _write_package(plugin_dir, "repo_meta_ok", "")
    _write_lazy_plugin(repo, "osh_prov", '[commands]\nthing = "A thing."\n', "pass\n")
    _write_lazy_plugin(
        repo,
        "osh_extok",
        'extends = ["thing"]\ndepends = ["osh-prov"]\n',
        "pass\n",
    )

    plugin_loader.warn_unresolved_meta()

    err = capsys.readouterr().err
    assert "osh-extok" not in err
    assert "osh-prov" not in err
