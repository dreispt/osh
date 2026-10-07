"""Tests for Docker port-collision detection in ``ensure_service_up``."""

from types import SimpleNamespace

import click
import pytest

from osh.plugins.osh_runtime_docker.runtimes import DockerRuntime
from osh.runtimes import EnvSpec

from .conftest import _free_port, _odoo_argv, _write_docker_config


def _docker_calls(fake_docker):
    return (
        (fake_docker / "calls.log").read_text()
        if (fake_docker / "calls.log").exists()
        else ""
    )


def test_port_collision_identifies_other_osh_project(
    tmp_project, fake_docker, held_port
):
    """A port held by another Osh project produces a targeted error."""
    port = held_port()
    _write_docker_config(tmp_project, port=port)
    other = tmp_project.parent / "other-project"
    (other / ".osh").mkdir(parents=True)
    (other / ".osh" / "docker.toml").write_text('service = "odoo"\n')
    labels = f"com.docker.compose.project.working_dir={other / '.osh'}"
    (fake_docker / "docker_ps").write_text(f"{labels}\t3 hours ago\n")

    with pytest.raises(click.ClickException) as excinfo:
        DockerRuntime().ensure_service_up(tmp_project)

    message = excinfo.value.format_message()
    assert f"Port {port} is already used by a container for {other}" in message
    assert "osh stop" in message
    assert "osh odoo -p" in message
    assert "up -d" not in _docker_calls(fake_docker)


def test_port_collision_identifies_host_process(
    tmp_project, fake_docker, tmp_path, spawn
):
    """A port held by a host Odoo process is named in the error."""
    port = _free_port()
    _write_docker_config(tmp_project, port=port)
    spawn(_odoo_argv(tmp_path, port, "odoo-bin"), port)

    with pytest.raises(click.ClickException) as excinfo:
        DockerRuntime().ensure_service_up(tmp_project)

    message = excinfo.value.format_message()
    assert f"Port {port} is already used by" in message
    assert "host Odoo process" in message
    assert "pid" in message
    assert "osh stop" in message
    assert "up -d" not in _docker_calls(fake_docker)


def test_port_collision_unidentified_holder(
    tmp_project, fake_docker, held_port, monkeypatch
):
    """An unidentifiable port holder produces the generic actionable error."""
    port = held_port()
    _write_docker_config(tmp_project, port=port)
    (fake_docker / "docker_ps").write_text("k8s_pod\t2 days ago\n")
    # The holder is invisible to the process scan — e.g. another user.
    monkeypatch.setattr("osh.runtimes._port_listeners", lambda port: [])

    with pytest.raises(click.ClickException) as excinfo:
        DockerRuntime().ensure_service_up(tmp_project)

    message = excinfo.value.format_message()
    assert f"Port {port} is already in use" in message
    assert "osh stop" in message
    assert "osh odoo -p" in message
    assert "up -d" not in _docker_calls(fake_docker)


def test_port_collision_uses_configured_port(
    tmp_project, fake_docker, held_port, monkeypatch
):
    """The checked port comes from ``.osh/docker.toml`` ``port``."""
    port = held_port()
    _write_docker_config(tmp_project, port=port)
    monkeypatch.setattr("osh.runtimes._port_listeners", lambda port: [])

    with pytest.raises(click.ClickException, match=f"Port {port}"):
        DockerRuntime().ensure_service_up(tmp_project)

    assert "up -d" not in _docker_calls(fake_docker)


def test_ensure_service_up_noop_when_service_running(tmp_project, fake_docker):
    """``ensure_service_up`` is a no-op when the service already runs."""
    _write_docker_config(tmp_project)
    (fake_docker / "ps").write_text("abc123\n")

    DockerRuntime().ensure_service_up(tmp_project)

    assert "ps" in _docker_calls(fake_docker)
    assert "up -d" not in _docker_calls(fake_docker)


