"""Tests for the plugin ``hooks`` machinery and command operation extensions.

``hooks`` remains a plugin-to-plugin mechanism; core commands are extended
through operation classes via the ``@extends`` decorator (see
``osh.operations``) — the ``osh odoo`` tests below exercise that path.
"""

import types

import click
import pytest
from click.testing import CliRunner

from osh import operations
from osh.commands.odoo_cmd import odoo
from osh.utils import plugin_loader


def _module_with_manifest(manifest):
    """Return a fake plugin module exposing *manifest*."""
    module = types.SimpleNamespace()
    if manifest is not None:
        module.OSH_PLUGIN_MANIFEST = manifest
    return module


def _module_with_extensions(*classes):
    """Return a fake plugin module exposing extension mixins as attributes."""
    return types.SimpleNamespace(**{f"ext_{i}": c for i, c in enumerate(classes)})


def _patch_plugin_modules(monkeypatch, modules):
    """Make the plugin loader iterate over *modules*."""
    monkeypatch.setattr(
        plugin_loader,
        "_iter_plugin_modules",
        lambda: (("test", mod) for mod in modules),
    )


def test_load_hooks_aggregates_manifest_hooks(monkeypatch):
    """``load_hooks`` flattens single and list hook implementations."""
    hook_a, hook_b, hook_c = object(), object(), object()
    _patch_plugin_modules(
        monkeypatch,
        [
            _module_with_manifest({"hooks": {"point": [hook_a, hook_b]}}),
            _module_with_manifest({"hooks": {"point": hook_c}}),
            _module_with_manifest({"commands": []}),  # no hooks key
            _module_with_manifest(None),  # no manifest at all
        ],
    )

    assert plugin_loader.load_hooks("point") == [hook_a, hook_b, hook_c]
    assert plugin_loader.load_hooks("missing") == []
    assert plugin_loader.load_hooks() == {"point": [hook_a, hook_b, hook_c]}


def test_load_hooks_ignores_non_dict_hooks(monkeypatch):
    """A non-dict ``hooks`` manifest value is ignored."""
    _patch_plugin_modules(
        monkeypatch,
        [_module_with_manifest({"hooks": ["not", "a", "dict"]})],
    )
    assert plugin_loader.load_hooks() == {}


def test_load_extensions_aggregates_marked_classes(monkeypatch):
    """``load_extensions`` collects classes carrying the ``_extends`` marker."""
    ext_a = operations.extends("db.list")(type("A", (), {}))
    ext_b = operations.extends("db.list")(type("B", (), {}))
    ext_c = operations.extends("db.list")(type("C", (), {}))
    _patch_plugin_modules(
        monkeypatch,
        [
            _module_with_extensions(ext_a, ext_b),
            _module_with_extensions(ext_a, ext_c),  # ext_a re-exported
            _module_with_manifest({"commands": []}),  # no marked classes
        ],
    )

    assert plugin_loader.load_extensions("db.list") == [ext_a, ext_b, ext_c]
    assert plugin_loader.load_extensions("missing") == []
    assert plugin_loader.load_extensions() == {"db.list": [ext_a, ext_b, ext_c]}
    assert plugin_loader.load_extension_entries("db.list") == [
        ("test", ext_a),
        ("test", ext_b),
        ("test", ext_c),
    ]


def test_load_extensions_ignores_unmarked_attributes(monkeypatch):
    """Attributes without the marker are ignored, incl. manifest entries."""
    module = types.SimpleNamespace(
        some_class=type("A", (), {}),
        some_obj=object(),
        OSH_PLUGIN_MANIFEST={"extends": {"db.list": [type("B", (), {})]}},
    )
    _patch_plugin_modules(monkeypatch, [module])
    assert plugin_loader.load_extensions() == {}


def test_resolve_layers_extensions_in_load_order(monkeypatch):
    """Later plugins override earlier ones; ``super()`` chains them."""
    calls = []

    class First:
        def probe(self):
            calls.append("first")
            return super().probe()

    class Second:
        def probe(self):
            calls.append("second")
            return super().probe()

    class Base(operations.Operation):
        def probe(self):
            calls.append("base")

    _patch_plugin_modules(
        monkeypatch,
        [
            _module_with_extensions(operations.extends("op")(First)),
            _module_with_extensions(operations.extends("op")(Second)),
        ],
    )
    monkeypatch.setitem(operations._OPERATIONS, "op", Base)

    cls = operations.registry["op"]
    assert issubclass(cls, Base)
    cls(operations.Env(None)).probe()
    assert calls == ["second", "first", "base"]


