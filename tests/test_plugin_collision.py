"""Tests for plugin command name collision handling."""

import importlib
import shutil
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

PLUGINS_DATA = Path(__file__).parent / "plugins"


def _copy_plugin(plugin_dir, name, dest):
    """Copy the static *name* plugin tree into the fake user plugin dir."""
    shutil.copytree(PLUGINS_DATA / name, plugin_dir / dest)


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
    _copy_plugin(plugin_dir, "fake_init", "fake")

    monkeypatch.setattr("osh.utils.plugin_registry.user_plugin_dir", lambda: plugin_dir)

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
        "osh.utils.plugin_loader.load_plugins",
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
    _copy_plugin(plugin_dir, "fake_unique", "fake")

    monkeypatch.setattr("osh.utils.plugin_registry.user_plugin_dir", lambda: plugin_dir)

    from osh import cli

    importlib.reload(cli)

    assert "unique" in cli.main.commands


def test_renamed_command_appears_in_help(monkeypatch, tmp_path):
    """The derived command name is visible in `osh --help`."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    _copy_plugin(plugin_dir, "fake_init", "fake")

    monkeypatch.setattr("osh.utils.plugin_registry.user_plugin_dir", lambda: plugin_dir)

    from osh import cli

    importlib.reload(cli)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["--help"])

    assert result.exit_code == 0
    assert "fake-init" in result.output


def test_plugin_commands_listed_in_separate_help_section(monkeypatch, tmp_path):
    """`osh --help` lists plugin commands in their own section."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    _copy_plugin(plugin_dir, "fake_unique", "fake")

    monkeypatch.setattr("osh.utils.plugin_registry.user_plugin_dir", lambda: plugin_dir)

    from osh import cli

    importlib.reload(cli)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["--help"])

    assert result.exit_code == 0
    core_section, _, plugin_section = result.output.partition("Plugin Commands:")
    assert "init" in core_section and "unique" not in core_section
    assert "unique" in plugin_section
    assert "[fake]" in plugin_section


