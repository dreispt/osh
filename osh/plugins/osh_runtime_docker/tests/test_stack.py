"""Tests for stack lifecycle: naming, overrides, ``up``, port collisions."""

import json
import types

import click
import pytest
from click.testing import CliRunner

from osh.cli import main
from osh.plugins.osh_runtime_docker.runtimes import DockerRuntime
from osh.runtimes import EnvSpec
from tests.helpers import _free_port, _odoo_argv

from .conftest import (
    _compose,
    _compose_projects,
    _docker_calls,
    _docker_ps_line,
    _running_containers,
    _started_at,
    _write_docker_config,
)


def test_generated_stack_runs_under_osh_project(tmp_project, capsys):
    """The generated stack runs under the isolated ``osh-*`` project name."""
    _write_docker_config(tmp_project)

    DockerRuntime().env(None, tmp_project, EnvSpec(argv=["odoo"]), dry_run=True)

    assert " -p osh-" in capsys.readouterr().err


def test_foreign_compose_shares_project_name(tmp_project, capsys):
    """A project compose file runs under its natural Compose project (no -p)."""
    (tmp_project / ".osh" / "docker.toml").write_text(
        "service = 'odoo'\ncompose_tool = 'docker compose'\n"
        "compose_file = 'devel.yaml'\n"
    )
    (tmp_project / "devel.yaml").write_text("services:\n  odoo:\n")

    DockerRuntime().env(None, tmp_project, EnvSpec(argv=["odoo"]), dry_run=True)

    err = capsys.readouterr().err
    assert str(tmp_project / "devel.yaml") in err
    assert " -p " not in err


def test_compose_override_foreign_injects_contract(docker_shared_project):
    """Foreign compose files get the idle command and the project mount."""
    # The shared stack's ensure_service_up already wrote the override.
    override = (docker_shared_project / ".osh" / "docker-compose.osh.yml").read_text()
    assert 'command: ["sleep", "infinity"]' in override
    # Host paths are quoted so spaces do not break the volume spec, and
    # POSIX-formatted so the mount works on Windows too.
    assert (
        f'"{docker_shared_project.resolve().as_posix()}:/mnt/extra-addons"' in override
    )
    assert "\\" not in override


def test_compose_override_generated_stack_needs_none(docker_daemon, tmp_project):
    """The generated stack carries the contract itself — and no db to wait on."""
    _write_docker_config(tmp_project)
    (tmp_project / ".osh" / "docker-compose.yml").write_text(
        "services:\n  odoo:\n    image: busybox\n    command: sleep infinity\n"
    )

    DockerRuntime().ensure_service_up(tmp_project)

    assert not (tmp_project / ".osh" / "docker-compose.osh.yml").exists()
    # The generated stack runs under osh's own ``-p osh-*`` project name.
    compose_file = str(tmp_project / ".osh" / "docker-compose.yml")
    assert any(
        compose_file in project.get("ConfigFiles", "")
        for project in _compose_projects()
    )


def test_docker_init_rebuilds_stale_image(docker_project):
    """A user who edits the project Dockerfile re-runs init to refresh it.

    Init runs ``compose build`` on stacks that declare ``build:``
    services, so re-initialising picks up Dockerfile edits without a
    stack restart.
    """
    from osh.commands.init_cmd import TodoPlan

    runtime = DockerRuntime()
    runtime.init(
        docker_project,
        version="19.0",
        service="app",
        command="true",
        todo=TodoPlan(None),
    )

    dockerfile = docker_project / "odoo" / "Dockerfile"
    dockerfile.write_text(
        dockerfile.read_text() + "RUN echo init-built > /init-marker\n"
    )
    runtime.init(
        docker_project,
        version="19.0",
        service="app",
        command="true",
        todo=TodoPlan(None),
    )

    result = _compose(docker_project, "run", "--rm", "app", "cat", "/init-marker")
    assert result.returncode == 0
    assert "init-built" in result.stdout


