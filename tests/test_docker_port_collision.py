"""Tests for Docker port-collision detection in ``ensure_service_up``."""

from types import SimpleNamespace

import click
import pytest

from osh.plugins.osh_runtime_docker.runtimes import DockerRuntime
from osh.runtimes import EnvSpec

from .conftest import _write_docker_config


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
        "osh.plugins.osh_runtime_docker.runtimes.run_subprocess",
        fake_run_subprocess,
    )
    monkeypatch.setattr(
        "osh.plugins.osh_runtime_docker.discovery.run_subprocess",
        fake_run_subprocess,
    )
    monkeypatch.setattr(
        "osh.plugins.osh_runtime_docker.runtimes.run_command", fake_run_command
    )
    monkeypatch.setattr(
        "osh.plugins.osh_runtime_docker.runtimes.port_in_use", lambda *a, **kw: True
    )
    # No host processes holding the port — the real /proc scan would make
    # the outcome depend on whatever the developer's machine is running.
    monkeypatch.setattr("osh.runtimes._port_listeners", lambda port: [])
    return calls


def test_port_collision_identifies_other_osh_project(tmp_project, monkeypatch):
    """A port held by another Osh project produces a targeted error."""
    _write_docker_config(tmp_project)
    other = tmp_project.parent / "other-project"
    (other / ".osh").mkdir(parents=True)
    (other / ".osh" / "docker.toml").write_text('service = "odoo"\n')
    labels = f"com.docker.compose.project.working_dir={other / '.osh'}"
    _patch_docker(monkeypatch, docker_ps_lines=[f"{labels}\t3 hours ago"])

    runtime = DockerRuntime()
    with pytest.raises(click.ClickException) as excinfo:
        runtime.ensure_service_up(tmp_project)

    message = excinfo.value.format_message()
    assert f"Port 8069 is already used by a container for {other}" in message
    assert "running 3 hours ago" in message
    assert "'osh docker stop'" in message
    assert "'osh odoo -p <n>'" in message


def test_port_collision_identifies_host_process(tmp_project, monkeypatch):
    """A port held by a host Odoo process is named in the error."""
    _write_docker_config(tmp_project)
    _patch_docker(monkeypatch)
    monkeypatch.setattr("osh.runtimes._port_listeners", lambda port: [4194304])
    monkeypatch.setattr(
        "osh.runtimes._pid_command",
        lambda pid: "/other/.venv/bin/odoo --dev=all",
    )

    runtime = DockerRuntime()
    with pytest.raises(click.ClickException) as excinfo:
        runtime.ensure_service_up(tmp_project)

    message = excinfo.value.format_message()
    assert "host Odoo process" in message
    assert "pid 4194304" in message
    assert "osh runtime stop" in message


def test_port_collision_unidentified_holder(tmp_project, monkeypatch):
    """An unidentifiable port holder produces the generic actionable error."""
    _write_docker_config(tmp_project)
    _patch_docker(monkeypatch, docker_ps_lines=["k8s_pod\t2 days ago"])

    runtime = DockerRuntime()
    with pytest.raises(click.ClickException) as excinfo:
        runtime.ensure_service_up(tmp_project)

    message = excinfo.value.format_message()
    assert "Port 8069 is already in use." in message
    assert "'osh docker stop'" in message
    assert "stop whatever's using port 8069" in message
    assert "'osh odoo -p <n>'" in message


def test_port_collision_uses_configured_port(tmp_project, monkeypatch):
    """The port configured in docker.toml is the one checked and reported."""
    _write_docker_config(tmp_project, port=18069)
    monkeypatch.setattr(
        "osh.plugins.osh_runtime_docker.runtimes.run_subprocess",
        lambda *a, **kw: (0, "", ""),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_runtime_docker.runtimes.run_command", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        "osh.plugins.osh_runtime_docker.runtimes.port_in_use", lambda *a, **kw: True
    )

    runtime = DockerRuntime()
    with pytest.raises(click.ClickException) as excinfo:
        runtime.ensure_service_up(tmp_project)

    assert "Port 18069 is already in use." in excinfo.value.format_message()


def test_ensure_service_up_noop_when_running(tmp_project, monkeypatch):
    """A running service short-circuits: no port check, no ``up -d``."""
    _write_docker_config(tmp_project)
    calls = _patch_docker(monkeypatch, running_ids="abc123\n")

    runtime = DockerRuntime()
    runtime.ensure_service_up(tmp_project)

    assert calls == []


def test_ensure_service_up_brings_stack_up(tmp_project, monkeypatch):
    """A stopped stack is started with ``compose up -d``."""
    _write_docker_config(tmp_project)
    calls = _patch_docker(monkeypatch)
    monkeypatch.setattr(
        "osh.plugins.osh_runtime_docker.runtimes.port_in_use",
        lambda *a, **kw: False,
    )

    runtime = DockerRuntime()
    runtime.ensure_service_up(tmp_project)

    assert len(calls) == 1
    # --build is a no-op for image-only stacks but rebuilds Dockerfile-based
    # ones, so the service picks up changes without a separate build step.
    assert calls[0][-3:] == ["up", "-d", "--build"]