def test_env_binds_ctx_and_params():
    """``env["op"]`` returns an instance with ctx and params set."""

    class Probe(operations.Operation):
        pass

    ctx = types.SimpleNamespace()
    env = operations.Env(ctx)
    op = Probe(env)(show_all=True, dry_run=False)
    assert op.env is env
    assert op.ctx is ctx
    assert op.show_all is True
    assert op.dry_run is False


def test_operation_rejects_reserved_param_names():
    """Params cannot shadow operation attributes or methods."""

    class Probe(operations.Operation):
        dry_run = False

        def run(self):
            pass

        @property
        def prop(self):
            return None

    op = Probe(operations.Env(None))
    for bad in ("env", "ctx", "operation_name", "run", "prop", "_private"):
        with pytest.raises(TypeError, match=bad):
            op(**{bad: 1})
    op(dry_run=True)  # class-level param defaults may still be overridden
    assert op.dry_run is True


def test_registry_returns_same_composed_class(monkeypatch):
    """Repeated ``registry[name]`` lookups return the same class object."""
    ext = operations.extends("db.list")(type("Ext", (), {}))
    _patch_plugin_modules(monkeypatch, [_module_with_extensions(ext)])
    assert operations.registry["db.list"] is operations.registry["db.list"]


def test_registry_contains_has_no_side_effects(monkeypatch, capsys):
    """Membership tests do not compose classes or emit warnings."""
    ext = operations.extends("no.such.op")(type("Ext", (), {}))
    _patch_plugin_modules(monkeypatch, [_module_with_extensions(ext)])
    monkeypatch.setattr(operations, "_WARNED_UNKNOWN", set())
    assert "odoo" in operations.registry
    assert "no.such.op" not in operations.registry
    assert capsys.readouterr().err == ""


def test_registry_behaves_like_a_mapping(monkeypatch):
    """``registry`` supports membership, iteration and KeyError."""
    _patch_plugin_modules(monkeypatch, [])
    assert "odoo" in operations.registry
    assert "db.list" in operations.registry
    assert "no.such.op" not in operations.registry
    assert "odoo" in list(operations.registry)
    with pytest.raises(KeyError):
        operations.registry["no.such.op"]


def test_resolve_without_extensions_returns_base(monkeypatch):
    """With no plugins declaring extensions, the base class resolves."""
    _patch_plugin_modules(monkeypatch, [])
    assert operations.registry["odoo"] is not None
    assert "db.list" in operations._OPERATIONS


def test_resolve_warns_on_unknown_operation(monkeypatch, capsys):
    """An ``extends`` target matching no operation is reported once."""
    ext = operations.extends("no.such.op")(type("Ext", (), {}))
    _patch_plugin_modules(monkeypatch, [_module_with_extensions(ext)])
    monkeypatch.setattr(operations, "_WARNED_UNKNOWN", set())

    operations.registry["odoo"]
    operations.registry["db.list"]
    err = capsys.readouterr().err
    assert err.count("unknown operation 'no.such.op'") == 1


def test_resolve_skips_non_class_extensions(monkeypatch, capsys):
    """Marked non-class attributes are reported and skipped."""
    bad = types.SimpleNamespace()
    bad._extends = "db.list"
    _patch_plugin_modules(monkeypatch, [types.SimpleNamespace(bad=bad)])
    cls = operations.registry["db.list"]
    assert "non-class extension" in capsys.readouterr().err
    assert cls.__name__ == "DbList"


def test_resolve_skips_uncomposable_extensions(monkeypatch, capsys):
    """An extension that fails class composition is skipped with an error."""
    meta_a = type("MetaA", (type,), {})
    meta_b = type("MetaB", (type,), {})
    ext_a = operations.extends("db.list")(meta_a("ExtA", (), {}))
    ext_b = operations.extends("db.list")(meta_b("ExtB", (), {}))
    _patch_plugin_modules(monkeypatch, [_module_with_extensions(ext_a, ext_b)])

    cls = operations.registry["db.list"]
    assert "could not be composed" in capsys.readouterr().err
    assert issubclass(cls, ext_a)
    assert not issubclass(cls, ext_b)


def _odoo_extensions(monkeypatch, *extensions):
    """Patch the plugin loader to expose *extensions* for the ``odoo`` op."""
    modules = [
        _module_with_extensions(operations.extends("odoo")(ext)) for ext in extensions
    ]
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

    class Recorder:
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

    class Recorder(_OpenOption):
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
    _odoo_extensions(monkeypatch, _OpenOption)
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

    class Abort:
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
    """Two extensions for the same op both run, last-loaded first."""
    calls = []

    class First:
        def pre_env(self):
            calls.append("first")
            super().pre_env()

    class Second:
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
