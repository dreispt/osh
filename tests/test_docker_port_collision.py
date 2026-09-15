"""Tests for Docker port-collision detection in ``ensure_service_up``."""

import click
import pytest

from osh.plugins.osh_backend_docker.backends import DockerBackend


def _write_docker_config(project, port=None):
    osh_dir = project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    text = 'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    if port:
        text += f"port = {port}\n"
    (osh_dir / "docker.toml").write_text(text)
    (osh_dir / "docker-compose.yml").write_text("services:\n  odoo:\n")


def _patch_docker(monkeypatch, *, running_ids="", docker_ps_lines=()):
    """Patch the subprocess calls ``ensure_service_up`` makes.

    ``running_ids`` is the output of ``compose ps --status running -q`` — a
    non-empty value means the service container is already up.
    ``docker_ps_lines`` is the ``docker ps --filter publish=...`` output used
    to identify the holder of a bound port.
    """
    calls = []

    def fake_run_subprocess(args, **kwargs):
        if "ps" in args and any("publish=" in str(a) for a in args):
            return 0, "\n".join(docker_ps_lines), ""
        if "ps" in args and "--status" in args:
            return 0, running_ids, ""
        return 0, "", ""

    def fake_run_command(args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends.run_subprocess",
        fake_run_subprocess,
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.utils.run_subprocess",
        fake_run_subprocess,
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends.run_command", fake_run_command
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends._port_in_use", lambda *a, **kw: True
    )
    return calls


def test_port_collision_identifies_other_osh_project(tmp_project, monkeypatch):
    """A port held by another Osh project produces a targeted error."""
    _write_docker_config(tmp_project)
    other = tmp_project.parent / "other-project"
    (other / ".osh").mkdir(parents=True)
    (other / ".osh" / "docker.toml").write_text('service = "odoo"\n')
    labels = f"com.docker.compose.project.working_dir={other / '.osh'}"
    _patch_docker(monkeypatch, docker_ps_lines=[f"{labels}\t3 hours ago"])

    backend = DockerBackend()
    with pytest.raises(click.ClickException) as excinfo:
        backend.ensure_service_up(tmp_project)

    message = excinfo.value.format_message()
    assert f"Port 8069 is already used by a container for {other}" in message
    assert "running 3 hours ago" in message
    assert "'osh docker stop'" in message
    assert "'osh docker init --port <n>'" in message


def test_port_collision_unidentified_holder(tmp_project, monkeypatch):
    """An unidentifiable port holder produces the generic actionable error."""
    _write_docker_config(tmp_project)
    _patch_docker(monkeypatch, docker_ps_lines=["k8s_pod\t2 days ago"])

    backend = DockerBackend()
    with pytest.raises(click.ClickException) as excinfo:
        backend.ensure_service_up(tmp_project)

    message = excinfo.value.format_message()
    assert "Port 8069 is already in use." in message
    assert "'osh docker stop'" in message
    assert "stop whatever's using port 8069" in message
    assert "'osh docker init --port <n>'" in message


def test_port_collision_uses_configured_port(tmp_project, monkeypatch):
    """The port configured in docker.toml is the one checked and reported."""
    _write_docker_config(tmp_project, port=18069)
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends.run_subprocess",
        lambda *a, **kw: (0, "", ""),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends.run_command", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends._port_in_use", lambda *a, **kw: True
    )

    backend = DockerBackend()
    with pytest.raises(click.ClickException) as excinfo:
        backend.ensure_service_up(tmp_project)

    assert "Port 18069 is already in use." in excinfo.value.format_message()


def test_ensure_service_up_noop_when_running(tmp_project, monkeypatch):
    """A running service short-circuits: no port check, no ``up -d``."""
    _write_docker_config(tmp_project)
    calls = _patch_docker(monkeypatch, running_ids="abc123\n")

    backend = DockerBackend()
    backend.ensure_service_up(tmp_project)

    assert calls == []


def test_ensure_service_up_brings_stack_up(tmp_project, monkeypatch):
    """A stopped stack is started with ``compose up -d``."""
    _write_docker_config(tmp_project)
    calls = _patch_docker(monkeypatch)
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends._port_in_use",
        lambda *a, **kw: False,
    )

    backend = DockerBackend()
    backend.ensure_service_up(tmp_project)

    assert len(calls) == 1
    assert calls[0][-2:] == ["up", "-d"]
