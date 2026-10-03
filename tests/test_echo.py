"""Tests for terminal output formatting."""

from click.testing import CliRunner

from osh.cli import main
from osh.db import set_project_config


def test_cli_echo_output_colored_in_terminal(tmp_project, monkeypatch):
    """A real ``osh`` command prints styled categories to a terminal.

    Pointing the project at a backend that isn't installed makes
    ``osh backend status`` emit its messages — styled when the terminal
    supports color, plain text when the output is piped.
    """
    set_project_config(tmp_project, "run", "target", "nosuchbackend")
    monkeypatch.chdir(tmp_project)

    colored = CliRunner().invoke(main, ["backend", "status"], color=True)

    assert "\x1b[" in colored.output  # ANSI styling applied
    assert "nosuchbackend" in colored.output

    piped = CliRunner().invoke(main, ["backend", "status"])

    assert "\x1b[" not in piped.output
    assert "nosuchbackend" in piped.output
