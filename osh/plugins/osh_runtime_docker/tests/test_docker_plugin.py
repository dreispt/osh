"""Tests for the built-in Docker runtime plugin."""

import json
import os
import shutil
import subprocess
import sys
import types
import uuid

import click
import pytest
from click.testing import CliRunner

from osh.cli import main
from osh.commands.shell_cmd import build_dynamic_odoo_config
from osh.db import set_project_config
from osh.plugins.osh_runtime_docker.runtimes import DockerRuntime
from osh.runtimes import EnvSpec, Runtime
from osh.utils.plugin_loader import load_plugins, load_runtimes

from .conftest import (
    FAKEBIN,
    _docker_calls,
    _docker_ps_line,
    _running_containers,
    _write_docker_config,
)


def test_docker_runtime_is_registered():
    """The docker plugin registers the unified Docker runtime."""
    runtimes = load_runtimes()
    assert "docker" in runtimes
    assert runtimes["docker"].name == "docker"
    assert runtimes["docker"].runtime_type == "runtime"


FIXTURE_PROJECT = os.path.join(FAKEBIN, "..", "fixtures", "odoo-project")


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


def test_init_target_docker_via_main_writes_compose_file(
    tmp_project, fake_docker, monkeypatch
):
    """``osh init --runtime=docker`` writes docker.toml and generates compose."""
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(
        main, ["init", "--runtime", "docker", "19.0", "--service", "app"]
    )

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert docker_toml.exists()
    text = docker_toml.read_text()
    assert "service = 'app'" in text
    assert "command = 'odoo'" in text
    assert "compose_file = '.osh/docker-compose.yml'" in text

    compose_file = tmp_project / ".osh" / "docker-compose.yml"
    assert compose_file.exists()
    compose_text = compose_file.read_text()
    assert "  app:" in compose_text
    assert 'image: "odoo:19.0"' in compose_text
    assert "image: postgres:16" in compose_text
    assert "..:/mnt/extra-addons" in compose_text
    assert "user: odoo" in compose_text
    assert "PGHOST: db" in compose_text
    assert "PGPASSWORD: myodoo" in compose_text
    assert not (tmp_project / "docker-compose.yml").exists()
    assert not (tmp_project / "Dockerfile").exists()


def test_init_docker_command_writes_config_and_compose(
    tmp_project, fake_docker, monkeypatch
):
    """``osh init --runtime=docker`` generates ``.osh/docker-compose.yml`` and config."""
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(
        main, ["init", "--runtime", "docker", "19.0", "--service", "odoo"]
    )

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert docker_toml.exists()
    assert "compose_file = '.osh/docker-compose.yml'" in docker_toml.read_text()
    assert (tmp_project / ".osh" / "docker-compose.yml").exists()


def test_init_docker_overwrites_existing_osh_compose(
    tmp_project, fake_docker, monkeypatch
):
    """``.osh/docker-compose.yml`` is Osh-managed and regenerated on init."""
    monkeypatch.chdir(tmp_project)

    existing = tmp_project / ".osh" / "docker-compose.yml"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("existing: compose\n")

    runner = CliRunner()
    result = runner.invoke(main, ["init", "--runtime", "docker", "19.0"])

    assert result.exit_code == 0, result.output
    compose_text = existing.read_text()
    assert 'image: "odoo:19.0"' in compose_text
    assert "user: odoo" in compose_text
    assert (
        "compose_file = '.osh/docker-compose.yml'"
        in (tmp_project / ".osh" / "docker.toml").read_text()
    )


def test_init_docker_updates_compose_for_a_different_version(
    tmp_project, fake_docker, monkeypatch
):
    """Re-initialising Docker with a new version updates the generated compose file."""
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(
        main, ["init", "--runtime", "docker", "19.0", "--service", "odoo"]
    )
    assert result.exit_code == 0, result.output
    compose = tmp_project / ".osh" / "docker-compose.yml"
    compose_text = compose.read_text()
    assert 'image: "odoo:19.0"' in compose_text
    assert "user: odoo" in compose_text

    result = runner.invoke(
        main, ["init", "--runtime", "docker", "20.0", "--service", "odoo"]
    )
    assert result.exit_code == 0, result.output
    compose_text = compose.read_text()
    assert 'image: "odoo:20.0"' in compose_text
    assert "user: odoo" in compose_text
    assert "version = '20.0'" in (tmp_project / ".osh" / "docker.toml").read_text()


def test_init_docker_includes_permission_fix(tmp_project, fake_docker, monkeypatch):
    """The generated compose file includes the user directive to run as odoo."""
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(
        main, ["init", "--runtime", "docker", "19.0", "--service", "odoo"]
    )

    assert result.exit_code == 0, result.output
    compose_file = tmp_project / ".osh" / "docker-compose.yml"
    compose_text = compose_file.read_text()

    # Check that the service runs as the odoo user
    assert "user: odoo" in compose_text


def test_init_docker_persists_provided_compose_file(
    tmp_project, fake_docker, monkeypatch
):
    """A provided ``--compose-file`` is persisted into docker.toml."""
    monkeypatch.chdir(tmp_project)

    (tmp_project / "devel.yaml").write_text("services:\n  odoo:\n    image: odoo\n")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--runtime",
            "docker",
            "19.0",
            "--service",
            "odoo",
            "--compose-file",
            "devel.yaml",
        ],
    )

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert "compose_file = 'devel.yaml'" in docker_toml.read_text()
    assert not (tmp_project / ".osh" / "docker-compose.yml").exists()


def test_init_docker_detects_project_compose_file(
    tmp_project, fake_docker, monkeypatch
):
    """A compose file at the project root is used instead of generating one."""
    monkeypatch.chdir(tmp_project)
    (tmp_project / "compose.yaml").write_text("services:\n  odoo:\n")

    runner = CliRunner()
    result = runner.invoke(
        main, ["init", "--runtime", "docker", "19.0", "--service", "odoo"]
    )

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert "compose_file = 'compose.yaml'" in docker_toml.read_text()
    assert not (tmp_project / ".osh" / "docker-compose.yml").exists()


def test_init_docker_detects_devel_yaml(tmp_project, fake_docker, monkeypatch):
    """A Doodba-style ``devel.yaml`` is detected like canonical names."""
    monkeypatch.chdir(tmp_project)
    (tmp_project / "devel.yaml").write_text("services:\n  odoo:\n")

    runner = CliRunner()
    result = runner.invoke(main, ["init", "--runtime", "docker", "19.0"])

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert "compose_file = 'devel.yaml'" in docker_toml.read_text()


