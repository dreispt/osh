"""Tests for command handler extensions.

Commands delegate to ``CommandHandler`` classes which plugins extend in
place by subclassing them (see ``osh.handlers``) — the ``osh odoo`` tests
below exercise that path through real command invocations.
"""

import types

import click
import pytest
from click.testing import CliRunner

from osh.commands.odoo_cmd import OdooRun, odoo
from osh.handlers import CommandHandler, Env, resolve
from osh.utils import plugin_loader


def test_core_commands_resolve_to_their_handlers():
    """Every built-in command name resolves to its handler class."""
    from osh.commands import (
        backend_cmd,
        config_cmd,
        db_cmd,
        init_cmd,
        plug_cmd,
        shell_cmd,
        switch_cmd,
    )

    expected = {
        "shell": shell_cmd.ShellRun,
        "init": init_cmd.Init,
        "switch": switch_cmd.Switch,
        "db": db_cmd.Db,
        "db.show": db_cmd.Db,
        "db.list": db_cmd.Db,
        "db.set": db_cmd.Db,
        "db.copy": db_cmd.Db,
        "db.shell": db_cmd.Db,
        "db.unset": db_cmd.Db,
        "backend": backend_cmd.BackendCtl,
        "backend.status": backend_cmd.BackendCtl,
        "backend.list": backend_cmd.BackendCtl,
        "backend.deactivate": backend_cmd.BackendCtl,
        "backend.stop": backend_cmd.BackendCtl,
        "config": config_cmd.Config,
        "config.show": config_cmd.Config,
        "config.user": config_cmd.ConfigUser,
        "config.user.verbosity": config_cmd.ConfigUser,
        "config.odoo": config_cmd.ConfigOdoo,
        "config.odoo.dev": config_cmd.ConfigOdoo,
        "plug": plug_cmd.Plug,
        "plug.install": plug_cmd.Plug,
        "plug.list": plug_cmd.Plug,
        "plug.uninstall": plug_cmd.Plug,
        "plug.enable": plug_cmd.Plug,
        "plug.disable": plug_cmd.Plug,
        "plug.alias": plug_cmd.Plug,
        "plug.unalias": plug_cmd.Plug,
    }
    for name, cls in expected.items():
        assert resolve(name) is cls, name


def _module_with_extensions(*classes):
    """Return a fake plugin module exposing extension classes as attributes."""
    return types.SimpleNamespace(**{f"ext_{i}": c for i, c in enumerate(classes)})


def _patch_plugin_modules(monkeypatch, modules):
    """Make the plugin loader iterate over *modules*."""
    monkeypatch.setattr(
        plugin_loader,
        "_iter_plugin_modules",
        lambda: (("test", mod) for mod in modules),
    )


class _LayersBase(CommandHandler):
    """Named handler used by the layering tests below."""

    _cli_name = "test_layers.op"

    calls = []

    def probe(self):
        self.calls.append("base")


class _LayersFirst(_LayersBase):
    def probe(self):
        self.calls.append("first")
        return super().probe()


class _LayersSecond(_LayersBase):
    def probe(self):
        self.calls.append("second")
        return super().probe()


def test_subclasses_extend_their_named_parent(monkeypatch):
    """Plain subclasses without ``_cli_name`` compose onto the parent."""
    _LayersBase.calls = calls = []
    _patch_plugin_modules(
        monkeypatch,
        [
            _module_with_extensions(_LayersFirst),
            _module_with_extensions(_LayersFirst, _LayersSecond),
        ],
    )

    cls = resolve("test_layers.op")
    assert cls is _LayersBase
    effective = _LayersBase.effective()
    assert issubclass(effective, _LayersSecond)
    # Later plugins wrap earlier ones; super() chains them.
    effective().probe()
    assert calls == ["second", "first", "base"]


def test_named_subclass_is_a_new_handler_not_an_extender(monkeypatch):
    """A subclass declaring its own ``_cli_name`` is not an extension."""

    class Derived(_LayersBase):
        _cli_name = "test_layers.derived"

    _patch_plugin_modules(monkeypatch, [_module_with_extensions(Derived)])

    assert resolve("test_layers.derived") is Derived
    assert not issubclass(_LayersBase.effective(), Derived)


