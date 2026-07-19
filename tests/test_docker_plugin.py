"""Tests for the built-in Docker backend plugin."""

import subprocess
import sys
import types

import click
import pytest
from click.testing import CliRunner

from osh.backends import Backend, RunSpec
from osh.cli import main
from osh.plugin_loader import load_backends, load_plugins
from osh.plugins.osh_docker.backends import DockerBackend
from osh.plugins.osh_docker.commands import init_docker


def test_docker_backends_are_registered():
    """The docker plugin registers the unified Docker backend."""
    backends = load_backends("backend")
    assert "docker" in backends
    assert backends["docker"].name == "docker"
    assert backends["docker"].backend_type == "backend"


def _patch_docker_tools(monkeypatch):
    """Make Docker tooling no-ops so tests do not require a Docker daemon."""
    monkeypatch.setattr(
        "osh.plugins.osh_docker.utils._find_compose_tool",
        lambda: ["docker", "compose"],
    )

    def fake_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        return subprocess.CompletedProcess(cmd, returncode=0)

    monkeypatch.setattr("osh.plugins.osh_docker.backends.run_command", fake_run)


def test_init_target_docker_via_main_writes_compose_file(tmp_project, monkeypatch):
    """``osh init --target docker`` writes docker.toml and generates compose."""
    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(
        main, ["init", "19.0", "--target", "docker", "--service", "app"]
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
    assert "image: odoo:19.0" in compose_text
    assert "image: postgres:16" in compose_text
    assert "..:/mnt/extra-addons" in compose_text
    assert not (tmp_project / "docker-compose.yml").exists()
    assert not (tmp_project / "Dockerfile").exists()


def test_init_docker_command_writes_config_and_compose(tmp_project, monkeypatch):
    """``osh init-docker`` generates ``.osh/docker-compose.yml`` and config."""
    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(init_docker, ["19.0", "--service", "odoo"])

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert docker_toml.exists()
    assert "compose_file = '.osh/docker-compose.yml'" in docker_toml.read_text()
    assert (tmp_project / ".osh" / "docker-compose.yml").exists()


def test_init_docker_overwrites_existing_osh_compose(tmp_project, monkeypatch):
    """``.osh/docker-compose.yml`` is Osh-managed and regenerated on init."""
    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    existing = tmp_project / ".osh" / "docker-compose.yml"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("existing: compose\n")

    runner = CliRunner()
    result = runner.invoke(init_docker, ["19.0"])

    assert result.exit_code == 0, result.output
    assert "image: odoo:19.0" in existing.read_text()
    assert (
        "compose_file = '.osh/docker-compose.yml'"
        in (tmp_project / ".osh" / "docker.toml").read_text()
    )


def test_init_docker_updates_compose_for_a_different_version(tmp_project, monkeypatch):
    """Re-initialising Docker with a new version updates the generated compose file."""
    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(init_docker, ["19.0", "--service", "odoo"])
    assert result.exit_code == 0, result.output
    compose = tmp_project / ".osh" / "docker-compose.yml"
    assert "image: odoo:19.0" in compose.read_text()

    result = runner.invoke(init_docker, ["20.0", "--service", "odoo"])
    assert result.exit_code == 0, result.output
    assert "image: odoo:20.0" in compose.read_text()
    assert "version = '20.0'" in (tmp_project / ".osh" / "docker.toml").read_text()


def test_init_docker_persists_provided_compose_file(tmp_project, monkeypatch):
    """A provided ``--compose-file`` is persisted into docker.toml."""
    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    (tmp_project / "devel.yaml").write_text("services:\n  odoo:\n    image: odoo\n")

    runner = CliRunner()
    result = runner.invoke(
        init_docker,
        ["19.0", "--service", "odoo", "--compose-file", "devel.yaml"],
    )

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert "compose_file = 'devel.yaml'" in docker_toml.read_text()
    assert not (tmp_project / ".osh" / "docker-compose.yml").exists()


def test_docker_diagnose_reports_odoo_version_from_sources(tmp_project, monkeypatch):
    """DockerBackend.diagnose reports the Odoo version from .osh/odoo sources."""
    monkeypatch.setattr(
        "osh.plugins.osh_docker.utils._find_compose_tool",
        lambda: ["docker", "compose"],
    )
    release = tmp_project / ".osh" / "odoo" / "odoo" / "release.py"
    release.parent.mkdir(parents=True, exist_ok=True)
    release.write_text('version = "21.0"\n')

    backend = DockerBackend()
    d = backend.diagnose(tmp_project)
    assert d.info["docker"]["odoo_version"] == "21.0"


def test_docker_detect_odoo_version_from_compose_image(tmp_project):
    """DockerBackend.detect_odoo_version reads the image tag from docker-compose.yml."""
    compose = tmp_project / ".osh" / "docker-compose.yml"
    compose.parent.mkdir(parents=True, exist_ok=True)
    compose.write_text("services:\n  odoo:\n    image: odoo:17.0\n")

    backend = DockerBackend()
    assert backend.detect_odoo_version(tmp_project) == "odoo 17.0"


def test_init_docker_missing_compose_file_raises(tmp_project, monkeypatch):
    """A missing explicit compose file raises an error."""
    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(
        init_docker,
        ["19.0", "--service", "odoo", "--compose-file", "missing.yaml"],
    )

    assert result.exit_code != 0
    assert "missing.yaml" in result.output or "not found" in result.output


def test_init_docker_dry_run_does_not_write(tmp_project, monkeypatch):
    """A dry-run ``init`` only reports what it would generate."""
    _patch_docker_tools(monkeypatch)
    backend = DockerBackend()

    ok = backend.init(
        tmp_project,
        version="19.0",
        edition="ce",
        dry_run=True,
        service="odoo",
    )

    assert ok is True
    assert not (tmp_project / ".osh" / "docker.toml").exists()
    assert not (tmp_project / ".osh" / "docker-compose.yml").exists()


def test_docker_backend_diagnose(tmp_project, monkeypatch):
    """``diagnose`` returns diagnostics for the configured stack."""
    monkeypatch.setattr(
        "osh.plugins.osh_docker.utils._find_compose_tool",
        lambda: ["docker", "compose"],
    )
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        "service = 'odoo'\ncommand = 'odoo'\ncompose_file = 'devel.yaml'\n"
    )

    backend = DockerBackend()
    d = backend.diagnose(tmp_project)

    assert d.info["docker"]["compose_file"] == "devel.yaml"
    assert d.info["docker"]["service"] == "odoo"
    assert d.info["docker"]["command"] == "odoo"