def _buildable_project(tmp_project, fake_docker):
    """A project whose foreign compose stack builds ``odoo`` from ``odoo/``."""
    (tmp_project / "docker-compose.yml").write_text(
        "services:\n  odoo:\n    build:\n      context: odoo\n"
    )
    context = tmp_project / "odoo"
    (context / "src").mkdir(parents=True)
    (context / "Dockerfile").write_text("FROM scratch\n")
    (context / "requirements.txt").write_text("requests\n")
    (context / "src" / "module.py").write_text("# addons\n")
    (context / "src" / "requirements.txt").write_text("lxml\n")
    (tmp_project / ".osh" / "docker.toml").write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )
    # ``compose config`` output for hosts without a real Docker to run it.
    (fake_docker / "config.json").write_text(
        json.dumps(
            {
                "name": "project",
                "services": {
                    "odoo": {
                        "build": {"context": str(context)},
                        "image": "project-odoo",
                    }
                },
            }
        )
    )


def _up_calls(fake_docker):
    """The ``compose up -d`` invocations the canned-answer docker recorded."""
    return [line for line in _docker_calls(fake_docker) if " up -d" in line]


def test_cold_start_warns_instead_of_rebuilding_on_input_change(
    tmp_project, fake_docker
):
    """Cold starts reuse the image; changed build inputs warn, not rebuild.

    The first ``up`` records a fingerprint of the Dockerfile, the build
    definition and the context files; later cold starts reuse the image
    with a plain ``up -d``. Addon code edits do not count, while a
    requirements edit — at the context root or nested — makes the run
    diagnostics warn to re-init, and ``up`` still does not rebuild nor
    bless the stale record.
    """
    _buildable_project(tmp_project, fake_docker)
    runtime = DockerRuntime()

    runtime.ensure_service_up(tmp_project)
    ups = _up_calls(fake_docker)
    assert ups and all(line.endswith("up -d") for line in ups)
    warnings = runtime.diagnose(tmp_project, phase="run").warnings
    assert not any("osh init docker" in w for w in warnings)

    (tmp_project / "odoo" / "src" / "module.py").write_text("# changed\n")
    warnings = runtime.diagnose(tmp_project, phase="run").warnings
    assert not any("osh init docker" in w for w in warnings)

    (tmp_project / "odoo" / "src" / "requirements.txt").write_text("lxml\nhttpx\n")
    warnings = runtime.diagnose(tmp_project, phase="run").warnings
    assert any("osh init docker" in w for w in warnings)

    (fake_docker / "calls.log").write_text("")
    runtime.ensure_service_up(tmp_project)
    ups = _up_calls(fake_docker)
    assert ups and all(line.endswith("up -d") for line in ups)
    # ``up`` did not rebuild, so the stale record survives — the warning
    # must keep firing until ``osh init docker`` rebuilds.
    warnings = runtime.diagnose(tmp_project, phase="run").warnings
    assert any("osh init docker" in w for w in warnings)


def test_cold_start_warns_when_a_build_input_is_removed(tmp_project, fake_docker):
    """A deleted build input warns on the next cold start — not just edits."""
    _buildable_project(tmp_project, fake_docker)
    runtime = DockerRuntime()

    runtime.ensure_service_up(tmp_project)
    (tmp_project / "odoo" / "requirements.txt").unlink()

    warnings = runtime.diagnose(tmp_project, phase="run").warnings
    assert any("osh init docker" in w for w in warnings)


def test_compose_build_config_change_warns(tmp_project, fake_docker):
    """Editing the compose ``build:`` mapping counts as an input change."""
    _buildable_project(tmp_project, fake_docker)
    runtime = DockerRuntime()
    runtime.ensure_service_up(tmp_project)

    (tmp_project / "docker-compose.yml").write_text(
        "services:\n  odoo:\n    build:\n      context: odoo\n"
        "      args:\n        - DEBUG=1\n"
    )
    (fake_docker / "config.json").write_text(
        json.dumps(
            {
                "name": "project",
                "services": {
                    "odoo": {
                        "build": {
                            "context": str(tmp_project / "odoo"),
                            "args": {"DEBUG": "1"},
                        },
                        "image": "project-odoo",
                    }
                },
            }
        )
    )

    warnings = runtime.diagnose(tmp_project, phase="run").warnings
    assert any("osh init docker" in w for w in warnings)