def test_underscore_named_handler_generates_no_command():
    """A ``_``-prefixed name segment marks a programmatic-only handler."""

    class Util(CommandHandler):
        _cli_name = "_test_ops.fmt"

    class GroupUtil(CommandHandler):
        _cli_name = "test_ops._fmt"

    assert Util.cli_command() == (None, None)
    assert GroupUtil.cli_command() == (None, None)


def test_cli_hidden_handler_generates_hidden_command():
    """``_cli_hidden`` hides the generated command from help listings."""

    class Sidecar(CommandHandler):
        _cli_name = "sidecar"
        _cli_hidden = True

    group, command = Sidecar.cli_command()
    assert group is None
    assert command.hidden is True


def test_instantiating_named_handler_dispatches_to_effective(monkeypatch):
    """``Handler(...)`` transparently yields the composed class instance."""
    _patch_plugin_modules(monkeypatch, [_module_with_extensions(_LayersFirst)])

    op = _LayersBase()
    assert isinstance(op, _LayersFirst)


def test_env_binds_ctx_and_params():
    """``env[cls]`` returns an instance with ctx and params set."""

    class Probe(CommandHandler):
        pass

    ctx = types.SimpleNamespace()
    env = Env(ctx)
    op = env[Probe](show_all=True, dry_run=False)
    assert op.env is env
    assert op.ctx is ctx
    assert op.show_all is True
    assert op.dry_run is False


def test_handler_rejects_reserved_param_names():
    """Params cannot shadow handler attributes or methods."""

    class Probe(CommandHandler):
        dry_run = False

        def run(self):
            pass

        @property
        def prop(self):
            return None

    op = Probe()
    for bad in ("env", "ctx", "run", "prop", "_private"):
        with pytest.raises(TypeError, match=bad):
            op(**{bad: 1})
    op(dry_run=True)  # class-level param defaults may still be overridden
    assert op.dry_run is True


def test_effective_returns_same_composed_class(monkeypatch):
    """Repeated ``effective()`` lookups return the same class object."""
    ext = type("Ext", (_LayersBase,), {})
    _patch_plugin_modules(monkeypatch, [_module_with_extensions(ext)])
    assert _LayersBase.effective() is _LayersBase.effective()


def test_resolve_without_extensions_returns_base(monkeypatch):
    """With no extensions, the literal handler class is the effective one."""
    _patch_plugin_modules(monkeypatch, [])
    assert resolve("odoo") is OdooRun
    assert OdooRun.effective() is OdooRun


def test_effective_skips_uncomposable_extensions(monkeypatch, capsys):
    """An extension that fails class composition is skipped with an error."""
    meta_a = type("MetaA", (type,), {})
    meta_b = type("MetaB", (type,), {})
    ext_a = meta_a("ExtA", (_LayersBase,), {})
    ext_b = meta_b("ExtB", (_LayersBase,), {})
    _patch_plugin_modules(monkeypatch, [_module_with_extensions(ext_a, ext_b)])

    cls = _LayersBase.effective()
    assert "could not be composed" in capsys.readouterr().err
    assert issubclass(cls, ext_a)
    assert not issubclass(cls, ext_b)


def _odoo_extensions(monkeypatch, *extensions):
    """Patch the plugin loader to expose *extensions* for the ``odoo`` handler."""
    modules = [_module_with_extensions(ext) for ext in extensions]
    _patch_plugin_modules(monkeypatch, modules)


class _OpenOption:
    """Extension mixin adding an ``--open`` flag to ``osh odoo``."""

    @classmethod
    def get_options(cls):
        return [
            *super().get_options(),
            click.Option(["--open", "open_browser"], is_flag=True),
        ]


