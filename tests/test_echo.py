"""Tests for terminal output formatting."""

from click.testing import CliRunner

from osh.cli import main
from osh.db import set_project_config


def test_cli_echo_output_colored_in_terminal(tmp_project, monkeypatch):
    """A real ``osh`` command prints styled categories to a terminal."""
    set_project_config(tmp_project, "run", "target", "nosuchbackend")
    monkeypatch.chdir(tmp_project)

    result = CliRunner().invoke(main, ["backend", "status"], color=True)

    assert "\x1b[" in result.output
    assert "nosuchbackend" in result.output
