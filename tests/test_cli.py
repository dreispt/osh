"""Tests for the ``osh`` root group: --version and global flags."""

import re
from pathlib import Path

from click.testing import CliRunner

from osh.cli import main


def test_version_flag_prints_version():
    """``osh --version`` prints the installed version."""
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0, result.output
    assert re.search(r"osh, version \d+\.\d+", result.output)


def test_version_flag_includes_git_hash_in_checkout():
    """Inside a git checkout, ``osh --version`` appends the commit hash."""
    osh_repo = Path(__file__).resolve().parent.parent

    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0, result.output
    if (osh_repo / ".git").exists():
        assert re.search(r"\+g?[0-9a-f]{7,}", result.output)


def test_version_subcommand_removed():
    """There is no separate ``osh version`` command."""
    result = CliRunner().invoke(main, ["version"])

    assert result.exit_code != 0
    assert "No such command" in result.output


def test_global_flags_are_mutually_exclusive():
    """Passing any two of --silent/--verbose/--debug is a usage error."""
    runner = CliRunner()
    for flags in (
        ["--silent", "--verbose"],
        ["--verbose", "--debug"],
        ["--silent", "--debug"],
        ["--silent", "--verbose", "--debug"],
    ):
        result = runner.invoke(main, flags + ["db"])
        assert result.exit_code != 0, flags
        assert "mutually exclusive" in result.output


def test_silent_and_verbose_map_to_levels(monkeypatch):
    """Global flags map onto the internal verbosity levels."""
    seen = []

    def _capture(verbosity=None, base=None):
        seen.append(verbosity)

    monkeypatch.setattr("osh.cli.echo._set_config", _capture)

    runner = CliRunner()
    runner.invoke(main, ["--silent", "db"])
    runner.invoke(main, ["--verbose", "db"])
    runner.invoke(main, ["--debug", "db"])
    runner.invoke(main, ["db"])

    assert seen == ["silent", "verbose", "debug", None]