def test_init_docker_compose_detection_precedence(
    tmp_project, fake_docker, monkeypatch
):
    """Canonical names win over ``devel.yaml``; the choice is reported."""
    monkeypatch.chdir(tmp_project)
    (tmp_project / "docker-compose.yml").write_text("services:\n  odoo:\n")
    (tmp_project / "devel.yaml").write_text("services:\n  odoo:\n")

    runner = CliRunner()
    result = runner.invoke(main, ["init", "--runtime", "docker", "19.0"])

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert "compose_file = 'docker-compose.yml'" in docker_toml.read_text()
    assert "devel.yaml" in result.output


def test_init_docker_dockerfile_generates_build_compose(
    tmp_project, fake_docker, monkeypatch
):
    """A project Dockerfile produces a generated compose file that builds it."""
    monkeypatch.chdir(tmp_project)
    (tmp_project / "Dockerfile").write_text("FROM odoo:19.0\n")

    runner = CliRunner()
    result = runner.invoke(main, ["init", "--runtime", "docker", "19.0"])

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    toml_text = docker_toml.read_text()
    assert "compose_file = '.osh/docker-compose.yml'" in toml_text
    assert "dockerfile = 'Dockerfile'" in toml_text

    compose_text = (tmp_project / ".osh" / "docker-compose.yml").read_text()
    assert 'dockerfile: "Dockerfile"' in compose_text
    assert "image: odoo:" not in compose_text
    # The image may not define an 'odoo' user, so no user directive.
    assert "user:" not in compose_text
    assert "command:" in compose_text


def test_init_docker_dockerfile_option(tmp_project, fake_docker, monkeypatch):
    """``--dockerfile`` accepts a Dockerfile not at the project root name."""
    monkeypatch.chdir(tmp_project)
    docker_dir = tmp_project / "docker"
    docker_dir.mkdir()
    (docker_dir / "Dockerfile.dev").write_text("FROM odoo:19.0\n")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--runtime",
            "docker",
            "19.0",
            "--dockerfile",
            "docker/Dockerfile.dev",
        ],
    )

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert "dockerfile = 'docker/Dockerfile.dev'" in docker_toml.read_text()
    compose_text = (tmp_project / ".osh" / "docker-compose.yml").read_text()
    assert 'dockerfile: "docker/Dockerfile.dev"' in compose_text


def test_init_docker_dockerfile_outside_project_errors(
    tmp_project, tmp_path, fake_docker, monkeypatch
):
    """A Dockerfile outside the build context (the project root) is refused."""
    monkeypatch.chdir(tmp_project)
    (tmp_path / "Dockerfile").write_text("FROM odoo:19.0\n")

    runner = CliRunner()
    result = runner.invoke(
        main, ["init", "--runtime", "docker", "19.0", "--dockerfile", "../Dockerfile"]
    )

    assert result.exit_code != 0
    assert "outside the project directory" in result.output


def test_reinit_keeps_configured_compose_file(tmp_project, fake_docker, monkeypatch):
    """Re-running init keeps the project compose file it already uses."""
    monkeypatch.chdir(tmp_project)
    (tmp_project / "custom.yaml").write_text("services:\n  odoo:\n")
    (tmp_project / ".osh" / "docker.toml").write_text(
        "service = 'odoo'\ncompose_file = 'custom.yaml'\n"
    )

    runner = CliRunner()
    result = runner.invoke(main, ["init", "--runtime", "docker", "19.0"])

    assert result.exit_code == 0, result.output
    assert (
        "compose_file = 'custom.yaml'"
        in (tmp_project / ".osh" / "docker.toml").read_text()
    )
    assert not (tmp_project / ".osh" / "docker-compose.yml").exists()


def test_reinit_dockerfile_option_keeps_configured_compose(
    tmp_project, fake_docker, monkeypatch
):
    """``--dockerfile`` does not replace an already-configured compose file."""
    monkeypatch.chdir(tmp_project)
    (tmp_project / "compose.yaml").write_text("services:\n  odoo:\n")
    (tmp_project / "Dockerfile").write_text("FROM odoo:19.0\n")
    (tmp_project / ".osh" / "docker.toml").write_text(
        "service = 'odoo'\ncompose_file = 'compose.yaml'\n"
    )

    result = CliRunner().invoke(
        main, ["init", "--runtime", "docker", "19.0", "--dockerfile", "Dockerfile"]
    )

    assert result.exit_code == 0, result.output
    assert "Ignoring --dockerfile" in result.output
    toml_text = (tmp_project / ".osh" / "docker.toml").read_text()
    assert "compose_file = 'compose.yaml'" in toml_text
    assert "dockerfile" not in toml_text
    assert not (tmp_project / ".osh" / "docker-compose.yml").exists()