def test_ensure_service_up_brings_stack_up(tmp_project, fake_docker):
    """With a free port the stack is started."""
    _write_docker_config(tmp_project, port=_free_port())

    DockerRuntime().ensure_service_up(tmp_project)

    assert "up -d" in _docker_calls(fake_docker)


def test_ensure_service_up_publishes_requested_port(tmp_project, fake_docker):
    """``ensure_service_up(port=…)`` re-publishes Odoo's port instead of
    checking it."""
    port = _free_port()
    _write_docker_config(tmp_project)

    DockerRuntime().ensure_service_up(tmp_project, port=port)

    override = tmp_project / ".osh" / "docker-compose.osh.yml"
    assert f'"{port}:{port}"' in override.read_text()
    assert "up -d" in _docker_calls(fake_docker)


def test_ensure_service_up_foreign_compose_skips_port_check(
    tmp_project, fake_docker, held_port
):
    """A foreign project never gets the collision check."""
    (tmp_project / "docker-compose.yml").write_text("services:\n  odoo:\n")
    osh_dir = tmp_project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    (osh_dir / "docker.toml").write_text(
        'service = "odoo"\ncompose_tool = "docker compose"\n'
    )
    held_port()  # a port collision would fail if it were checked

    DockerRuntime().ensure_service_up(tmp_project)

    assert "up -d" in _docker_calls(fake_docker)


def test_ensure_service_up_foreign_compose_checks_requested_port(
    tmp_project, fake_docker
):
    """``ensure_service_up(port=…)`` re-publishes even a foreign project."""
    (tmp_project / "docker-compose.yml").write_text("services:\n  odoo:\n")
    osh_dir = tmp_project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    (osh_dir / "docker.toml").write_text(
        'service = "odoo"\ncompose_tool = "docker compose"\n'
    )
    port = _free_port()

    DockerRuntime().ensure_service_up(tmp_project, port=port)

    override = tmp_project / ".osh" / "docker-compose.osh.yml"
    assert f'"{port}:{port}"' in override.read_text()
    assert "up -d" in _docker_calls(fake_docker)


def test_db_probe_keeps_requested_port(tmp_project, fake_docker, held_port):
    """``db_env`` uses the CLI's requested port even when the config port
    is taken."""
    _write_docker_config(tmp_project, port=held_port())
    ctx = SimpleNamespace(params={}, obj={"http_port": _free_port()})

    DockerRuntime().db_env(ctx, tmp_project, EnvSpec(argv=["psql", "-l"]), capture=True)

    assert "psql" in _docker_calls(fake_docker)


def test_probe_reuses_override_port(tmp_project, fake_docker, held_port):
    """Probes keep the port the compose override already publishes."""
    port = held_port()
    _write_docker_config(tmp_project)
    (tmp_project / ".osh" / "docker-compose.osh.yml").write_text(
        "services:\n  odoo:\n    ports: !override\n" f'      - "{port}:8069"\n'
    )
    ctx = SimpleNamespace(params={}, obj={})

    with pytest.raises(click.ClickException, match=f"Port {port}"):
        DockerRuntime().db_env(
            ctx, tmp_project, EnvSpec(argv=["psql", "-l"]), capture=True
        )


def test_env_checks_the_requested_port(tmp_project, fake_docker, held_port):
    """``env()`` checks the port a server run publishes, not the other way
    around."""
    configured = held_port()
    _write_docker_config(tmp_project, port=configured)
    runtime = DockerRuntime()
    ctx = SimpleNamespace(params={}, obj={})

    # A server run re-publishes its own -p port, ignoring the configured one.
    requested = _free_port()
    runtime.env(
        ctx,
        tmp_project,
        EnvSpec(argv=["odoo", "-p", str(requested), "-d", "testdb"]),
        capture=True,
    )
    assert "exec" in _docker_calls(fake_docker)

    # A plain server run does hit the configured port check.
    with pytest.raises(click.ClickException, match=f"Port {configured}"):
        runtime.env(ctx, tmp_project, EnvSpec(argv=["odoo", "--dev=all"]), capture=True)
