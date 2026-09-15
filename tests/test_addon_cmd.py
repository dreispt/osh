"""Tests for the ``osh addon`` command group."""

import importlib

import click
import pytest
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def _reset_cli():
    """Reload osh.cli after each test so imports return to the default state."""
    yield
    from osh import cli

    importlib.reload(cli)


def test_addon_is_a_command_group():
    """``addon`` is registered as a group on the root CLI."""
    from osh.cli import main

    assert isinstance(main.commands["addon"], click.Group)


def test_addon_group_accepts_plugin_subcommands(monkeypatch):
    """Plugin ``group_commands`` entries attach under ``osh addon``."""

    @click.command(name="audit")
    def plugin_audit():
        click.echo("plugin audit")

    monkeypatch.setattr(
        "osh.utils.plugin_loader.load_group_commands",
        lambda: {"addon": [("fake", plugin_audit)]},
    )

    from osh import cli

    importlib.reload(cli)

    assert cli.main.commands["addon"].commands["audit"].callback is (
        plugin_audit.callback
    )

    result = CliRunner().invoke(cli.main, ["addon", "--help"])
    assert result.exit_code == 0
    core_section, _, plugin_section = result.output.partition("Plugin Commands:")
    assert "audit" not in core_section
    assert "audit" in plugin_section
    assert "[fake]" in plugin_section