def test_reinit_regenerates_stack_over_detected_compose(
    tmp_project, fake_docker, monkeypatch
):
    """A compose file appearing later does not replace a generated stack."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()

    result = runner.invoke(main, ["init", "--runtime", "docker", "19.0"])
    assert result.exit_code == 0, result.output

    (tmp_project / "compose.yaml").write_text("services:\n  odoo:\n")
    result = runner.invoke(main, ["init", "--runtime", "docker", "20.0"])

    assert result.exit_code == 0, result.output
    docker_toml = (tmp_project / ".osh" / "docker.toml").read_text()
    assert "compose_file = '.osh/docker-compose.yml'" in docker_toml
    generated = (tmp_project / ".osh" / "docker-compose.yml").read_text()
    assert 'image: "odoo:20.0"' in generated


def test_reinit_ignores_dockerfile_outside_project(
    tmp_project, tmp_path, fake_docker, monkeypatch
):
    """A configured Dockerfile outside the build context is not reused."""
    monkeypatch.chdir(tmp_project)
    # A Dockerfile at tmp_path is outside the project's build context.
    (tmp_path / "Dockerfile").write_text("FROM odoo:19.0\n")
    (tmp_project / ".osh" / "docker.toml").write_text(
        "service = 'odoo'\ndockerfile = '../Dockerfile'\n"
    )

    result = CliRunner().invoke(main, ["init", "--runtime", "docker", "19.0"])

    assert result.exit_code == 0, result.output
    compose_text = (tmp_project / ".osh" / "docker-compose.yml").read_text()
    assert "../Dockerfile" not in compose_text
    assert 'image: "odoo:19.0"' in compose_text
    assert "dockerfile" not in (tmp_project / ".osh" / "docker.toml").read_text()


def test_reinit_reports_missing_configured_compose_file(
    tmp_project, fake_docker, monkeypatch
):
    """A configured compose file that vanished is a clear init error."""
    monkeypatch.chdir(tmp_project)
    (tmp_project / ".osh" / "docker.toml").write_text("compose_file = 'gone.yaml'\n")

    result = CliRunner().invoke(main, ["init", "--runtime", "docker", "19.0"])

    assert result.exit_code != 0
    assert "gone.yaml" in result.output


def test_init_docker_missing_service_errors(tmp_project, fake_docker, monkeypatch):
    """A compose file without the configured service fails at init."""
    monkeypatch.chdir(tmp_project)
    (tmp_project / "compose.yaml").write_text("services:\n  web:\n    image: web\n")

    result = CliRunner().invoke(main, ["init", "--runtime", "docker", "19.0"])

    assert result.exit_code != 0
    assert "Service 'odoo' not found in compose.yaml" in result.output
    assert "web" in result.output


def test_init_docker_invalid_service_name_errors(tmp_project, fake_docker, monkeypatch):
    """A service name that would corrupt generated YAML is refused."""
    monkeypatch.chdir(tmp_project)

    result = CliRunner().invoke(
        main, ["init", "--runtime", "docker", "19.0", "--service", "bad name"]
    )

    assert result.exit_code != 0
    assert "Invalid Docker service name" in result.output


def test_init_docker_custom_service_on_foreign_compose(
    tmp_project, fake_docker, monkeypatch
):
    """A non-default ``--service`` is accepted when the file defines it."""
    monkeypatch.chdir(tmp_project)
    (tmp_project / "compose.yaml").write_text(
        "services:\n  app:\n    image: odoo:19.0\n"
    )

    result = CliRunner().invoke(
        main, ["init", "--runtime", "docker", "19.0", "--service", "app"]
    )

    assert result.exit_code == 0, result.output
    docker_toml = (tmp_project / ".osh" / "docker.toml").read_text()
    assert "service = 'app'" in docker_toml
    assert "compose_file = 'compose.yaml'" in docker_toml


def test_init_docker_dry_run_still_validates_service(tmp_project, docker_cli):
    """A dry-run init reports a missing service instead of failing on the real run."""
    (tmp_project / "compose.yaml").write_text("services:\n  web:\n    image: web\n")
    from osh.commands.init_cmd import TodoPlan

    runtime = DockerRuntime()
    with pytest.raises(click.ClickException, match="Service 'odoo' not found"):
        runtime.init(
            tmp_project,
            version="19.0",
            dry_run=True,
            compose_file="compose.yaml",
            todo=TodoPlan(None),
        )
    assert not (tmp_project / ".osh" / "docker.toml").exists()


def test_init_docker_dockerfile_missing_errors(tmp_project, fake_docker, monkeypatch):
    """A missing explicit ``--dockerfile`` raises an error."""
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", "--runtime", "docker", "19.0", "--dockerfile", "missing.Dockerfile"],
    )

    assert result.exit_code != 0
    assert "missing.Dockerfile" in result.output


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


def test_diagnose_reports_odoo_version_from_docker_toml(tmp_project, docker_cli):
    """The recorded version is reported when sources and image tags miss."""
    (tmp_project / ".osh" / "docker.toml").write_text(
        "service = 'odoo'\nversion = '19.0'\n"
    )

    d = DockerRuntime().diagnose(tmp_project)

    assert d.info["docker"]["odoo_version"] == "19.0"


def test_docker_diagnose_reports_odoo_version_from_sources(tmp_project, docker_cli):
    """DockerRuntime.diagnose reports the Odoo version from .osh/odoo sources."""
    release = tmp_project / ".osh" / "odoo" / "odoo" / "release.py"
    release.parent.mkdir(parents=True, exist_ok=True)
    release.write_text('version = "21.0"\n')

    runtime = DockerRuntime()
    d = runtime.diagnose(tmp_project)
    assert d.info["docker"]["odoo_version"] == "21.0"


def test_diagnose_reports_odoo_version_from_compose_image(tmp_project, docker_cli):
    """The Odoo version is read from the generated stack's image tag."""
    compose = tmp_project / ".osh" / "docker-compose.yml"
    compose.parent.mkdir(parents=True, exist_ok=True)
    compose.write_text("services:\n  odoo:\n    image: odoo:17.0\n")

    d = DockerRuntime().diagnose(tmp_project)

    assert d.info["docker"]["odoo_version"] == "odoo 17.0"


def test_init_docker_missing_compose_file_raises(tmp_project, fake_docker, monkeypatch):
    """A missing explicit compose file raises an error."""
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--runtime",
            "docker",
            "19.0",
            "--service",
            "odoo",
            "--compose-file",
            "missing.yaml",
        ],
    )

    assert result.exit_code != 0
    assert "missing.yaml" in result.output or "not found" in result.output


def test_init_docker_dry_run_does_not_write(tmp_project, docker_cli, monkeypatch):
    """A dry-run ``init`` only reports what it would generate."""
    runtime = DockerRuntime()

    from osh.commands.init_cmd import TodoPlan

    ok = runtime.init(
        tmp_project,
        version="19.0",
        edition="ce",
        dry_run=True,
        service="odoo",
        todo=TodoPlan(None),
    )

    assert ok is True
    assert not (tmp_project / ".osh" / "docker.toml").exists()
    assert not (tmp_project / ".osh" / "docker-compose.yml").exists()


def test_docker_runtime_diagnose(tmp_project, docker_cli):
    """``diagnose`` returns diagnostics for the configured stack."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        "service = 'odoo'\ncommand = 'odoo'\ncompose_file = 'devel.yaml'\n"
    )

    runtime = DockerRuntime()
    d = runtime.diagnose(tmp_project)

    assert d.info["docker"]["compose_file"] == "devel.yaml"
    assert d.info["docker"]["service"] == "odoo"
    assert d.info["docker"]["command"] == "odoo"


def test_docker_runtime_diagnose_honors_custom_compose_file(tmp_project, docker_cli):
    """``diagnose`` resolves the effective compose file from config/options."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        "service = 'odoo'\ncommand = 'odoo'\ncompose_file = 'devel.yaml'\n"
    )
    (tmp_project / "devel.yaml").write_text("services:\n  odoo:\n")

    runtime = DockerRuntime()
    d = runtime.diagnose(tmp_project, phase="run")

    assert d.ready
    assert not d.errors
    assert "resolved_compose_file" in d.info["docker"]
    assert str(tmp_project / "devel.yaml") in d.info["docker"]["resolved_compose_file"]


