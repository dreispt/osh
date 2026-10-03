"""Tests for ``osh runtime status`` and ``osh runtime list``."""

from click.testing import CliRunner

from osh.cli import main
from osh.db import set_project_config


def test_status_without_active_backend(in_project):
    """``osh runtime status`` reports host execution when nothing is active.

    The wording matches the ``host (active)`` marker ``osh runtime list``
    prints, so the two commands never contradict each other.
    """
    result = CliRunner().invoke(main, ["runtime", "status"])

    assert result.exit_code == 0, result.output
    assert "Active runtime: host" in result.output
    assert "commands run on the host" in result.output


def test_status_reports_active_backend(in_project):
    """``osh runtime status`` names the backend recorded as run.runtime."""
    set_project_config(in_project, "run", "runtime", "venv")

    result = CliRunner().invoke(main, ["runtime", "status"])

    assert result.exit_code == 0, result.output
    assert "Active runtime: venv" in result.output


def test_status_warns_when_backend_unavailable(in_project):
    """A run.runtime naming an unloaded backend is reported with a warning."""
    set_project_config(in_project, "run", "runtime", "missing-backend")

    result = CliRunner().invoke(main, ["runtime", "status"])

    assert result.exit_code == 0, result.output
    assert "Active runtime: missing-backend" in result.output
    assert "not available" in result.output


def test_list_shows_all_backends(in_project):
    """``osh runtime list`` lists the built-in and bundled plugin backends."""
    result = CliRunner().invoke(main, ["runtime", "list"])

    assert result.exit_code == 0, result.output
    assert "host" in result.output
    assert "venv" in result.output
    assert "docker" in result.output


def test_list_marks_active_backend(in_project):
    """``osh runtime list`` marks the project's active backend."""
    set_project_config(in_project, "run", "runtime", "docker")

    result = CliRunner().invoke(main, ["runtime", "list"])

    assert result.exit_code == 0, result.output
    assert "docker (active)" in result.output
    assert "venv (active)" not in result.output


def test_list_marks_host_as_default_active(in_project):
    """Without a run.runtime the ``host`` runtime is the active one."""
    result = CliRunner().invoke(main, ["runtime", "list"])

    assert result.exit_code == 0, result.output
    assert "host (active)" in result.output


def test_list_outside_project_marks_nothing(tmp_path, monkeypatch):
    """Outside a project, ``osh runtime list`` still works, with no marker."""
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["runtime", "list"])

    assert result.exit_code == 0, result.output
    assert "host" in result.output
    assert "(active)" not in result.output


def test_status_requires_project(tmp_path, monkeypatch):
    """``osh runtime status`` outside a project prints the no-project message.

    ``find_project_root(required=True)`` exits 0 by design — not being in a
    project is a normal state to report, not an error.
    """
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["runtime", "status"])

    assert result.exit_code == 0, result.output
    assert "Not inside an Osh project" in result.output
    assert "Active runtime" not in result.output


def test_status_reads_legacy_run_target(in_project):
    """Projects recorded with ``run.target`` (pre-runtime) still resolve."""
    set_project_config(in_project, "run", "target", "venv")

    result = CliRunner().invoke(main, ["runtime", "status"])

    assert result.exit_code == 0, result.output
    assert "Active runtime: venv" in result.output


def test_status_legacy_none_target_is_host(in_project):
    """A legacy ``run.target = none`` is reported as the host runtime."""
    set_project_config(in_project, "run", "target", "none")

    result = CliRunner().invoke(main, ["runtime", "list"])

    assert result.exit_code == 0, result.output
    assert "host (active)" in result.output


def test_run_runtime_takes_precedence_over_run_target(in_project):
    """When both keys exist, ``run.runtime`` wins."""
    set_project_config(in_project, "run", "target", "venv")
    set_project_config(in_project, "run", "runtime", "docker")

    result = CliRunner().invoke(main, ["runtime", "status"])

    assert "Active runtime: docker" in result.output


def test_backend_alias_is_hidden_and_deprecated(in_project):
    """``osh backend`` still works, warns it is deprecated and is not in help."""
    runner = CliRunner()
    result = runner.invoke(main, ["backend", "status"])

    assert result.exit_code == 0, result.output
    assert "Active runtime: host" in result.output
    assert "deprecated" in result.output

    help_output = runner.invoke(main, ["--help"]).output
    assert "\n  runtime " in help_output
    assert "\n  backend " not in help_output
