"""Tests for ``osh init``'s status report on an initialized project."""

from click.testing import CliRunner

from osh.cli import main
from osh.db import set_project_config


def test_status_without_active_runtime(in_project):
    """``osh init`` reports host execution when no runtime is active."""
    result = CliRunner().invoke(main, ["init"])

    assert result.exit_code == 0, result.output
    assert "Active runtime: host" in result.output
    assert "commands run on the host" in result.output


def test_status_reports_recorded_version_and_edition(in_project):
    """``osh init`` reports the Odoo version and edition init recorded."""
    set_project_config(in_project, "init", "version", "19.0")
    set_project_config(in_project, "init", "edition", "ee")

    result = CliRunner().invoke(main, ["init"])

    assert result.exit_code == 0, result.output
    assert "19.0" in result.output
    assert "Enterprise" in result.output


def test_status_reports_active_runtime(in_project):
    """``osh init`` names the runtime recorded as run.runtime."""
    set_project_config(in_project, "run", "runtime", "venv")

    result = CliRunner().invoke(main, ["init"])

    assert result.exit_code == 0, result.output
    assert "Active runtime: venv" in result.output


def test_status_warns_when_runtime_unavailable(in_project):
    """A run.runtime naming an unloaded runtime is reported with a warning."""
    set_project_config(in_project, "run", "runtime", "missing-runtime")

    result = CliRunner().invoke(main, ["init"])

    assert result.exit_code == 0, result.output
    assert "Active runtime: missing-runtime" in result.output
    assert "not available" in result.output


def test_status_lists_available_runtimes(in_project):
    """``osh init`` lists the bundled runtimes it can switch to."""
    result = CliRunner().invoke(main, ["init"])

    assert result.exit_code == 0, result.output
    for name in ("host", "venv", "docker"):
        assert name in result.output


def test_status_makes_no_changes(in_project):
    """``osh init`` on an initialized project writes nothing."""
    result = CliRunner().invoke(main, ["init"])

    assert result.exit_code == 0, result.output
    assert not (in_project / ".osh" / "config").exists()


def test_status_outside_project_still_initialises(tmp_path, monkeypatch):
    """``osh init`` outside a project keeps its setup meaning."""
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["init", "19.0", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Would create" in result.output


def test_status_reads_legacy_run_target(in_project):
    """Projects recorded with ``run.target`` (pre-runtime) still resolve."""
    set_project_config(in_project, "run", "target", "venv")

    result = CliRunner().invoke(main, ["init"])

    assert result.exit_code == 0, result.output
    assert "Active runtime: venv" in result.output


def test_status_legacy_none_target_is_host(in_project):
    """A legacy ``run.target = none`` is reported as the host runtime."""
    set_project_config(in_project, "run", "target", "none")

    result = CliRunner().invoke(main, ["init"])

    assert result.exit_code == 0, result.output
    assert "Active runtime: host" in result.output


def test_run_runtime_takes_precedence_over_run_target(in_project):
    """When both keys exist, ``run.runtime`` wins."""
    set_project_config(in_project, "run", "target", "venv")
    set_project_config(in_project, "run", "runtime", "docker")

    result = CliRunner().invoke(main, ["init"])

    assert result.exit_code == 0, result.output
    assert "Active runtime: docker" in result.output


def test_version_argument_reinitialises(in_project, monkeypatch):
    """``osh init 19.0`` is setup, not status: the version is recorded."""
    result = CliRunner().invoke(main, ["init", "19.0", "--edition", "ce", "--yes"])

    assert result.exit_code == 0, result.output
    assert "Initialised" in result.output
    from osh.db import get_project_config

    assert get_project_config(in_project, "init", "version") == "19.0"


def test_status_not_shown_when_setup_options_given(in_project):
    """Setup options like ``--edition`` switch ``osh init`` back to setup."""
    set_project_config(in_project, "init", "version", "19.0")

    result = CliRunner().invoke(main, ["init", "--edition", "ee", "--yes"])

    assert result.exit_code == 0, result.output
    assert "Active runtime" not in result.output
    from osh.db import get_project_config

    assert get_project_config(in_project, "init", "edition") == "ee"


def test_old_runtime_commands_are_gone(in_project):
    """The retired command groups fail with an unknown-command error."""
    runner = CliRunner()
    for argv in (
        ["runtime", "status"],
        ["backend", "status"],
        ["venv", "init"],
        ["docker", "init"],
    ):
        result = runner.invoke(main, argv)
        assert result.exit_code != 0, argv
        assert "No such command" in result.output, argv
