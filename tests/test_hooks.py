"""Tests for the plugin hooks manifest capability and ``osh odoo`` hook points."""

import types

import click
from click.testing import CliRunner

from osh.commands.odoo_cmd import odoo
from osh.hooks import HOOK_ODOO_OPTIONS, HOOK_ODOO_PRE_ENV
from osh.utils import plugin_loader


def _module_with_manifest(manifest):
    """Return a fake plugin module exposing *manifest*."""
    module = types.SimpleNamespace()
    if manifest is not None:
        module.OSH_PLUGIN_MANIFEST = manifest
    return module


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


def _odoo_hooks(monkeypatch, options=None, pre_env=None):
    """Patch ``load_hooks`` in odoo_cmd with the given implementations."""
    hooks = {
        HOOK_ODOO_OPTIONS: options or [],
        HOOK_ODOO_PRE_ENV: pre_env or [],
    }
    monkeypatch.setattr(
        "osh.commands.odoo_cmd.load_hooks",
        lambda name=None: hooks if name is None else hooks.get(name, []),
    )


def test_odoo_runs_pre_env_hooks(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    capture_execvp,
):
    """``odoo.pre_env`` hooks run with (ctx, base, env_spec) before exec."""
    calls = []
    _odoo_hooks(monkeypatch, pre_env=[lambda c, b, s: calls.append((c, b, s))])

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["-d", "mydb"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    hook_ctx, hook_base, env_spec = calls[0]
    assert hook_base == tmp_project
    assert env_spec.argv[0].endswith("odoo")
    assert "mydb" in env_spec.argv
    assert capture_execvp  # exec still happened after the hook


def test_odoo_options_hook_adds_cli_options(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    capture_execvp,
):
    """``odoo.options`` params parse and land in ``ctx.params``."""
    seen_params = {}
    _odoo_hooks(
        monkeypatch,
        options=[click.Option(["--open", "open_browser"], is_flag=True)],
        pre_env=[lambda c, b, s: seen_params.update(c.params)],
    )

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--open", "-d", "mydb"])

    assert result.exit_code == 0, result.output
    assert seen_params["open_browser"] is True


def test_odoo_help_lists_hook_options(monkeypatch):
    """Injected options appear in ``osh odoo --help``."""
    _odoo_hooks(
        monkeypatch,
        options=[click.Option(["--open", "open_browser"], is_flag=True)],
    )
    runner = CliRunner()
    result = runner.invoke(odoo, ["--help"])

    assert result.exit_code == 0
    assert "--open" in result.output


def test_odoo_pre_env_hook_can_abort(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    capture_execvp,
):
    """A ``ClickException`` raised by a hook aborts before exec."""

    def aborting_hook(ctx, base, env_spec):
        raise click.ClickException("hook says no")

    _odoo_hooks(monkeypatch, pre_env=[aborting_hook])

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["-d", "mydb"])

    assert result.exit_code != 0
    assert "hook says no" in result.output
    assert not capture_execvp


def test_odoo_without_hooks_is_unchanged(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    capture_execvp,
):
    """With no plugins declaring hooks, ``osh odoo`` behaves as before."""
    _odoo_hooks(monkeypatch)

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["-d", "mydb"])

    assert result.exit_code == 0, result.output
    assert capture_execvp