def test_backend_groups_listed_in_separate_help_section(monkeypatch, tmp_path):
    """`osh --help` lists backend groups under Backend Commands — lazily."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()

    monkeypatch.setattr("osh.utils.plugin_registry.user_plugin_dir", lambda: plugin_dir)

    from osh import cli

    importlib.reload(cli)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["--help"])

    assert result.exit_code == 0
    assert "docker" in cli.main.backend_commands
    core_section, _, rest = result.output.partition("Backend Commands:")
    backend_section, _, _ = rest.partition("Plugin Commands:")
    assert "\n  docker " not in core_section
    assert "\n  docker " in backend_section
    assert "[osh-backend-docker]" in backend_section
    # Help renders from metadata — the backend plugins are never imported.
    from osh.utils.plugin_registry import plugin_registry

    specs = plugin_registry().specs
    assert not specs["osh-backend-docker"].loaded
    assert not specs["osh-backend-venv"].loaded


def test_group_plugin_subcommand_listed_in_separate_section(monkeypatch, tmp_path):
    """`osh db --help` sections plugin-provided subcommands too."""

    @click.command(name="audit")
    def plugin_audit():
        click.echo("plugin audit")

    monkeypatch.setattr(
        "osh.utils.plugin_loader.load_group_commands",
        lambda: {"db": [("fake", plugin_audit)]},
    )

    from osh import cli

    importlib.reload(cli)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["db", "--help"])

    assert result.exit_code == 0
    core_section, _, plugin_section = result.output.partition("Plugin Commands:")
    assert "audit" not in core_section
    assert "audit" in plugin_section
    assert "[fake]" in plugin_section


def test_double_collision_is_ignored(monkeypatch, tmp_path, capsys):
    """If even the prefixed name collides, the plugin command is skipped."""

    @click.command(name="custom")
    def first_custom():
        click.echo("first custom")

    @click.command(name="custom")
    def second_custom():
        click.echo("second custom")

    monkeypatch.setattr(
        "osh.utils.plugin_loader.load_plugins",
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
        "conflicts with an existing command and is ignored" in capsys.readouterr().err
    )


def test_collision_warns_on_every_load(monkeypatch, tmp_path, capsys):
    """A renamed plugin command prints a warning on every CLI load."""

    @click.command(name="init")
    def plugin_init():
        click.echo("plugin init")

    monkeypatch.setattr(
        "osh.utils.plugin_loader.load_plugins",
        lambda: [("fake", plugin_init)],
    )

    from osh import cli

    importlib.reload(cli)

    err = capsys.readouterr().err
    assert "plugin 'fake' command 'init' conflicts" in err
    assert "registered as 'fake-init'" in err
    assert "osh plug alias fake init <name>" in err


def test_alias_registers_chosen_name(monkeypatch, tmp_path, capsys):
    """A configured alias wins over both the declared and fallback names."""

    @click.command(name="init")
    def plugin_init():
        click.echo("plugin init")

    monkeypatch.setattr(
        "osh.utils.plugin_loader.load_plugins",
        lambda: [("fake", plugin_init)],
    )
    monkeypatch.setattr(
        "osh.config.get_plugin_aliases",
        lambda source: {"init": "project-init"} if source == "fake" else {},
    )

    from osh import cli

    importlib.reload(cli)

    assert cli.main.commands["project-init"].callback is plugin_init.callback
    assert "fake-init" not in cli.main.commands
    err = capsys.readouterr().err
    assert "'fake'" not in err


def test_alias_collision_is_skipped_with_error(monkeypatch, tmp_path, capsys):
    """An alias that collides with an existing command is not registered."""

    @click.command(name="mycmd")
    def plugin_cmd():
        click.echo("plugin cmd")

    monkeypatch.setattr(
        "osh.utils.plugin_loader.load_plugins",
        lambda: [("fake", plugin_cmd)],
    )
    monkeypatch.setattr(
        "osh.config.get_plugin_aliases",
        lambda source: {"mycmd": "init"} if source == "fake" else {},
    )

    from osh import cli

    importlib.reload(cli)

    assert cli.main.commands["init"].callback.__module__ == "osh.commands.init_cmd"
    assert "mycmd" not in cli.main.commands
    assert "is ignored" in capsys.readouterr().err


def test_alias_renames_non_colliding_command(monkeypatch, tmp_path):
    """Aliases work for plugin commands that do not collide at all."""

    @click.command(name="scan")
    def plugin_scan():
        click.echo("scan")

    monkeypatch.setattr(
        "osh.utils.plugin_loader.load_plugins",
        lambda: [("fake", plugin_scan)],
    )
    monkeypatch.setattr(
        "osh.config.get_plugin_aliases",
        lambda source: {"scan": "audit"} if source == "fake" else {},
    )

    from osh import cli

    importlib.reload(cli)

    assert cli.main.commands["audit"].callback is plugin_scan.callback
    assert "scan" not in cli.main.commands


def test_group_subcommand_collision_renamed(monkeypatch, tmp_path, capsys):
    """A plugin group subcommand colliding on the target group is renamed."""

    @click.command(name="show")
    def plugin_show():
        click.echo("plugin show")

    monkeypatch.setattr(
        "osh.utils.plugin_loader.load_group_commands",
        lambda: {"db": [("fake", plugin_show)]},
    )

    from osh import cli

    importlib.reload(cli)

    db_group = cli.main.commands["db"]
    assert db_group.commands["show"].callback.__module__ == "osh.commands.db_cmd"
    assert db_group.commands["fake-show"].callback is plugin_show.callback
    assert "'db.show' conflicts" in capsys.readouterr().err


def test_group_subcommand_unknown_group_is_skipped(monkeypatch, tmp_path, capsys):
    """A group command targeting a non-group command is skipped with an error."""

    @click.command(name="sub")
    def plugin_sub():
        click.echo("plugin sub")

    monkeypatch.setattr(
        "osh.utils.plugin_loader.load_group_commands",
        lambda: {"init": [("fake", plugin_sub)]},
    )

    from osh import cli

    importlib.reload(cli)

    assert "not a command group" in capsys.readouterr().err