def test_docker_runtime_diagnose_ee_sources_missing_with_version(
    tmp_project, docker_cli
):
    """``diagnose`` allows missing source copies when a version is configured."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        "service = 'odoo'\ncommand = 'odoo'\nedition = 'sh'\nversion = '19.0'\n"
    )
    (tmp_project / ".osh" / "docker-compose.yml").write_text("services:\n  odoo:\n")

    runtime = DockerRuntime()
    d = runtime.diagnose(tmp_project, phase="run")

    assert d.ready
    assert not d.errors


def test_docker_runtime_diagnose_reports_container_state(docker_project):
    """``diagnose`` reports leftover container state so users notice it."""
    runtime = DockerRuntime()

    _compose(docker_project, "up", "-d")
    container = runtime.diagnose(docker_project, phase="run").info["docker"][
        "container"
    ]
    assert container.startswith("running, started")

    _compose(docker_project, "stop")
    container = runtime.diagnose(docker_project, phase="run").info["docker"][
        "container"
    ]
    assert container == "not running"


def test_docker_diagnose_warns_when_build_input_changes(
    docker_shared_project,
):
    """A build-input edit after the image build prompts a re-init hint.

    The shared fixture's ``app`` image was fingerprinted when its stack
    came up, so nothing is stale until a file it was built from changes —
    addon trees under the context do not count: they are mounted over at
    run time.
    """
    runtime = DockerRuntime()
    warnings = runtime.diagnose(docker_shared_project, phase="run").warnings
    assert not any("osh init --runtime=docker" in w for w in warnings)

    dockerfile = docker_shared_project / "odoo" / "Dockerfile"
    dockerfile.write_text(dockerfile.read_text() + "# edited\n")

    warnings = runtime.diagnose(docker_shared_project, phase="run").warnings
    assert any("osh init --runtime=docker" in w for w in warnings)


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
    assert not any("osh init --runtime=docker" in w for w in warnings)

    (tmp_project / "odoo" / "src" / "module.py").write_text("# changed\n")
    warnings = runtime.diagnose(tmp_project, phase="run").warnings
    assert not any("osh init --runtime=docker" in w for w in warnings)

    (tmp_project / "odoo" / "src" / "requirements.txt").write_text("lxml\nhttpx\n")
    warnings = runtime.diagnose(tmp_project, phase="run").warnings
    assert any("osh init --runtime=docker" in w for w in warnings)

    (fake_docker / "calls.log").write_text("")
    runtime.ensure_service_up(tmp_project)
    ups = _up_calls(fake_docker)
    assert ups and all(line.endswith("up -d") for line in ups)
    # ``up`` did not rebuild, so the stale record survives — the warning
    # must keep firing until ``osh init --runtime=docker`` rebuilds.
    warnings = runtime.diagnose(tmp_project, phase="run").warnings
    assert any("osh init --runtime=docker" in w for w in warnings)


def test_cold_start_warns_when_a_build_input_is_removed(tmp_project, fake_docker):
    """A deleted build input warns on the next cold start — not just edits."""
    _buildable_project(tmp_project, fake_docker)
    runtime = DockerRuntime()

    runtime.ensure_service_up(tmp_project)
    (tmp_project / "odoo" / "requirements.txt").unlink()

    warnings = runtime.diagnose(tmp_project, phase="run").warnings
    assert any("osh init --runtime=docker" in w for w in warnings)


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
    assert any("osh init --runtime=docker" in w for w in warnings)


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
    assert any("osh init --runtime=docker" in w for w in warnings)

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
    assert not any("osh init --runtime=docker" in w for w in warnings)


def test_docker_runtime_env_dry_run(tmp_project, capsys):
    """``env`` builds and prints the docker compose command in dry-run mode."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        "service = 'app'\ncommand = 'odoo'\ncompose_tool = 'docker compose'\n"
    )

    runtime = DockerRuntime()
    runtime.env(None, tmp_project, EnvSpec(argv=["odoo"]), dry_run=True)

    err = capsys.readouterr().err
    assert "Would run:" in err
    assert "docker compose" in err
    assert " exec " in err
    assert " app " in err
    assert "odoo" in err


def test_docker_runtime_env_runs_user_command(tmp_project, capsys):
    """The command passed by the user is invoked inside the container."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "python3 -m odoo"\ncompose_tool = "docker compose"\n'
    )

    runtime = DockerRuntime()
    runtime.env(
        None,
        tmp_project,
        EnvSpec(argv=["python3", "-m", "odoo"]),
        dry_run=True,
    )

    err = capsys.readouterr().err
    assert "python3 -m odoo" in err


def test_docker_runtime_env_exports_pg_env_for_other_commands(tmp_project, capsys):
    """Non-odoo commands run through a shell mapping image vars to libpq vars."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )

    runtime = DockerRuntime()
    runtime.env(None, tmp_project, EnvSpec(argv=["psql", "-l"]), dry_run=True)

    err = capsys.readouterr().err
    assert 'PGHOST="${PGHOST:-$HOST}"' in err
    assert 'PGPASSWORD="${PGPASSWORD:-$PASSWORD}"' in err
    assert "osh psql -l" in err
    # C.UTF-8 overrides the image's ungenerated LANG=en_US.UTF-8, which makes
    # perl-based tools (pg_wrapper) warn on every exec.
    assert "-e LC_ALL=C.UTF-8" in err


def test_docker_runtime_env_odoo_command_maps_db_env(tmp_project, capsys):
    """``odoo`` runs via a wrapper mapping HOST/USER/... to libpq variables.

    ``compose exec`` bypasses the image entrypoint, which would map them to
    ``--db_*`` arguments; the exported ``PG*`` variables reach Odoo through
    psycopg2's libpq fallback instead.
    """
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )

    runtime = DockerRuntime()
    runtime.env(None, tmp_project, EnvSpec(argv=["odoo"]), dry_run=True)

    err = capsys.readouterr().err
    assert 'PGHOST="${PGHOST:-$HOST}"' in err
    assert " osh odoo" in err


