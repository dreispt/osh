"""Tests for ``osh backend status`` and ``osh backend list``."""

from click.testing import CliRunner

from osh.cli import main
from osh.db import set_project_config


def test_status_without_active_backend(in_project):
    """``osh backend status`` reports host execution when nothing is active.

    The wording matches the ``none (active)`` marker ``osh backend list``
    prints, so the two commands never contradict each other.
    """
    result = CliRunner().invoke(main, ["backend", "status"])

    assert result.exit_code == 0, result.output
    assert "Active backend: none" in result.output
    assert "commands run on the host" in result.output


def test_status_reports_active_backend(in_project):
    """``osh backend status`` names the backend recorded as run.target."""
    set_project_config(in_project, "run", "target", "venv")

    result = CliRunner().invoke(main, ["backend", "status"])

    assert result.exit_code == 0, result.output
    assert "Active backend: venv" in result.output


def test_status_warns_when_backend_unavailable(in_project):
    """A run.target naming an unloaded backend is reported with a warning."""
    set_project_config(in_project, "run", "target", "missing-backend")

    result = CliRunner().invoke(main, ["backend", "status"])

    assert result.exit_code == 0, result.output
    assert "Active backend: missing-backend" in result.output
    assert "not available" in result.output


def test_list_shows_all_backends(in_project):
    """``osh backend list`` lists the built-in and bundled plugin backends."""
    result = CliRunner().invoke(main, ["backend", "list"])

    assert result.exit_code == 0, result.output
    assert "none" in result.output
    assert "venv" in result.output
    assert "docker" in result.output


def test_list_marks_active_backend(in_project):
    """``osh backend list`` marks the project's active backend."""
    set_project_config(in_project, "run", "target", "docker")

    result = CliRunner().invoke(main, ["backend", "list"])

    assert result.exit_code == 0, result.output
    assert "docker (active)" in result.output
    assert "venv (active)" not in result.output


def test_list_marks_none_as_default_active(in_project):
    """Without a run.target the ``none`` backend is the active one."""
    result = CliRunner().invoke(main, ["backend", "list"])

    assert result.exit_code == 0, result.output
    assert "none (active)" in result.output


def test_list_outside_project_marks_nothing(tmp_path, monkeypatch):
    """Outside a project, ``osh backend list`` still works, with no marker."""
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["backend", "list"])

    assert result.exit_code == 0, result.output
    assert "none" in result.output
    assert "(active)" not in result.output


def test_status_requires_project(tmp_path, monkeypatch):
    """``osh backend status`` outside a project prints the no-project message.

    ``find_project_root(required=True)`` exits 0 by design — not being in a
    project is a normal state to report, not an error.
    """
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["backend", "status"])

    assert result.exit_code == 0, result.output
    assert "Not inside an Osh project" in result.output
    assert "Active backend" not in result.output
