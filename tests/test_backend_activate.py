"""Tests for ``osh <backend> activate`` and ``osh backend deactivate``."""

from click.testing import CliRunner

from osh.cli import main
from osh.db import get_project_config


def _write_docker_config(project):
    osh_dir = project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    (osh_dir / "docker.toml").write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )
    (osh_dir / "docker-compose.yml").write_text("services:\n  odoo:\n")


def _active_target(project):
    return get_project_config(project, "run", "target")


def test_deactivate_without_backend_is_noop(in_project):
    """``osh backend deactivate`` reports when no backend is active."""
    result = CliRunner().invoke(main, ["backend", "deactivate"])

    assert result.exit_code == 0, result.output
    assert "No backend is active" in result.output
    assert _active_target(in_project) is None


def test_deactivate_records_none_backend(in_project):
    """``osh backend deactivate`` switches an active project back to the host."""
    from osh.db import set_project_config

    set_project_config(in_project, "run", "target", "venv")
    result = CliRunner().invoke(main, ["backend", "deactivate"])

    assert result.exit_code == 0, result.output
    assert "'venv' deactivated" in result.output
    assert _active_target(in_project) == "none"


def test_activate_venv_records_run_target(in_project):
    """``osh venv activate`` records 'venv' as the project's run backend."""
    result = CliRunner().invoke(main, ["venv", "activate"])

    assert result.exit_code == 0, result.output
    assert _active_target(in_project) == "venv"


def test_activate_docker_requires_init(in_project):
    """``osh docker activate`` fails when the backend was never initialized."""
    result = CliRunner().invoke(main, ["docker", "activate"])

    assert result.exit_code != 0
    assert "osh docker init" in result.output
    assert _active_target(in_project) is None


def test_activate_docker_records_run_target(in_project, monkeypatch):
    """``osh docker activate`` on an initialized project records 'docker'."""
    _write_docker_config(in_project)
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.utils._find_compose_tool",
        lambda: ["docker", "compose"],
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends.run_subprocess",
        lambda *a, **kw: (0, "", ""),
    )

    result = CliRunner().invoke(main, ["docker", "activate"])

    assert result.exit_code == 0, result.output
    assert _active_target(in_project) == "docker"


def test_activate_switches_active_backend(in_project, monkeypatch):
    """Activating a different backend replaces the recorded run backend."""
    _write_docker_config(in_project)
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.utils._find_compose_tool",
        lambda: ["docker", "compose"],
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends.run_subprocess",
        lambda *a, **kw: (0, "", ""),
    )

    runner = CliRunner()
    assert runner.invoke(main, ["docker", "activate"]).exit_code == 0
    assert _active_target(in_project) == "docker"
    assert runner.invoke(main, ["backend", "deactivate"]).exit_code == 0
    assert _active_target(in_project) == "none"