def test_init_fingerprint_accepts_inputs_without_rebuild(
    tmp_project, fake_docker, monkeypatch
):
    """``osh init --fingerprint`` accepts changed inputs without rebuilding.

    A requirements edit after the recorded fingerprint makes the run
    diagnostics warn; init with ``--fingerprint`` records the new inputs —
    no ``compose build`` runs and the warning clears.
    """
    _buildable_project(tmp_project, fake_docker)
    runtime = DockerRuntime()
    runtime.ensure_service_up(tmp_project)

    (tmp_project / "odoo" / "requirements.txt").write_text("requests\nhttpx\n")
    warnings = runtime.diagnose(tmp_project, phase="run").warnings
    assert any("osh init docker" in w for w in warnings)

    monkeypatch.chdir(tmp_project)
    (fake_docker / "calls.log").write_text("")
    result = CliRunner().invoke(
        main,
        ["init", "--runtime", "docker", "19.0", "--service", "odoo", "--fingerprint"],
    )
    assert result.exit_code == 0, result.output
    calls = (fake_docker / "calls.log").read_text().splitlines()
    assert not any(" build " in f" {line} " for line in calls)

    warnings = runtime.diagnose(tmp_project, phase="run").warnings
    assert not any("osh init docker" in w for w in warnings)


def test_ensure_service_up_waits_for_db_ready(docker_shared_project):
    """After a cold ``up -d``, the db service is accepting connections.

    ``compose up -d`` returns once containers start, before PostgreSQL
    accepts connections; without the wait, probes racing it report
    existing databases as missing. The shared fixture performed the cold
    ``ensure_service_up`` — this asserts what it left behind.
    """
    ready = _compose(docker_shared_project, "exec", "-T", "db", "pg_isready")
    assert ready.returncode == 0


def test_ensure_service_up_skips_wait_when_stack_running(docker_shared_project):
    """Bringing up an already-running stack leaves its containers alone."""
    runtime = DockerRuntime()
    started = _started_at(docker_shared_project, "app")

    runtime.ensure_service_up(docker_shared_project)

    assert _started_at(docker_shared_project, "app") == started


def test_ensure_service_up_warns_on_db_timeout(
    docker_daemon, tmp_project, monkeypatch, capsys
):
    """A db service that never gets ready warns instead of blocking forever."""
    _write_docker_config(tmp_project)
    # postgres has pg_isready but ``sleep infinity`` never starts a server.
    (tmp_project / ".osh" / "docker-compose.yml").write_text(
        "services:\n"
        "  odoo:\n    image: busybox\n    command: sleep infinity\n"
        "  db:\n    image: postgres:16-alpine\n    command: sleep infinity\n"
    )
    monkeypatch.setattr(
        "osh.plugins.osh_runtime_docker.runtimes._DB_READY_TIMEOUT_SECONDS", 0
    )

    DockerRuntime().ensure_service_up(tmp_project)

    assert "not accepting connections" in capsys.readouterr().out


def test_port_collision_identifies_other_osh_project(
    tmp_project, fake_docker, held_port
):
    """A port held by another Osh project produces a targeted error."""
    port = held_port()
    _write_docker_config(tmp_project, port=port)
    other = tmp_project.parent / "other-project"
    (other / ".osh").mkdir(parents=True)
    (other / ".osh" / "docker.toml").write_text('service = "odoo"\n')
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "other-odoo-1",
            "odoo:19.0",
            f"0.0.0.0:{port}->8069/tcp",
            "Up 3 hours",
            f"com.docker.compose.project.working_dir={other / '.osh'}",
        ),
    )

    with pytest.raises(click.ClickException) as excinfo:
        DockerRuntime().ensure_service_up(tmp_project)

    message = excinfo.value.format_message()
    assert f"Port {port} is already used by a container for {other}" in message
    assert "osh stop" in message
    assert "osh odoo -p" in message
    assert not any("up -d" in c for c in _docker_calls(fake_docker))


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
    assert not any("up -d" in c for c in _docker_calls(fake_docker))


