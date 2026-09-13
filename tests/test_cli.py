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
