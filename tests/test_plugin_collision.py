"""Tests for plugin command name collision handling."""

import importlib

import pytest
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def _reset_cli():
    """Reload osh.cli after each test so imports return to the default state."""
    yield
    from osh import cli

    importlib.reload(cli)


def test_collision_with_core_command_is_renamed(pip_install):
    """An installed plugin command named like a core command is prefixed."""
    pip_install("fake_init")

    from osh import cli

    importlib.reload(cli)

    assert cli.main.commands["init"].callback.__module__ == "osh.commands.init_cmd"
    assert "fake-init" in cli.main.commands
    assert cli.main.commands["fake-init"].callback.__module__ == "fake"


def test_collision_between_plugins_is_renamed(pip_install, capsys):
    """When two plugins register the same name, one keeps it, one is prefixed."""
    pip_install("repo_collision")

    from osh import cli

    importlib.reload(cli)

    # Entry points are discovered in declaration order — "first" keeps
    # the bare name, "second" gets the source-prefixed fallback.
    assert "custom" in cli.main.commands
    assert "second-custom" in cli.main.commands
    assert "first-custom" not in cli.main.commands
    err = capsys.readouterr().err
    assert "command 'custom' conflicts" in err


def test_no_collision_registers_plugin_command(pip_install):
    """A plugin command with a unique name is registered normally."""
    pip_install("fake_unique")

    from osh import cli

    importlib.reload(cli)

    assert "unique" in cli.main.commands


def test_renamed_command_appears_in_help(pip_install):
    """The derived command name is visible in `osh --help`."""
    pip_install("fake_init")

    from osh import cli

    importlib.reload(cli)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["--help"])

    assert result.exit_code == 0
    assert "fake-init" in result.output


def test_plugin_commands_listed_in_separate_help_section(pip_install):
    """`osh --help` lists plugin commands in their own section."""
    pip_install("fake_unique")

    from osh import cli

    importlib.reload(cli)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["--help"])

    assert result.exit_code == 0
    core_section, _, plugin_section = result.output.partition("Plugin Commands:")
    assert "init" in core_section and "unique" not in core_section
    assert "unique" in plugin_section
    assert "[fake]" in plugin_section


def test_help_does_not_import_runtime_plugins():
    """`osh --help` renders without importing the runtime plugins."""
    from osh import cli

    importlib.reload(cli)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["--help"])

    assert result.exit_code == 0
    # Runtime lifecycle folds into ``osh init``/``osh stop`` — no per-runtime
    # command groups exist.
    assert "docker" not in cli.main.commands
    assert "venv" not in cli.main.commands
    # Help renders from metadata — the runtime plugins are never imported.
    from osh.utils.plugin_registry import plugin_registry

    specs = plugin_registry().specs
    assert not specs["osh-runtime-docker"].loaded
    assert not specs["osh-runtime-venv"].loaded


def test_group_plugin_subcommand_listed_in_separate_section(pip_install):
    """`osh db --help` sections plugin-provided subcommands too."""
    pip_install("repo_audit")

    from osh import cli

    importlib.reload(cli)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["db", "--help"])

    assert result.exit_code == 0
    core_section, _, plugin_section = result.output.partition("Plugin Commands:")
    assert "audit" not in core_section
    assert "audit" in plugin_section
    assert "[osh-audit]" in plugin_section


def test_double_collision_is_ignored(pip_install, capsys):
    """If even the prefixed name collides, the plugin command is skipped."""
    pip_install("repo_dup")

    from osh import cli

    importlib.reload(cli)

    assert "fake-init" in cli.main.commands
    assert "fake-fake-init" not in cli.main.commands
    assert (
        "plugin 'fake' command 'init' conflicts with an existing command and is ignored"
        in capsys.readouterr().err
    )


def test_collision_warns_on_every_load(pip_install, capsys):
    """A renamed plugin command prints a warning on every CLI load."""
    pip_install("fake_init")

    from osh import cli

    importlib.reload(cli)

    err = capsys.readouterr().err
    assert "plugin 'fake' command 'init' conflicts" in err
    assert "registered as 'fake-init'" in err


def test_group_subcommand_collision_renamed(pip_install, capsys):
    """A plugin group subcommand colliding on the target group is renamed."""
    pip_install("repo_dbshow")

    from osh import cli

    importlib.reload(cli)

    db_group = cli.main.commands["db"]
    assert db_group.commands["show"].callback.__module__ == "osh.commands.db_cmd"
    assert "osh-dbshow-show" in db_group.commands
    assert "'db.show' conflicts" in capsys.readouterr().err


def test_group_subcommand_unknown_group_is_skipped(pip_install, capsys):
    """A group command targeting a non-group command is skipped with an error."""
    pip_install("repo_odoosub")

    from osh import cli

    importlib.reload(cli)

    assert "not a command group" in capsys.readouterr().err