def test_port_collision_unidentified_holder(
    tmp_project, fake_docker, held_port, monkeypatch
):
    """An unidentifiable port holder produces the generic actionable error."""
    port = held_port()
    _write_docker_config(tmp_project, port=port)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "k8s_pod",
            "registry.k8s.io/pause:3.9",
            f"0.0.0.0:{port}->8080/tcp",
            "Up 2 days",
        ),
    )
    # The holder is invisible to the process scan — e.g. another user.
    monkeypatch.setattr("osh.runtimes._port_listeners", lambda port: [])

    with pytest.raises(click.ClickException) as excinfo:
        DockerRuntime().ensure_service_up(tmp_project)

    message = excinfo.value.format_message()
    assert f"Port {port} is already in use" in message
    assert "osh stop" in message
    assert "osh odoo -p" in message
    assert not any("up -d" in c for c in _docker_calls(fake_docker))


def test_port_collision_uses_configured_port(
    tmp_project, fake_docker, held_port, monkeypatch
):
    """The checked port comes from ``.osh/docker.toml`` ``port``."""
    port = held_port()
    _write_docker_config(tmp_project, port=port)
    monkeypatch.setattr("osh.runtimes._port_listeners", lambda port: [])

    with pytest.raises(click.ClickException, match=f"Port {port}"):
        DockerRuntime().ensure_service_up(tmp_project)

    assert not any("up -d" in c for c in _docker_calls(fake_docker))


def test_ensure_service_up_noop_when_service_running(tmp_project, fake_docker):
    """``ensure_service_up`` is a no-op when the service already runs."""
    _write_docker_config(tmp_project)
    (fake_docker / "ps").write_text("abc123\n")

    DockerRuntime().ensure_service_up(tmp_project)

    assert any(" ps " in c for c in _docker_calls(fake_docker))
    assert not any("up -d" in c for c in _docker_calls(fake_docker))


def test_ensure_service_up_brings_stack_up(tmp_project, fake_docker):
    """With a free port the stack is started."""
    _write_docker_config(tmp_project, port=_free_port())

    DockerRuntime().ensure_service_up(tmp_project)

    assert any("up -d" in c for c in _docker_calls(fake_docker))


def test_ensure_service_up_publishes_requested_port(tmp_project, fake_docker):
    """``ensure_service_up(port=…)`` re-publishes Odoo's port instead of
    checking it."""
    port = _free_port()
    _write_docker_config(tmp_project)

    DockerRuntime().ensure_service_up(tmp_project, port=port)

    override = tmp_project / ".osh" / "docker-compose.osh.yml"
    assert f'"{port}:{port}"' in override.read_text()
    assert any("up -d" in c for c in _docker_calls(fake_docker))


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

    assert any("up -d" in c for c in _docker_calls(fake_docker))


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
    assert any("up -d" in c for c in _docker_calls(fake_docker))


def test_db_probe_keeps_requested_port(tmp_project, fake_docker, held_port):
    """``db_env`` uses the CLI's requested port even when the config port
    is taken."""
    _write_docker_config(tmp_project, port=held_port())
    ctx = types.SimpleNamespace(params={}, obj={"http_port": _free_port()})

    DockerRuntime().db_env(ctx, tmp_project, EnvSpec(argv=["psql", "-l"]), capture=True)

    assert any("psql" in c for c in _docker_calls(fake_docker))


def test_probe_reuses_override_port(tmp_project, fake_docker, held_port):
    """Probes keep the port the compose override already publishes."""
    port = held_port()
    _write_docker_config(tmp_project)
    (tmp_project / ".osh" / "docker-compose.osh.yml").write_text(
        "services:\n  odoo:\n    ports: !override\n" f'      - "{port}:8069"\n'
    )
    ctx = types.SimpleNamespace(params={}, obj={})

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
    ctx = types.SimpleNamespace(params={}, obj={})

    # A server run re-publishes its own -p port, ignoring the configured one.
    requested = _free_port()
    runtime.env(
        ctx,
        tmp_project,
        EnvSpec(argv=["odoo", "-p", str(requested), "-d", "testdb"]),
        capture=True,
    )
    assert any(" exec " in c for c in _docker_calls(fake_docker))

    # A plain server run does hit the configured port check.
    with pytest.raises(click.ClickException, match=f"Port {configured}"):
        runtime.env(ctx, tmp_project, EnvSpec(argv=["odoo", "--dev=all"]), capture=True)
