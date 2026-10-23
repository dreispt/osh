"""Regression tests — each case names the bug it protects against."""

from click.testing import CliRunner

from osh.cli import main
from osh.db import get_project_config


def test_bare_init_binds_runtime_init_options(tmp_path, monkeypatch, user_config):
    """A bare ``osh init`` must bind the merged runtime init options.

    ``osh init`` with no arguments dispatches through the group callback,
    which invoked the default command via ``ctx.invoke(command)`` — a path
    that fills defaults from the command's static params only and never
    runs ``parse_args``. The runtime options merged dynamically by
    ``get_params`` (e.g. venv's ``--odoo-source``) were never bound, so a
    stored ``init.runtime`` default crashed init with
    ``AttributeError: 'Init' object has no attribute 'odoo_source'``.
    """
    target = tmp_path / "fresh"
    target.mkdir()
    (target / ".git").mkdir()
    monkeypatch.chdir(target)
    monkeypatch.setenv("OSH_INIT_VERSION", "19.0")
    user_config.parent.mkdir(parents=True)
    user_config.write_text('[init]\nruntime = "venv"\nedition = "ce"\n')

    from osh.utils.plugin_loader import get_runtime_class

    calls = []
    monkeypatch.setattr(
        get_runtime_class("venv"),
        "init",
        lambda self, target, **kw: calls.append(kw) or True,
    )

    result = CliRunner().invoke(main, ["init"])

    assert result.exit_code == 0, result.output
    assert calls and "odoo_source" in calls[0]
    assert get_project_config(target, "run", "runtime") == "venv"


def test_duplicate_module_warning_skips_symlinked_copies(tmp_path, capsys):
    """A module listed twice through a symlink aggregator is not a duplicate.

    Doodba's ``odoo/auto/addons`` links each module into ``custom/src``, so
    discovery found every module in two directories and commands like
    ``osh odoo`` or ``osh db list`` printed hundreds of warnings — one per
    module. The two entries resolve to the same directory and cannot
    diverge; only genuinely different copies should be reported.
    """
    module = tmp_path / "custom" / "src" / "repo" / "l10n_dup"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{'name': 'x'}\n")
    aggregate = tmp_path / "auto" / "addons"
    aggregate.mkdir(parents=True)
    (aggregate / "l10n_dup").symlink_to(module)

    from osh.utils.odoo_layout import build_addons_paths

    build_addons_paths(tmp_path)

    assert "exists in" not in capsys.readouterr().err
