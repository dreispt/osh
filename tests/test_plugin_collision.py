"""Tests for plugin command name collision handling."""

import importlib

import click
import pytest
from click.testing import CliRunner


def _write_fake_plugin(plugin_dir, name, command_name):
    """Create a minimal plugin package that exposes one Click command."""
    plugin_path = plugin_dir / name
    plugin_path.mkdir(parents=True)
    (plugin_path / "__init__.py").write_text(
        f"import click\n\n"
        f"@click.command(name='{command_name}')\n"
        f"def {command_name}():\n"
        f"    click.echo('from {name}')\n\n"
        f"COMMANDS = [{command_name}]\n"
    )


@pytest.fixture(autouse=True)
def _reset_cli():
    """Reload osh.cli after each test so imports return to the default state."""
    yield
    from osh import cli

    importlib.reload(cli)


def test_collision_with_core_command_is_renamed(monkeypatch, tmp_path):
    """A user plugin command with the same name as a core command is prefixed."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    _write_fake_plugin(plugin_dir, "fake", "init")

    monkeypatch.setattr("osh.plugin_loader._user_plugin_dir", lambda: plugin_dir)

    from osh import cli

    importlib.reload(cli)

    assert cli.main.commands["init"].callback.__module__ == "osh.commands.init_cmd"
    assert "fake-init" in cli.main.commands
    assert cli.main.commands["fake-init"].callback.__module__ == "osh_user_plugin_fake"


def test_collision_between_plugins_is_renamed(monkeypatch, tmp_path):
    """When two plugins register the same name, the second one is prefixed."""

    @click.command(name="custom")
    def first_custom():
        click.echo("first custom")

    @click.command(name="custom")
    def second_custom():
        click.echo("second custom")

    monkeypatch.setattr(
        "osh.plugin_loader.load_plugins",
        lambda: [("first", first_custom), ("second", second_custom)],
    )

    from osh import cli

    importlib.reload(cli)

    assert cli.main.commands["custom"].callback is first_custom.callback
    assert cli.main.commands["second-custom"].callback is second_custom.callback


def test_no_collision_registers_plugin_command(monkeypatch, tmp_path):
    """A plugin command with a unique name is registered normally."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    _write_fake_plugin(plugin_dir, "fake", "unique")

    monkeypatch.setattr("osh.plugin_loader._user_plugin_dir", lambda: plugin_dir)

    from osh import cli

    importlib.reload(cli)

    assert "unique" in cli.main.commands


def test_renamed_command_appears_in_help(monkeypatch, tmp_path):
    """The derived command name is visible in `osh --help`."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    _write_fake_plugin(plugin_dir, "fake", "init")

    monkeypatch.setattr("osh.plugin_loader._user_plugin_dir", lambda: plugin_dir)

    from osh import cli

    importlib.reload(cli)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["--help"])

    assert result.exit_code == 0
    assert "fake-init" in result.output


def test_double_collision_is_ignored(monkeypatch, tmp_path, capsys):
    """If even the prefixed name collides, the plugin command is skipped."""

    @click.command(name="custom")
    def first_custom():
        click.echo("first custom")

    @click.command(name="custom")
    def second_custom():
        click.echo("second custom")

    monkeypatch.setattr(
        "osh.plugin_loader.load_plugins",
        lambda: [
            ("first", first_custom),
            ("source", second_custom),
            ("source", second_custom),
        ],
    )

    from osh import cli

    importlib.reload(cli)

    assert cli.main.commands["custom"].callback is first_custom.callback
    assert cli.main.commands["source-custom"].callback is second_custom.callback
    assert "source-source-custom" not in cli.main.commands
    assert (
        "conflicts with an existing command and is ignored" in capsys.readouterr().out
    )