def test_docker_runtime_env_dash_args_prepend_odoo_command(tmp_project, capsys):
    """Flags as argv[0] get the configured command prepended."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )

    runtime = DockerRuntime()
    runtime.env(None, tmp_project, EnvSpec(argv=["-d", "mydb"]), dry_run=True)

    err = capsys.readouterr().err
    assert 'PGHOST="${PGHOST:-$HOST}"' in err
    assert " osh odoo -d mydb" in err


def test_docker_runtime_env_parses_quoted_command(tmp_project, capsys):
    """A configured command with shell quoting is parsed with ``shlex``.

    ``docker.toml`` stores commands as shell strings; splitting on
    whitespace would break a quoted argument like ``-c 'print(1)'`` into
    stray quote tokens.
    """
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "python3 -c \'print(1)\'"\n'
        'compose_tool = "docker compose"\n'
        'compose_file = ".osh/docker-compose.yml"\n'
    )
    (tmp_project / ".osh" / "docker-compose.yml").write_text("services:\n  odoo:\n")

    runtime = DockerRuntime()
    runtime.env(None, tmp_project, EnvSpec(argv=["-d", "mydb"]), dry_run=True)

    err = capsys.readouterr().err
    assert "python3 -c 'print(1)'" in err


def test_exec_script_maps_image_vars_to_libpq(docker_shared_project, monkeypatch):
    """Commands in the container get libpq vars mapped from the image's vars.

    ``compose exec`` bypasses the image entrypoint, so the emitted command
    wraps the user command in a shell preamble exporting ``PG*`` from
    ``HOST``/``PORT``/``USER``/``PASSWORD`` — preferring values already in
    the environment (e.g. ``-e PGUSER=...``).
    """
    calls = []
    monkeypatch.setattr(
        "osh.plugins.osh_runtime_docker.runtimes.os.execvp",
        lambda exe, args: calls.append(args),
    )

    DockerRuntime().env(
        None, docker_shared_project, EnvSpec(argv=["odoo", "--dev=all"])
    )

    args = calls[0]
    i = args.index("-c")
    script, tail = args[i + 1], args[i + 2 :]
    script = script.replace(
        'exec "$@"', 'printf "%s\\n" "$PGHOST:$PGPORT:$PGUSER:$PGPASSWORD"'
    )
    env = {
        "PATH": os.environ["PATH"],
        "HOST": "db",
        "PORT": "5432",
        "USER": "odoo",
        "PASSWORD": "secret",
        "PGUSER": "preset",
    }
    result = subprocess.run(
        ["sh", "-c", script, *tail],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["db:5432:preset:secret"]


def test_conf_addons_path_resolves_symlinks_on_host(tmp_project):
    """A symlinked source checkout maps to its real path in the conf.

    ``.osh/odoo`` may be a symlink to a source checkout (e.g. created by
    ``osh init`` linking a project-local clone). Translated literally it
    would dangle inside the container; resolving on the host first maps it
    to the real directory under ``/mnt/extra-addons``.
    """
    (tmp_project / "odoo" / "odoo" / "addons").mkdir(parents=True)
    (tmp_project / ".osh" / "odoo").symlink_to(
        tmp_project / "odoo" / "odoo", target_is_directory=True
    )

    conf = build_dynamic_odoo_config(tmp_project, "mydb", DockerRuntime())

    addons_path = next(
        line for line in conf.read_text().splitlines() if line.startswith("addons_path")
    )
    assert "/mnt/extra-addons/odoo/odoo/addons" in addons_path
    assert ".osh" not in addons_path


def test_docker_addons_paths_mount_out_of_project_sources(
    docker_daemon, tmp_project, tmp_path
):
    """Sources linked from outside the project get a ``/mnt/osh-src`` mount.

    A generated Compose override adds them as read-only volumes, since the
    ``/mnt/extra-addons`` project mount cannot reach them.
    """
    external = tmp_path / "shared-odoo"
    (external / "addons").mkdir(parents=True)
    (tmp_project / ".osh" / "odoo").symlink_to(external, target_is_directory=True)
    _write_docker_config(tmp_project)
    (tmp_project / ".osh" / "docker-compose.yml").write_text(
        "services:\n  odoo:\n    image: busybox\n    command: sleep infinity\n"
    )

    runtime = DockerRuntime()

    conf = build_dynamic_odoo_config(tmp_project, "mydb", runtime)
    container_path = (
        conf.read_text().split("addons_path = ", 1)[1].splitlines()[0].strip()
    )
    assert container_path.startswith("/mnt/osh-src/addons-")

    # The override is generated on ensure_service_up and carries the mount.
    runtime.ensure_service_up(tmp_project)

    override = tmp_project / ".osh" / "docker-compose.osh.yml"
    assert override.is_file()
    text = override.read_text()
    assert f"{external / 'addons'}:{container_path}:ro" in text


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


def test_docker_runtime_env_interactive_shell_exports_pg_env(tmp_project, capsys):
    """An interactive ``osh shell`` session also gets the libpq variables."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )

    runtime = DockerRuntime()
    runtime.env(None, tmp_project, EnvSpec(argv=[]), dry_run=True)

    err = capsys.readouterr().err
    assert 'PGHOST="${PGHOST:-$HOST}"' in err
    assert "command -v bash" in err
    assert "else exec sh" in err


def test_docker_runtime_db_env_targets_db_service(tmp_project, capsys):
    """``db_env`` execs into the db service with the POSTGRES_* var mapping.

    ``ODOO_RC`` is dropped: the db container does not mount the project.
    """
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )

    runtime = DockerRuntime()
    env_spec = EnvSpec(
        argv=["psql", "-l"], env={"ODOO_RC": "/p/.osh/x.conf", "PGDATABASE": "db1"}
    )
    runtime.db_env(None, tmp_project, env_spec, dry_run=True)

    err = capsys.readouterr().err
    assert "Would run:" in err
    assert " db sh -c" in err
    assert 'PGUSER="${PGUSER:-$POSTGRES_USER}"' in err
    assert 'PGDATABASE="${PGDATABASE:-$POSTGRES_DB}"' in err
    assert "osh psql -l" in err
    assert "ODOO_RC" not in err

    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
        'db_service = "postgres"\n'
    )
    runtime.db_env(None, tmp_project, env_spec, dry_run=True)
    err = capsys.readouterr().err
    assert " postgres sh -c" in err


def test_stop_by_name_downs_the_project_stack(docker_project):
    """``osh stop <name>`` tears down a stack located by its Compose labels."""
    _compose(docker_project, "up", "-d")

    result = CliRunner().invoke(main, ["stop", os.environ["COMPOSE_PROJECT_NAME"]])

    assert result.exit_code == 0, result.output
    assert not _compose(docker_project, "ps", "-aq").stdout.strip()