def test_docker_backend_diagnose_honors_custom_compose_file(tmp_project, monkeypatch):
    """``diagnose`` resolves the effective compose file from config/options."""
    monkeypatch.setattr(
        "osh.plugins.osh_docker.utils._find_compose_tool",
        lambda: ["docker", "compose"],
    )
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        "service = 'odoo'\ncommand = 'odoo'\ncompose_file = 'devel.yaml'\n"
    )
    (tmp_project / "devel.yaml").write_text("services:\n  odoo:\n")

    backend = DockerBackend()
    d = backend.diagnose(tmp_project, phase="run")

    assert d.ready
    assert not d.errors
    assert "generated_compose_file" in d.info["docker"]
    assert str(tmp_project / "devel.yaml") in d.info["docker"]["generated_compose_file"]


def test_docker_backend_diagnose_ee_sources_missing_with_version(
    tmp_project, monkeypatch
):
    """``diagnose`` allows missing source copies when a version is configured."""
    monkeypatch.setattr(
        "osh.plugins.osh_docker.utils._find_compose_tool",
        lambda: ["docker", "compose"],
    )
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        "service = 'odoo'\ncommand = 'odoo'\nedition = 'sh'\nversion = '19.0'\n"
    )
    (tmp_project / ".osh" / "docker-compose.yml").write_text("services:\n  odoo:\n")

    backend = DockerBackend()
    d = backend.diagnose(tmp_project, phase="run")

    assert d.ready
    assert not d.errors


def test_docker_backend_run_dry_run(tmp_project, capsys):
    """``run`` builds and prints the docker compose command in dry-run mode."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        "service = 'app'\ncommand = 'odoo'\ncompose_tool = 'docker compose'\n"
    )

    backend = DockerBackend()
    backend.run(None, tmp_project, ["odoo"], dry_run=True, verbose=False)

    err = capsys.readouterr().err
    assert "Would run:" in err
    assert "docker compose run --rm --service-ports app odoo" in err


def test_docker_backend_uses_container_executable(tmp_project, capsys):
    """The configured command is invoked inside the container."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "python3 -m odoo"\ncompose_tool = "docker compose"\n'
    )

    backend = DockerBackend()
    backend.run(None, tmp_project, ["odoo"], dry_run=True, verbose=False)

    err = capsys.readouterr().err
    assert "python3 -m odoo" in err


def test_docker_backend_requires_service(tmp_project):
    """``run`` fails when no service is configured."""
    backend = DockerBackend()
    with pytest.raises(click.ClickException):
        backend.run(None, tmp_project, ["odoo"], dry_run=True, verbose=False)


