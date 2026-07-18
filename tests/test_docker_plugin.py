"""Tests for the built-in Docker backend plugin."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from osh.cli import main
from osh.plugin_loader import load_backends
from osh.plugins.osh_docker.backends import DockerBackend
from osh.plugins.osh_docker.commands import init_docker


def test_docker_backends_are_registered() -> None:
    """The docker plugin registers the unified Docker backend."""
    backends = load_backends("backend")
    assert "docker" in backends
    assert backends["docker"].name == "docker"
    assert backends["docker"].backend_type == "backend"


def _patch_docker_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make Docker tooling no-ops so tests do not require a Docker daemon."""
    monkeypatch.setattr(
        "osh.plugins.osh_docker.backends.ensure_tool", lambda _name: None
    )

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        cmd = args[0] if args else kwargs.get("args", [])
        return subprocess.CompletedProcess(cmd, returncode=0)

    monkeypatch.setattr("osh.plugins.osh_docker.backends.subprocess.run", fake_run)


def test_init_target_docker_via_main_writes_compose_file(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_init_docker_command_writes_config_and_compose(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_init_docker_does_not_overwrite_existing_osh_compose(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing ``.osh/docker-compose.yml`` is left untouched."""
    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    existing = tmp_project / ".osh" / "docker-compose.yml"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("existing: compose\n")

    runner = CliRunner()
    result = runner.invoke(init_docker, ["19.0"])

    assert result.exit_code == 0, result.output
    assert existing.read_text() == "existing: compose\n"
    assert (
        "compose_file = '.osh/docker-compose.yml'"
        in (tmp_project / ".osh" / "docker.toml").read_text()
    )


def test_init_docker_persists_provided_compose_file(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_init_docker_missing_compose_file_raises(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_init_docker_dry_run_does_not_write(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_docker_backend_status(tmp_project: Path) -> None:
    """``status`` returns diagnostic lines for the configured stack."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        "service = 'odoo'\ncommand = 'odoo'\ncompose_file = 'devel.yaml'\n"
    )

    backend = DockerBackend()
    lines = backend.status(None, tmp_project)

    assert "compose: devel.yaml" in lines
    assert "service: odoo" in lines
    assert "command: odoo" in lines


def test_docker_backend_run_dry_run(
    tmp_project: Path, capsys: pytest.CaptureFixture
) -> None:
    """``run`` builds and prints the docker compose command in dry-run mode."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text("service = 'app'\ncommand = 'odoo'\n")

    backend = DockerBackend()
    backend.run(None, tmp_project, ["odoo"], dry_run=True, verbose=False)

    err = capsys.readouterr().err
    assert "Would run:" in err
    assert "docker compose run --rm --service-ports app odoo" in err


def test_docker_backend_uses_container_executable(
    tmp_project: Path, capsys: pytest.CaptureFixture
) -> None:
    """The configured command is invoked inside the container."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text('service = "odoo"\ncommand = "python3 -m odoo"\n')

    backend = DockerBackend()
    backend.run(None, tmp_project, ["odoo"], dry_run=True, verbose=False)

    err = capsys.readouterr().err
    assert "python3 -m odoo" in err


def test_docker_backend_requires_service(tmp_project: Path) -> None:
    """``run`` fails when no service is configured."""
    backend = DockerBackend()
    with pytest.raises(click.ClickException):
        backend.run(None, tmp_project, ["odoo"], dry_run=True, verbose=False)


def test_docker_backend_compose_file_from_config(
    tmp_project: Path, capsys: pytest.CaptureFixture
) -> None:
    """The compose file from docker.toml is passed with ``-f``."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_file = "devel.yaml"\n'
    )

    backend = DockerBackend()
    backend.run(None, tmp_project, ["odoo"], dry_run=True, verbose=False)

    err = capsys.readouterr().err
    assert "docker compose -f devel.yaml run" in err


def test_docker_backend_compose_file_cli_override(
    tmp_project: Path, capsys: pytest.CaptureFixture
) -> None:
    """A compose file passed in the click context overrides config."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_file = "devel.yaml"\n'
    )

    class FakeCtx:
        params = {"compose_file": "test.yaml"}

    backend = DockerBackend()
    backend.run(FakeCtx(), tmp_project, ["odoo"], dry_run=True, verbose=False)

    err = capsys.readouterr().err
    assert "docker compose -f test.yaml run" in err


def test_docker_backend_restore_not_implemented(tmp_project: Path) -> None:
    """``restore`` is not implemented for Docker."""
    backend = DockerBackend()
    with pytest.raises(click.ClickException):
        backend.restore(None, tmp_project, "db", tmp_project / "dump.sql")


def test_docker_backend_prune_not_implemented(tmp_project: Path) -> None:
    """``prune`` is not implemented for Docker."""
    backend = DockerBackend()
    with pytest.raises(click.ClickException):
        backend.prune(None, tmp_project)


def test_init_docker_writes_version_and_edition(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_docker_backend_run_appends_addons_path_for_sh(
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
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
