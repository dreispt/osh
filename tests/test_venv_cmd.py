"""Tests for `osh venv` command."""

import os

from click.testing import CliRunner

from osh.commands.venv_cmd import venv


def _setup_venv(project):
    """Create a minimal ``.venv/bin`` directory for *project*."""
    venv_bin = project / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    (venv_bin / "python").write_text("#!/bin/sh\necho python")
    (venv_bin / "python").chmod(0o755)
    return venv_bin


def test_venv_opens_interactive_shell(tmp_project, monkeypatch):
    """``osh venv`` with no arguments launches the user's shell in the venv."""
    venv_bin = _setup_venv(tmp_project)
    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))

    calls = []
    monkeypatch.setattr(
        "osh.commands.venv_cmd.os.execvp",
        lambda exe, args: calls.append((exe, list(args))),
    )

    runner = CliRunner()
    result = runner.invoke(venv, [])

    assert result.exit_code == 0, result.output
    assert calls == [("/bin/zsh", ["/bin/zsh"])]
    assert os.environ["VIRTUAL_ENV"] == str(tmp_project / ".venv")
    assert os.environ["PATH"].startswith(str(venv_bin) + os.pathsep)


def test_venv_runs_command_in_venv(tmp_project, monkeypatch):
    """``osh venv <cmd>`` runs the command with .venv/bin first on PATH."""
    venv_bin = _setup_venv(tmp_project)
    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))

    calls = []
    monkeypatch.setattr(
        "osh.commands.venv_cmd.os.execvp",
        lambda exe, args: calls.append((exe, list(args))),
    )

    runner = CliRunner()
    result = runner.invoke(venv, ["python", "--version"])

    assert result.exit_code == 0, result.output
    assert calls == [("python", ["python", "--version"])]
    assert os.environ["VIRTUAL_ENV"] == str(tmp_project / ".venv")
    assert os.environ["PATH"].startswith(str(venv_bin) + os.pathsep)


def test_venv_strips_leading_dashdash(tmp_project, monkeypatch):
    """``osh venv -- <cmd>`` strips the leading ``--`` separator."""
    _setup_venv(tmp_project)
    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))

    calls = []
    monkeypatch.setattr(
        "osh.commands.venv_cmd.os.execvp",
        lambda exe, args: calls.append((exe, list(args))),
    )

    runner = CliRunner()
    result = runner.invoke(venv, ["--", "pytest", "tests/"])

    assert result.exit_code == 0, result.output
    assert calls == [("pytest", ["pytest", "tests/"])]


def test_venv_fails_without_virtualenv(tmp_project, monkeypatch):
    """``osh venv`` fails if the project has no ``.venv``."""
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(venv, ["python"])

    assert result.exit_code == 1
    assert "No virtualenv found" in result.output