def test_ensure_service_up_publishes_requested_port(tmp_project, monkeypatch):
    """``osh odoo -p 8080`` republishes the service on the requested port."""
    _write_docker_config(tmp_project)
    calls = _patch_docker(monkeypatch)
    ports_checked = []
    monkeypatch.setattr(
        "osh.plugins.osh_runtime_docker.runtimes.port_in_use",
        lambda port, **kw: ports_checked.append(port) or False,
    )

    runtime = DockerRuntime()
    runtime.ensure_service_up(tmp_project, port=8080)

    assert ports_checked == [8080]
    override = (tmp_project / ".osh" / "docker-compose.osh.yml").read_text()
    assert "ports: !override" in override
    assert '"8080:8080"' in override
    assert calls[0][-3:] == ["up", "-d", "--build"]


def test_odoo_http_port_parsing():
    """``odoo_http_port`` extracts the -p/--http-port value from Odoo args."""
    from osh.common import odoo_http_port

    assert odoo_http_port(["-p", "8080"]) == 8080
    assert odoo_http_port(["--http-port", "8080"]) == 8080
    assert odoo_http_port(["--http-port=8080"]) == 8080
    assert odoo_http_port(["-p8080"]) == 8080
    assert odoo_http_port(["-p", "junk"]) is None
    assert odoo_http_port(["shell"]) is None
    assert odoo_http_port([]) is None


def test_db_probe_keeps_requested_port(tmp_project, monkeypatch):
    """Internal probes publish the ctx-requested port, not the configured one."""
    _write_docker_config(tmp_project)
    _patch_docker(monkeypatch)
    runtime = DockerRuntime()
    calls = {}
    monkeypatch.setattr(
        runtime,
        "ensure_service_up",
        lambda base, compose_file=None, port=None: calls.setdefault("port", port),
    )
    monkeypatch.setattr(runtime, "_db_exec_args", lambda *a, **k: ["probe"])
    ctx = SimpleNamespace(params={}, obj={"http_port": 8081})

    runtime.db_env(ctx, tmp_project, EnvSpec(argv=["psql", "-l"]), capture=True)

    assert calls["port"] == 8081


def test_probe_reuses_override_port(tmp_project):
    """Probes keep the port the compose override already publishes."""
    from osh.plugins.osh_runtime_docker.runtimes import _requested_port

    _write_docker_config(tmp_project)
    (tmp_project / ".osh" / "docker-compose.osh.yml").write_text(
        'services:\n  odoo:\n    ports: !override\n      - "8080:8080"\n'
    )
    ctx = SimpleNamespace(params={}, obj={})

    assert _requested_port(ctx, tmp_project) == 8080
    assert _requested_port(ctx, tmp_project / "nope") is None


def _write_foreign_docker_config(project):
    """Write a docker runtime config pointing at a project compose file."""
    osh_dir = project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    (osh_dir / "docker.toml").write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
        'compose_file = "devel.yaml"\n'
    )
    (project / "devel.yaml").write_text("services:\n  odoo:\n")


def test_ensure_service_up_foreign_skips_port_check(tmp_project, monkeypatch):
    """A project compose file's own port mapping is authoritative.

    Without an explicit ``osh odoo -p``, checking the configured 8069 would
    flag a port the project's own compose file may not even publish —
    ``port_in_use`` is patched to report a collision, so reaching ``up``
    at all proves the check was skipped.
    """
    _write_foreign_docker_config(tmp_project)
    calls = _patch_docker(monkeypatch)

    runtime = DockerRuntime()
    runtime.ensure_service_up(tmp_project)

    assert len(calls) == 1
    assert calls[0][-3:] == ["up", "-d", "--build"]
    # Foreign stacks share the project's natural Compose project name.
    assert "-p" not in calls[0]


def test_ensure_service_up_foreign_checks_requested_port(tmp_project, monkeypatch):
    """``osh odoo -p`` on a foreign stack still gets the collision check."""
    _write_foreign_docker_config(tmp_project)
    _patch_docker(monkeypatch)
    ports_checked = []
    monkeypatch.setattr(
        "osh.plugins.osh_runtime_docker.runtimes.port_in_use",
        lambda port, **kw: ports_checked.append(port) or False,
    )

    runtime = DockerRuntime()
    runtime.ensure_service_up(tmp_project, port=8080)

    assert ports_checked == [8080]
    override = (tmp_project / ".osh" / "docker-compose.osh.yml").read_text()
    assert '"8080:8080"' in override


def test_env_port_semantics(tmp_project, monkeypatch):
    """``env()`` resets the port for Odoo runs, keeps it for other argv."""
    from osh.plugins.osh_runtime_docker.runtimes import _PORT_KEEP

    _write_docker_config(tmp_project)
    runtime = DockerRuntime()
    seen = []
    monkeypatch.setattr(
        runtime, "_exec_env", lambda *a, **k: seen.append(k.get("port"))
    )
    ctx = SimpleNamespace(params={}, obj={})

    runtime.env(ctx, tmp_project, EnvSpec(argv=["odoo", "-p", "8081"]))
    runtime.env(ctx, tmp_project, EnvSpec(argv=["odoo", "--dev=all"]))
    runtime.env(ctx, tmp_project, EnvSpec(argv=["odoo", "shell", "-d", "x"]))
    runtime.env(ctx, tmp_project, EnvSpec(argv=[]))

    assert seen == [8081, None, _PORT_KEEP, _PORT_KEEP]
