"""Fixtures for the Docker runtime plugin tests."""

import json
import os
import shutil
import socket
import subprocess
import uuid

import pytest

from osh.plugins.osh_runtime_docker.runtimes import DockerRuntime

FAKEBIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fakebin")
FIXTURE_PROJECT = os.path.join(FAKEBIN, "..", "fixtures", "odoo-project")


def pytest_collection_modifyitems(config, items):
    """Mark tests that start real Docker containers.

    ``pytest --no-docker`` (registered in the root ``conftest.py``) skips
    them for fast local iteration; CI runs them as a separate job via
    ``-m docker``. The mark follows fixture use, so tests don't repeat
    the decorator.
    """
    skip_docker = config.getoption("--no-docker")
    docker_fixtures = {"docker_daemon", "docker_project", "docker_shared_project"}
    for item in items:
        if docker_fixtures & set(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.docker)
            if skip_docker:
                item.add_marker(pytest.mark.skip(reason="needs a Docker daemon"))


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    """Put a canned-answer ``docker`` on PATH; return its response dir.

    For the cases real Docker cannot reproduce deterministically — an
    empty ``docker ps``, or a failing one. Absent response files mean
    success with empty output; ``docker_ps`` and ``docker_rc`` override
    plain ``docker`` calls (``osh stop --all``).
    """
    real = shutil.which("docker")
    state = tmp_path / "fake-docker"
    state.mkdir()
    monkeypatch.setenv("OSH_FAKE_DOCKER", str(state))
    monkeypatch.setenv("PATH", f"{FAKEBIN}{os.pathsep}{os.environ['PATH']}")
    if real:
        monkeypatch.setenv("OSH_REAL_DOCKER", real)
    return state


@pytest.fixture
def held_port():
    """Bind real TCP ports for the test's duration, closing on teardown."""
    socks = []

    def hold():
        sock = socket.socket()
        sock.bind(("0.0.0.0", 0))
        sock.listen(1)
        socks.append(sock)
        return sock.getsockname()[1]

    yield hold

    for sock in socks:
        sock.close()


@pytest.fixture
def docker_cli():
    """Require the ``docker compose`` CLI on the test machine.

    ``version`` and ``config`` do not need a daemon, so stateless tests
    (init, conf generation, diagnose) get real Compose resolution.
    """
    docker = shutil.which("docker")
    if (
        not docker
        or subprocess.run(
            [docker, "compose", "version"], capture_output=True
        ).returncode
    ):
        pytest.skip("docker compose is not available")


@pytest.fixture
def docker_daemon(docker_cli, monkeypatch, tmp_path):
    """A real Docker daemon scoped to a unique Compose project name."""
    if subprocess.run(["docker", "info"], capture_output=True).returncode:
        pytest.skip("a running Docker daemon is required")
    name = f"osh-test-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", name)
    # Port probing is a host socket check, not a Docker call — pretend the
    # Odoo port is free so ensure_service_up never binds anything.
    monkeypatch.setattr(
        "osh.plugins.osh_runtime_docker.runtimes.port_in_use",
        lambda *a, **kw: False,
    )
    yield name
    # Down the fixture's own namespace first — generated ``.osh`` stacks may
    # not be attributed to it by ``docker compose ls``.
    subprocess.run(["docker", "compose", "-p", name, "down", "-v"], capture_output=True)
    # Tear down every Compose project whose config lives under tmp_path —
    # covers both the test namespace and osh's own ``-p osh-*`` names.
    for project in _compose_projects():
        if str(tmp_path) in project.get("ConfigFiles", ""):
            subprocess.run(
                ["docker", "compose", "-p", project["Name"], "down", "-v"],
                capture_output=True,
            )
    stray = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"name={name}"],
        capture_output=True,
        text=True,
    ).stdout.split()
    if stray:
        subprocess.run(["docker", "rm", "-f", *stray], capture_output=True)