def test_stop_all_with_no_osh_stacks(tmp_project, fake_docker, monkeypatch):
    """``osh stop --all`` reports when no Osh-managed stacks exist.

    A real daemon may run unrelated containers, so this stays on the
    canned-answer ``docker`` to get a deterministic empty list.
    """
    monkeypatch.setattr("osh.runtimes._osh_managed_odoo_pids", lambda: [])

    result = CliRunner().invoke(main, ["stop", "--all"])

    assert result.exit_code == 0, result.output
    assert "No Osh-managed Docker stacks found" in result.output


def test_stop_all_surfaces_docker_failure(tmp_project, fake_docker, monkeypatch):
    """A ``docker ps`` failure surfaces as a command error."""
    (fake_docker / "docker_rc").write_text("1\n")
    monkeypatch.setattr("osh.runtimes._osh_managed_odoo_pids", lambda: [])

    result = CliRunner().invoke(main, ["stop", "--all"])

    assert result.exit_code != 0
    assert "Could not list Docker containers" in result.output


def test_stop_downs_the_project_stack(tmp_project, fake_docker, monkeypatch):
    """``osh stop`` delegates teardown to the project's recorded runtime."""
    _write_docker_config(tmp_project)
    set_project_config(tmp_project, "run", "runtime", "docker")
    monkeypatch.chdir(tmp_project)

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0, result.output
    # "down" here is Compose's own verb — the osh-level spelling is `stop`.
    downs = [c for c in _docker_calls(fake_docker) if c.endswith(" down")]
    assert len(downs) == 1


def test_stop_without_docker_config_is_noop(tmp_project, fake_docker, monkeypatch):
    """A docker project without docker.toml reports nothing to stop."""
    set_project_config(tmp_project, "run", "runtime", "docker")
    monkeypatch.chdir(tmp_project)

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0, result.output
    assert "nothing to stop" in result.output


def test_stop_by_name_downs_another_project(tmp_path, fake_docker):
    """``osh stop <name>`` downs another project's stack by dirname."""
    other = tmp_path / "other"
    _write_docker_config(other)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "other-odoo-1",
            "odoo:19.0",
            "0.0.0.0:8069->8069/tcp",
            "Up 2 hours",
            f"com.docker.compose.project.working_dir={other / '.osh'},"
            "com.docker.compose.project=osh-other-abc123",
        ),
    )

    result = CliRunner().invoke(main, ["stop", "other"])

    assert result.exit_code == 0, result.output
    # The stack's actual Compose project name is used, not the derived one.
    downs = [c for c in _docker_calls(fake_docker) if c.endswith(" down")]
    assert any("-p osh-other-abc123" in c for c in downs)


def test_stop_by_name_unknown_project(fake_docker):
    """An unknown project name fails pointing at ``osh stop --all``."""
    result = CliRunner().invoke(main, ["stop", "nosuch"])

    assert result.exit_code != 0
    assert "No Docker stack found for project 'nosuch'" in result.output
    assert "osh stop --all" in result.output


def test_stop_by_name_ambiguous_project(tmp_path, fake_docker):
    """Two projects sharing a directory name require the full path."""
    proj_a = tmp_path / "a" / "same"
    proj_b = tmp_path / "b" / "same"
    _write_docker_config(proj_a)
    _write_docker_config(proj_b)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "same-odoo-1",
            "odoo:19.0",
            "0.0.0.0:8069->8069/tcp",
            "Up 1 hour",
            f"com.docker.compose.project.working_dir={proj_a / '.osh'},"
            "com.docker.compose.project=osh-same-aaaaaa",
        ),
        _docker_ps_line(
            "same-odoo-2",
            "odoo:19.0",
            "0.0.0.0:9070->8069/tcp",
            "Up 2 hours",
            f"com.docker.compose.project.working_dir={proj_b / '.osh'},"
            "com.docker.compose.project=osh-same-bbbbbb",
        ),
    )

    result = CliRunner().invoke(main, ["stop", "same"])

    assert result.exit_code != 0
    assert "matches more than one" in result.output
    assert str(proj_a) in result.output
    assert str(proj_b) in result.output


def test_stop_all_lists_and_downs_every_stack(tmp_path, fake_docker, monkeypatch):
    """``osh stop --all`` reports then tears down every Osh Compose stack."""
    proj_a = tmp_path / "proj-a"
    proj_b = tmp_path / "proj-b"
    _write_docker_config(proj_a)
    _write_docker_config(proj_b)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "a-odoo-1",
            "odoo:19.0",
            "0.0.0.0:8069->8069/tcp",
            "Up 1 hour",
            f"com.docker.compose.project.working_dir={proj_a / '.osh'},"
            "com.docker.compose.project=osh-proj-a-aaaaaa",
        ),
        _docker_ps_line(
            "b-odoo-1",
            "odoo:19.0",
            "0.0.0.0:9070->8069/tcp",
            "Up 2 hours",
            f"com.docker.compose.project.working_dir={proj_b / '.osh'},"
            "com.docker.compose.project=osh-proj-b-bbbbbb",
        ),
    )
    # Keep the host sweep quiet — no Odoo processes exist in tests.
    monkeypatch.setattr("osh.runtimes._osh_managed_odoo_pids", lambda: [])

    result = CliRunner().invoke(main, ["stop", "--all"])

    assert result.exit_code == 0, result.output
    # The list shows both stacks before tearing them down.
    assert "Osh-managed Docker stacks:" in result.output
    assert str(proj_a) in result.output
    assert str(proj_b) in result.output
    downs = [c for c in _docker_calls(fake_docker) if c.endswith(" down")]
    assert len(downs) == 2


