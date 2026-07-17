"""Tests for the built-in Docker backend plugin."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from osh.cli import main
from osh.commands.run_cmd import run
from osh.plugin_loader import load_backends


def test_docker_backends_are_registered() -> None:
    """The docker plugin registers init and run backends."""
    init_backends = load_backends("init")
    assert "docker" in init_backends
    assert init_backends["docker"].name == "docker"

    run_backends = load_backends("run")
    assert "docker" in run_backends
    assert run_backends["docker"].name == "docker"


def _patch_docker_tools(monkeypatch) -> None:
    """Make Docker commands no-ops so tests do not require a Docker daemon."""
    monkeypatch.setattr(
        "osh.plugins.osh_docker.backends._ensure_tool", lambda _name: None
    )
    monkeypatch.setattr(
        "osh.plugins.osh_docker.backends.subprocess.check_call", lambda *a, **k: None
    )

    def fake_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        return subprocess.CompletedProcess(cmd, returncode=0)

    monkeypatch.setattr("osh.plugins.osh_docker.backends.subprocess.run", fake_run)


def test_init_target_docker_writes_docker_toml(tmp_project: Path, monkeypatch) -> None:
    """``osh init --target docker`` writes ``.osh/docker.toml``."""
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
    assert not (tmp_project / "docker-compose.yml").exists()
    assert not (tmp_project / "Dockerfile").exists()


def test_init_target_docker_no_compose_files_generated(
    tmp_project: Path, monkeypatch
) -> None:
    """The docker backend does not overwrite an existing compose file."""
    existing = tmp_project / "docker-compose.yml"
    existing.write_text('version: "2"\nservices:\n  app:\n    image: odoo\n')

    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(main, ["init", "19.0", "--target", "docker"])

    assert result.exit_code == 0, result.output
    assert existing.read_text() == (
        'version: "2"\nservices:\n  app:\n    image: odoo\n'
    )


def test_run_target_docker_dry_run_uses_config(in_project: Path, monkeypatch) -> None:
    """``osh run --target docker --dry-run`` uses the configured service/command."""
    docker_toml = in_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text('service = "app"\ncommand = "odoo"\n')
    (in_project / ".osh" / "config").touch()

    monkeypatch.setattr(
        "osh.commands.run_cmd._resolve_db_name", lambda _base, _verbose: "testdb"
    )

    runner = CliRunner()
    result = runner.invoke(run, ["--target", "docker", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "docker compose run --rm --service-ports app odoo" in result.output
    assert "-d testdb" in result.output
    assert "--db-filter ^testdb$" in result.output
    assert "--config" not in result.output
    assert "--addons-path" not in result.output


def test_docker_run_backend_uses_container_executable(
    tmp_project: Path, monkeypatch
) -> None:
    """The docker run backend invokes the configured command inside the container."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text('service = "odoo"\ncommand = "python3 -m odoo"\n')

    monkeypatch.setattr("osh.plugins.osh_docker.backends.os.execvp", lambda *a: None)

    from osh.plugins.osh_docker.backends import DockerRunBackend

    backend = DockerRunBackend()
    backend.run(None, tmp_project, ["odoo"], dry_run=True, verbose=False)


def test_docker_run_backend_requires_service(tmp_project: Path) -> None:
    """The docker run backend fails when no service is configured."""
    from osh.plugins.osh_docker.backends import DockerRunBackend

    backend = DockerRunBackend()
    with pytest.raises(Exception):
        backend.run(None, tmp_project, ["odoo"], dry_run=True, verbose=False)


def test_doodba_compose_file_is_used(in_project: Path, monkeypatch) -> None:
    """``--compose-file`` (or ``compose_file`` in config) passes ``-f`` to compose."""
    docker_toml = in_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_file = "devel.yaml"\n'
    )
    (in_project / ".osh" / "config").touch()

    monkeypatch.setattr(
        "osh.commands.run_cmd._resolve_db_name", lambda _base, _verbose: "testdb"
    )

    runner = CliRunner()
    result = runner.invoke(run, ["--target", "docker", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "docker compose -f devel.yaml run" in result.output


def test_doodba_compose_file_override(in_project: Path, monkeypatch) -> None:
    """``--compose-file`` on the command line overrides the config file."""
    docker_toml = in_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_file = "devel.yaml"\n'
    )
    (in_project / ".osh" / "config").touch()

    monkeypatch.setattr(
        "osh.commands.run_cmd._resolve_db_name", lambda _base, _verbose: "testdb"
    )

    runner = CliRunner()
    result = runner.invoke(
        run,
        ["--target", "docker", "--compose-file", "test.yaml", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "docker compose -f test.yaml run" in result.output


def test_run_remembers_target_from_init(tmp_project: Path, monkeypatch) -> None:
    """``osh run`` without ``--target`` uses the target from the last ``osh init``."""
    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    init_result = runner.invoke(
        main, ["init", "19.0", "--target", "docker", "--service", "odoo"]
    )
    assert init_result.exit_code == 0, init_result.output

    monkeypatch.setattr(
        "osh.commands.run_cmd._resolve_db_name", lambda _base, _verbose: "testdb"
    )
    run_result = runner.invoke(run, ["--dry-run"])

    assert run_result.exit_code == 0, run_result.output
    assert "docker compose run" in run_result.output


def test_run_explicit_target_overrides_init(tmp_project: Path, monkeypatch) -> None:
    """An explicit ``--target`` on ``osh run`` overrides the saved init target."""
    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    init_result = runner.invoke(
        main, ["init", "19.0", "--target", "docker", "--service", "odoo"]
    )
    assert init_result.exit_code == 0, init_result.output

    # The local target is missing a venv, so it should fail immediately.
    run_result = runner.invoke(run, ["--target", "local", "--dry-run"])
    assert run_result.exit_code != 0


def test_init_target_docker_writes_compose_file(tmp_project: Path, monkeypatch) -> None:
    """``--compose-file`` is persisted to ``.osh/docker.toml``."""
    (tmp_project / "devel.yaml").write_text("services:\n  odoo:\n    image: odoo\n")

    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

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
            "--compose-file",
            "devel.yaml",
        ],
    )

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert "compose_file = 'devel.yaml'" in docker_toml.read_text()