@pytest.fixture
def docker_project(docker_daemon, tmp_path, monkeypatch):
    """The sample Osh project copied into a fresh, namespaced stack."""
    project = tmp_path / "project"
    shutil.copytree(FIXTURE_PROJECT, project)
    (project / ".osh").mkdir(exist_ok=True)
    shutil.copy(project / "docker.toml", project / ".osh" / "docker.toml")
    monkeypatch.chdir(project)
    return project


@pytest.fixture(scope="module")
def docker_shared_project(tmp_path_factory):
    """One real sample-project stack shared by the read-only tests.

    ``ensure_service_up`` runs once — covering the cold ``up`` and the
    db-readiness wait — and tests then exercise exec/list paths on the
    running stack. Tests that mutate stack state use ``docker_project``.
    """
    docker = shutil.which("docker")
    if (
        not docker
        or subprocess.run(
            [docker, "compose", "version"], capture_output=True
        ).returncode
    ):
        pytest.skip("docker compose is not available")
    if subprocess.run(["docker", "info"], capture_output=True).returncode:
        pytest.skip("a running Docker daemon is required")
    tmp_path = tmp_path_factory.mktemp("docker-shared")
    project = tmp_path / "project"
    shutil.copytree(FIXTURE_PROJECT, project)
    (project / ".osh").mkdir(exist_ok=True)
    shutil.copy(project / "docker.toml", project / ".osh" / "docker.toml")
    name = f"osh-test-{uuid.uuid4().hex[:8]}"
    previous = os.environ.get("COMPOSE_PROJECT_NAME")
    os.environ["COMPOSE_PROJECT_NAME"] = name
    try:
        DockerRuntime().ensure_service_up(project)
        yield project
    finally:
        subprocess.run(
            ["docker", "compose", "-p", name, "down", "-v"],
            capture_output=True,
        )
        stray = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"name={name}"],
            capture_output=True,
            text=True,
        ).stdout.split()
        if stray:
            subprocess.run(["docker", "rm", "-f", *stray], capture_output=True)
        if previous is None:
            os.environ.pop("COMPOSE_PROJECT_NAME", None)
        else:
            os.environ["COMPOSE_PROJECT_NAME"] = previous


def _write_docker_config(project, port=None):
    """Write a minimal docker runtime config and generated compose file."""
    osh_dir = project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    text = 'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    if port:
        text += f"port = {port}\n"
    (osh_dir / "docker.toml").write_text(text)
    (osh_dir / "docker-compose.yml").write_text("services:\n  odoo:\n")


def _docker_ps_line(name, image, ports, status, labels="", cid=None):
    """Return a ``docker ps --format '{{json .}}'`` output line."""
    return json.dumps(
        {
            "ID": cid or name,
            "Names": name,
            "Image": image,
            "Ports": ports,
            "Status": status,
            "Labels": labels,
        }
    )


def _running_containers(fake_docker, *lines):
    """Feed the canned-answer ``docker`` a ``docker ps`` listing."""
    (fake_docker / "docker_ps").write_text("\n".join(lines))


def _docker_calls(fake_docker):
    """Return the argv lines the canned-answer ``docker`` recorded."""
    log = fake_docker / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def _compose(cwd, *args, file=None):
    """Run ``docker compose`` from *cwd*; return the completed process."""
    cmd = ["docker", "compose"]
    if file:
        cmd += ["-f", str(file)]
    cmd += list(args)
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _compose_projects():
    """Return ``docker compose ls`` entries."""
    out = subprocess.run(
        ["docker", "compose", "ls", "--format", "json"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    return json.loads(out) if out else []


def _started_at(project, service):
    """Return the started timestamp of a service's container."""
    cid = _compose(project, "ps", "-q", service).stdout.strip()
    return subprocess.run(
        ["docker", "inspect", "-f", "{{.State.StartedAt}}", cid],
        capture_output=True,
        text=True,
    ).stdout.strip()