def test_stop_list_reports_stack_without_downing(tmp_project, fake_docker, monkeypatch):
    """``osh stop --list`` shows the stack containers and the down it would run."""
    _write_docker_config(tmp_project)
    set_project_config(tmp_project, "run", "runtime", "docker")
    monkeypatch.chdir(tmp_project)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "proj-odoo-1",
            "odoo:19.0",
            "0.0.0.0:8069->8069/tcp",
            "Up 1 hour",
            f"com.docker.compose.project.working_dir={tmp_project / '.osh'},"
            "com.docker.compose.project=osh-proj-aaaaaa",
        ),
        _docker_ps_line(
            "proj-db-1",
            "postgres:16",
            "",
            "Up 1 hour",
            f"com.docker.compose.project.working_dir={tmp_project / '.osh'},"
            "com.docker.compose.project=osh-proj-aaaaaa",
        ),
    )

    result = CliRunner().invoke(main, ["stop", "--list"])

    assert result.exit_code == 0, result.output
    assert "proj-odoo-1" in result.output
    assert "proj-db-1" in result.output
    assert "this project's stack" in result.output
    assert "Would run: docker compose" in result.output
    assert " down" in result.output
    assert not [c for c in _docker_calls(fake_docker) if c.endswith(" down")]


def test_stop_list_names_the_project_holding_the_port(
    tmp_project, fake_docker, monkeypatch
):
    """Another project's container publishing the port points at its owner."""
    other = tmp_project.parent / "other"
    _write_docker_config(other)
    _write_docker_config(tmp_project)
    set_project_config(tmp_project, "run", "runtime", "docker")
    monkeypatch.chdir(tmp_project)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "other-odoo-1",
            "odoo:19.0",
            "0.0.0.0:8069->8069/tcp",
            "Up 1 hour",
            f"com.docker.compose.project.working_dir={other / '.osh'},"
            "com.docker.compose.project=osh-other-bbbbbb",
        ),
    )

    result = CliRunner().invoke(main, ["stop", "--list"])

    assert result.exit_code == 0, result.output
    assert "other-odoo-1" in result.output
    assert f"osh stop {other}" in result.output
    assert not [c for c in _docker_calls(fake_docker) if c.endswith(" down")]


def test_stop_list_reports_a_port_range_publisher(
    tmp_project, fake_docker, monkeypatch
):
    """A container publishing a range that includes the port is listed.

    Podman reports ``Labels`` as a JSON object rather than a comma-separated
    string; the holder must be identified either way.
    """
    _write_docker_config(tmp_project)
    set_project_config(tmp_project, "run", "runtime", "docker")
    monkeypatch.chdir(tmp_project)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "proj-odoo-1",
            "odoo:19.0",
            "0.0.0.0:8069-8070->8069-8070/tcp",
            "Up 1 hour",
            labels={
                "com.docker.compose.project.working_dir": str(tmp_project / ".osh"),
                "com.docker.compose.project": "osh-proj-aaaaaa",
            },
        ),
    )

    result = CliRunner().invoke(main, ["stop", "--list"])

    assert result.exit_code == 0, result.output
    assert "proj-odoo-1" in result.output
    assert "this project's stack" in result.output
    assert not [c for c in _docker_calls(fake_docker) if c.endswith(" down")]


def test_stop_all_list_reports_stacks_without_downing(
    tmp_path, fake_docker, monkeypatch
):
    """``osh stop --all --list`` lists the stacks and tears nothing down."""
    proj_a = tmp_path / "proj-a"
    _write_docker_config(proj_a)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "a-odoo-1",
            "odoo:19.0",
            "0.0.0.0:8069->8069/tcp",
            "Up 1 hour",
            f"com.docker.compose.project.working_dir={proj_a / '.osh'},"
            "com.docker.compose.project=osh-proj-a-aaaaaa",
        ),
    )
    monkeypatch.setattr("osh.runtimes._osh_managed_odoo_pids", lambda: [])

    result = CliRunner().invoke(main, ["stop", "--all", "--list"])

    assert result.exit_code == 0, result.output
    assert "osh-proj-a-aaaaaa" in result.output
    assert str(proj_a) in result.output
    assert not [c for c in _docker_calls(fake_docker) if c.endswith(" down")]


def test_stop_by_name_list_reports_the_stack(tmp_path, fake_docker):
    """``osh stop <name> --list`` reports the stack without downing it."""
    other = tmp_path / "other"
    _write_docker_config(other)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "other-odoo-1",
            "odoo:19.0",
            "0.0.0.0:8069->8069/tcp",
            "Up 2 hours",
            f"com.docker.compose.project.working_dir={other / '.osh'},"
            "com.docker.compose.project=osh-other-abc123",
        ),
    )

    result = CliRunner().invoke(main, ["stop", "other", "--list"])

    assert result.exit_code == 0, result.output
    assert "osh-other-abc123" in result.output
    assert not [c for c in _docker_calls(fake_docker) if c.endswith(" down")]


def test_docker_runtime_requires_service(tmp_project):
    """``env`` fails when no service is configured."""
    runtime = DockerRuntime()
    with pytest.raises(click.ClickException):
        runtime.env(None, tmp_project, EnvSpec(argv=["odoo"]), dry_run=True)


def test_docker_runtime_env_forwards_stdin(docker_shared_project):
    """``EnvSpec.stdin`` reaches the container through ``compose exec -T``.

    This is how ``osh backup restore`` streams dumps into the container
    without relying on any volume mount.
    """
    payload = "dump-bytes\n"
    dump = docker_shared_project / "dump.sql"
    dump.write_text(payload)

    with dump.open("rb") as stream:
        rc, out, _err = DockerRuntime().env(
            None,
            docker_shared_project,
            EnvSpec(argv=["cat"], stdin=stream),
            capture=True,
        )

    assert rc == 0
    assert payload in out


def test_conf_data_dir_only_when_declared(tmp_project):
    """``data_dir`` is written into the run conf only when declared."""
    runtime = DockerRuntime()

    # Nothing declared: no compose file, no docker.toml key.
    conf = build_dynamic_odoo_config(tmp_project, "mydb", runtime)
    assert "data_dir" not in conf.read_text()

    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.write_text('service = "odoo"\ndata_dir = "/opt/odoo/data"\n')
    conf = build_dynamic_odoo_config(tmp_project, "mydb", runtime)
    assert "data_dir = /opt/odoo/data" in conf.read_text()


