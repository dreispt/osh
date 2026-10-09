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
