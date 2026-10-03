"""Tests for ``osh <runtime> activate`` and ``osh runtime activate|deactivate``."""

from click.testing import CliRunner

from osh.cli import main
from osh.db import get_project_config

from .conftest import _write_docker_config


def _active_target(project):
    return get_project_config(project, "run", "runtime")


def test_deactivate_without_backend_is_noop(in_project):
    """``osh runtime deactivate`` reports when no backend is active."""
    result = CliRunner().invoke(main, ["runtime", "deactivate"])

    assert result.exit_code == 0, result.output
    assert "No runtime is active" in result.output
    assert _active_target(in_project) is None


def test_deactivate_records_host_runtime(in_project):
    """``osh runtime deactivate`` switches an active project back to the host."""
    from osh.db import set_project_config

    set_project_config(in_project, "run", "runtime", "venv")
    result = CliRunner().invoke(main, ["runtime", "deactivate"])

    assert result.exit_code == 0, result.output
    assert "'venv' deactivated" in result.output
    assert _active_target(in_project) == "host"


def test_activate_venv_records_run_runtime(in_project):
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


def test_activate_docker_records_run_runtime(in_project, monkeypatch):
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
    assert runner.invoke(main, ["runtime", "deactivate"]).exit_code == 0
    assert _active_target(in_project) == "host"


def test_deactivate_legacy_run_target(in_project):
    """Deactivating a project recorded with legacy ``run.target`` works."""
    from osh.db import set_project_config

    set_project_config(in_project, "run", "target", "venv")
    result = CliRunner().invoke(main, ["runtime", "deactivate"])

    assert result.exit_code == 0, result.output
    assert "'venv' deactivated" in result.output
    assert _active_target(in_project) == "host"


def test_runtime_activate_by_name(in_project):
    """``osh runtime activate venv`` is equivalent to ``osh venv activate``."""
    result = CliRunner().invoke(main, ["runtime", "activate", "venv"])

    assert result.exit_code == 0, result.output
    assert _active_target(in_project) == "venv"
    assert get_project_config(in_project, "run", "target") is None


def test_runtime_activate_host_and_legacy_none(in_project):
    """``host`` and its legacy name ``none`` both activate the host runtime."""
    runner = CliRunner()
    for name in ("host", "none"):
        result = runner.invoke(main, ["runtime", "activate", name])
        assert result.exit_code == 0, result.output
        assert _active_target(in_project) == "host"


def test_runtime_activate_unknown(in_project):
    """Activating an unknown runtime fails without recording it."""
    result = CliRunner().invoke(main, ["runtime", "activate", "nope"])

    assert result.exit_code != 0
    assert "No runtime named 'nope'" in result.output
    assert _active_target(in_project) is None


def test_backend_alias_deactivate(in_project):
    """The deprecated ``osh backend`` alias still switches back to host."""
    from osh.db import set_project_config

    set_project_config(in_project, "run", "runtime", "venv")
    result = CliRunner().invoke(main, ["backend", "deactivate"])

    assert result.exit_code == 0, result.output
    assert _active_target(in_project) == "host"