def test_conf_data_dir_from_compose_env(tmp_project, docker_cli):
    """The service's ``ODOO_DATA_DIR`` lands in the generated config."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.write_text(
        'service = "odoo"\ncompose_tool = "docker compose"\n'
        'compose_file = "docker-compose.yml"\n'
    )
    (tmp_project / "docker-compose.yml").write_text(
        "services:\n  odoo:\n    image: odoo:19.0\n"
        "    environment:\n      ODOO_DATA_DIR: /odoo/data\n"
    )

    conf = build_dynamic_odoo_config(tmp_project, "mydb", DockerRuntime())

    assert "data_dir = /odoo/data" in conf.read_text()


def test_conf_data_dir_from_volume_mount(tmp_project, docker_cli):
    """A ``*/data`` or ``/var/lib/odoo`` volume target is honored."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.write_text(
        'service = "odoo"\ncompose_tool = "docker compose"\n'
        'compose_file = "docker-compose.yml"\n'
    )
    compose = tmp_project / "docker-compose.yml"
    runtime = DockerRuntime()

    for target in ("/odoo/data", "/var/lib/odoo"):
        compose.write_text(
            "services:\n  odoo:\n    image: odoo:19.0\n"
            f"    volumes:\n      - data:{target}\nvolumes:\n  data:\n"
        )
        conf = build_dynamic_odoo_config(tmp_project, "mydb", runtime)
        assert f"data_dir = {target}" in conf.read_text()

    # Unrelated mounts leave Odoo's own default untouched.
    compose.write_text(
        "services:\n  odoo:\n    image: odoo:19.0\n"
        "    volumes:\n      - .:/mnt/extra-addons\n"
    )
    conf = build_dynamic_odoo_config(tmp_project, "mydb", runtime)
    assert "data_dir" not in conf.read_text()


def test_docker_runtime_compose_file_from_config(tmp_project, capsys):
    """The compose file from docker.toml is passed with ``-f``."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_file = "devel.yaml"\n'
        'compose_tool = "docker compose"\n'
    )

    runtime = DockerRuntime()
    runtime.env(None, tmp_project, EnvSpec(argv=["odoo"]), dry_run=True)

    err = capsys.readouterr().err
    assert "docker compose" in err
    assert "-f" in err and "devel.yaml" in err
    assert " exec " in err


def test_docker_runtime_compose_file_cli_override(tmp_project, capsys):
    """A compose file passed in the click context overrides config."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_file = "devel.yaml"\n'
        'compose_tool = "docker compose"\n'
    )

    class FakeCtx:
        params = {"compose_file": "test.yaml"}

    runtime = DockerRuntime()
    runtime.env(
        FakeCtx(),
        tmp_project,
        EnvSpec(argv=["odoo"]),
        dry_run=True,
    )

    err = capsys.readouterr().err
    assert "-f" in err and "test.yaml" in err


def test_init_docker_writes_version_and_edition(tmp_project, fake_docker, monkeypatch):
    """``osh init --runtime=docker`` persists the Odoo version and edition."""
    monkeypatch.chdir(tmp_project)

    ent = tmp_project / "enterprise"
    (ent / "web").mkdir(parents=True, exist_ok=True)
    (ent / "web" / "__manifest__.py").touch()

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--runtime",
            "docker",
            "19.0",
            "--service",
            "odoo",
            "--ee",
            "--enterprise-source",
            str(ent),
        ],
    )

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    text = docker_toml.read_text()
    assert "version = '19.0'" in text
    assert "edition = 'ee'" in text


def test_osh_run_docker_uses_branch_database(
    tmp_project,
    monkeypatch,
):
    """``osh odoo`` on the docker runtime uses a branch-based database name."""
    subprocess.run(["git", "init"], cwd=tmp_project, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "x@y"], cwd=tmp_project, check=True)
    subprocess.run(["git", "config", "user.name", "x"], cwd=tmp_project, check=True)
    (tmp_project / "README").write_text("x")
    subprocess.run(["git", "add", "README"], cwd=tmp_project, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_project, check=True)
    subprocess.run(["git", "checkout", "-b", "feature-x"], cwd=tmp_project, check=True)

    osh_dir = tmp_project / ".osh"
    docker_toml = osh_dir / "docker.toml"
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )
    (osh_dir / "docker-compose.yml").write_text("services:\n  odoo:\n")

    # Command assembly only: the generated database name is what is asserted,
    # so the existence probe is stubbed rather than creating a real database.
    monkeypatch.setattr("osh.db.db_exists", lambda base, name, **kw: True)
    from osh.db import set_project_config

    set_project_config(tmp_project, "run", "runtime", "docker")
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(main, ["odoo", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Using database: project-feature-x" in result.output
    assert "PGDATABASE=project-feature-x" in result.output
    assert " osh odoo" in result.output
    assert "-d project-feature-x" not in result.output
    assert "--db-filter" not in result.output


def test_load_runtimes_warns_on_name_collision(monkeypatch, capsys):
    """A runtime name collision is reported instead of silently ignored."""
    from osh.utils import plugin_loader, plugin_registry

    class FakeRuntime(Runtime):
        name = "docker"

    class OtherRuntime(Runtime):
        name = "docker"

    first = types.ModuleType("first")
    first.FakeRuntime = FakeRuntime
    second = types.ModuleType("second")
    second.OtherRuntime = OtherRuntime

    # Isolate the registry so only the patched modules contribute runtimes.
    monkeypatch.setattr(plugin_registry, "_REGISTRY", plugin_registry.PluginRegistry())
    monkeypatch.setattr(
        plugin_loader,
        "_iter_plugin_modules",
        lambda: [("first", first), ("second", second)],
    )

    runtimes = load_runtimes()
    assert runtimes["docker"] is FakeRuntime
    err = capsys.readouterr().err
    assert "runtime 'docker' from 'second' conflicts" in err


def test_entry_point_plugin_loading(monkeypatch):
    """``module:attr`` entry points resolve their command lazily."""
    from osh.utils import plugin_registry

    fake_cmd = click.Command(name="fake-cmd")

    fake_module = types.ModuleType("fake_entry_plugin")
    fake_module.fake_cmd = fake_cmd
    monkeypatch.setitem(sys.modules, "fake_entry_plugin", fake_module)

    class FakeEntryPoint:
        def __init__(self, name, value, group="osh.plugins"):
            self.name = name
            self.value = value
            self.group = group

    class FakeEntryPoints:
        def __init__(self, eps):
            self._eps = eps

        def select(self, **kwargs):
            if kwargs.get("group") == "osh.plugins":
                return self._eps
            return []

    fake_metadata = types.ModuleType("fake_metadata")
    fake_metadata.entry_points = lambda: FakeEntryPoints(
        [FakeEntryPoint("fake", "fake_entry_plugin:fake_cmd")]
    )
    monkeypatch.setattr(plugin_registry, "_metadata", fake_metadata)

    commands = {cmd.name: (src, cmd) for src, cmd in load_plugins()}
    src, cmd = commands["fake"]
    assert src == "fake"
    assert cmd.load() is fake_cmd