def test_docker_backend_compose_file_from_config(tmp_project, capsys):
    """The compose file from docker.toml is passed with ``-f``."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_file = "devel.yaml"\n'
        'compose_tool = "docker compose"\n'
    )

    backend = DockerBackend()
    backend.run(None, tmp_project, ["odoo"], dry_run=True, verbose=False)

    err = capsys.readouterr().err
    assert "docker compose -f devel.yaml run" in err


def test_docker_backend_compose_file_cli_override(tmp_project, capsys):
    """A compose file passed in the click context overrides config."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_file = "devel.yaml"\n'
        'compose_tool = "docker compose"\n'
    )

    class FakeCtx:
        params = {"compose_file": "test.yaml"}

    backend = DockerBackend()
    backend.run(FakeCtx(), tmp_project, ["odoo"], dry_run=True, verbose=False)

    err = capsys.readouterr().err
    assert "docker compose -f test.yaml run" in err


def test_docker_backend_restore_not_implemented(tmp_project):
    """``restore`` is not implemented for Docker."""
    backend = DockerBackend()
    with pytest.raises(click.ClickException):
        backend.restore(None, tmp_project, "db", tmp_project / "dump.sql")


def test_docker_backend_prune_not_implemented(tmp_project):
    """``prune`` is not implemented for Docker."""
    backend = DockerBackend()
    with pytest.raises(click.ClickException):
        backend.prune(None, tmp_project)


def test_init_docker_writes_version_and_edition(tmp_project, monkeypatch):
    """``osh init --target docker`` persists the Odoo version and edition."""
    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    ent = tmp_project / "enterprise"
    (ent / "web").mkdir(parents=True, exist_ok=True)
    (ent / "web" / "__manifest__.py").touch()

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "19.0",
            "--target",
            "docker",
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
    """``osh run --target docker`` defaults to a branch-based database name."""
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

    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(main, ["run", "--target", "docker", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "-d project-feature-x" in result.output
    assert "--db-filter ^project-feature-x$" in result.output


def test_docker_backend_run_appends_addons_path_for_sh(
    tmp_project,
    monkeypatch,
    capsys,
):
    """``run`` includes the mounted container --addons-path for sh editions."""
    _patch_docker_tools(monkeypatch)

    osh_dir = tmp_project / ".osh"
    (osh_dir / "enterprise" / "web").mkdir(parents=True, exist_ok=True)
    (osh_dir / "design-themes" / "theme_buzzy").mkdir(parents=True, exist_ok=True)
    docker_toml = osh_dir / "docker.toml"
    docker_toml.write_text(
        "service = 'odoo'\n"
        'command = "odoo"\n'
        "edition = 'sh'\n"
        "version = '19.0'\n"
    )

    backend = DockerBackend()
    backend.run(None, tmp_project, ["odoo"], dry_run=True, verbose=False)

    err = capsys.readouterr().err
    assert "--addons-path" in err
    assert "/mnt/extra-addons/.osh/enterprise" in err
    assert "/mnt/extra-addons/.osh/design-themes" in err


def test_backend_make_init_option_sets_target_group():
    """make_init_option attaches the backend name as the target_group."""
    option = DockerBackend.make_init_option(["--my-opt"], help="An option.")
    assert option.target_group == "docker"


def test_docker_backend_run_accepts_runspec(tmp_project, capsys):
    """Backend.run accepts a RunSpec as well as a raw argv list."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.write_text(
        "service = 'odoo'\ncommand = 'odoo'\ncompose_tool = 'docker compose'\n"
    )

    backend = DockerBackend()
    spec = RunSpec(argv=["odoo", "-d", "mydb"], db_name="mydb")
    backend.run(None, tmp_project, spec, dry_run=True, verbose=False)

    err = capsys.readouterr().err
    assert "-d mydb" in err


def test_load_backends_warns_on_name_collision(monkeypatch, capsys):
    """A backend name collision is reported instead of silently ignored."""
    from osh import plugin_loader

    class FakeBackend(Backend):
        name = "docker"
        backend_type = "backend"

    first = types.ModuleType("first")
    first.BACKENDS = [FakeBackend]
    second = types.ModuleType("second")
    second.BACKENDS = [FakeBackend]

    monkeypatch.setattr(
        plugin_loader,
        "_iter_plugin_modules",
        lambda: [("first", first), ("second", second)],
    )

    backends = load_backends()
    assert backends["docker"] is FakeBackend
    err = capsys.readouterr().err
    assert "Warning: backend 'docker' from 'second' conflicts" in err


def test_entry_point_plugin_loading(monkeypatch):
    """Plugins registered as Python entry points are loaded by load_plugins."""
    from osh import plugin_loader

    fake_cmd = click.Command(name="fake-cmd")

    fake_module = types.ModuleType("fake_entry_plugin")
    fake_module.get_commands = lambda: [fake_cmd]
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
        [FakeEntryPoint("fake", "fake_entry_plugin")]
    )
    monkeypatch.setattr(plugin_loader, "_metadata", fake_metadata)

    commands = [cmd for _, cmd in load_plugins()]
    assert fake_cmd in commands