def test_odoo_runs_pre_env_extensions(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    capture_execvp,
):
    """``pre_env`` extensions run with the assembled state before exec."""
    calls = []

    class Recorder(OdooRun):
        def pre_env(self):
            super().pre_env()
            calls.append(self)

    _odoo_extensions(monkeypatch, Recorder)

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["-d", "mydb"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    op = calls[0]
    assert op.base == tmp_project
    assert op.env_spec.argv[0].endswith("odoo")
    assert "mydb" in op.env_spec.argv
    assert capture_execvp  # exec still happened after the extension


def test_odoo_get_options_adds_cli_options(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    capture_execvp,
):
    """``get_options`` params parse and land in ``ctx.params``."""
    seen_params = {}

    class Recorder(_OpenOption, OdooRun):
        def pre_env(self):
            super().pre_env()
            seen_params.update(self.ctx.params)

    _odoo_extensions(monkeypatch, Recorder)

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--open", "-d", "mydb"])

    assert result.exit_code == 0, result.output
    assert seen_params["open_browser"] is True


def test_odoo_help_lists_extension_options(monkeypatch):
    """Extension-provided options appear in ``osh odoo --help``."""

    class Recorder(_OpenOption, OdooRun):
        pass

    _odoo_extensions(monkeypatch, Recorder)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--help"])

    assert result.exit_code == 0
    assert "--open" in result.output


def test_odoo_pre_env_extension_can_abort(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    capture_execvp,
):
    """A ``ClickException`` raised in ``pre_env`` aborts before exec."""

    class Abort(OdooRun):
        def pre_env(self):
            super().pre_env()
            raise click.ClickException("extension says no")

    _odoo_extensions(monkeypatch, Abort)

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["-d", "mydb"])

    assert result.exit_code != 0
    assert "extension says no" in result.output
    assert not capture_execvp


def test_odoo_extensions_chain_through_super(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    capture_execvp,
):
    """Two extensions for the same handler both run, last-loaded first."""
    calls = []

    class First(OdooRun):
        def pre_env(self):
            calls.append("first")
            super().pre_env()

    class Second(OdooRun):
        def pre_env(self):
            calls.append("second")
            super().pre_env()

    _odoo_extensions(monkeypatch, First, Second)

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["-d", "mydb"])

    assert result.exit_code == 0, result.output
    assert calls == ["second", "first"]


def test_odoo_without_extensions_is_unchanged(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    capture_execvp,
):
    """With no plugins declaring extensions, ``osh odoo`` behaves as before."""
    _odoo_extensions(monkeypatch)

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["-d", "mydb"])

    assert result.exit_code == 0, result.output
    assert capture_execvp


def test_core_command_extensions_run_through_the_command(monkeypatch, tmp_path):
    """Subclassing a group handler hooks its subcommand invocation."""
    from osh.commands.plug_cmd import Plug, plug

    calls = []

    class Recorder(Plug):
        def list(self):
            calls.append("extension")
            super().list()

    _patch_plugin_modules(monkeypatch, [_module_with_extensions(Recorder)])
    monkeypatch.setenv("OSH_PLUGINS_DIR", str(tmp_path))
    monkeypatch.setattr("osh.commands.plug_cmd.user_plugin_dir", lambda: tmp_path)

    result = CliRunner().invoke(plug, ["list"])

    assert result.exit_code == 0, result.output
    assert calls == ["extension"]
    assert "No plugins installed." in result.output


def test_db_shell_shares_shell_preparation(monkeypatch, tmp_project):
    """``db shell`` reuses ``osh shell``'s steps, swapping ``env`` for ``db_env``."""
    from osh.commands.db_cmd import db

    seen = {}

    class FakeBackend:
        def env(self, ctx, base, env_spec, **kwargs):
            seen["env"] = env_spec

        def db_env(self, ctx, base, env_spec, **kwargs):
            seen["db_env"] = env_spec

        def build_addons_paths(self, base, include_themes=False):
            return []

    monkeypatch.setattr(
        "osh.commands.shell_cmd.db_module.resolve_backend",
        lambda base: FakeBackend(),
    )
    monkeypatch.setattr(
        "osh.commands.shell_cmd.check_run_diagnostics", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "osh.commands.shell_cmd.prepare_env_context",
        lambda *a, **k: (None, {}, None),
    )

    monkeypatch.chdir(tmp_project)
    result = CliRunner().invoke(db, ["shell", "psql"])

    assert result.exit_code == 0, result.output
    assert seen["db_env"].argv == ["psql"]
    assert "env" not in seen
