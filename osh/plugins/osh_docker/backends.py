"""Docker Compose backend implementations for ``osh init`` and ``osh run``."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

import click

from ...backends import InitBackend, RunBackend
from ...utils import _ensure_tool

_DOCKER_TOML = Path(".osh") / "docker.toml"


def _load_docker_config(base: Path) -> dict[str, Any]:
    """Load the Docker backend configuration from ``.osh/docker.toml``."""
    config_path = base / _DOCKER_TOML
    if not config_path.exists():
        return {}
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]
    with config_path.open("rb") as f:
        return tomllib.load(f)


def _save_docker_config(
    base: Path,
    service: str | None,
    command: str | None,
    compose_file: str | None = None,
) -> None:
    """Write ``.osh/docker.toml`` with the selected service and command."""
    config_path = base / _DOCKER_TOML
    config_path.parent.mkdir(parents=True, exist_ok=True)
    service = service or "odoo"
    command = command or "odoo"
    lines = [f"service = {service!r}", f"command = {command!r}"]
    if compose_file:
        lines.append(f"compose_file = {compose_file!r}")
    config_path.write_text("\n".join(lines) + "\n")


def _docker_command(service: str, command: str | list[str] | None) -> list[str]:
    """Return the Odoo command inside the container as a list."""
    if command is None:
        command = "odoo"
    if isinstance(command, list):
        return list(command)
    return shlex.split(str(command))


def _compose_base_command(base: Path, ctx: click.Context | None) -> list[str]:
    """Return the ``docker compose`` invocation, including any ``-f`` option."""
    cfg = _load_docker_config(base)
    cli_params = getattr(ctx, "params", {}) if ctx else {}
    compose_file = cli_params.get("compose_file") or cfg.get("compose_file")
    cmd = ["docker", "compose"]
    if compose_file:
        cmd.extend(["-f", str(compose_file)])
    return cmd


class DockerInitBackend(InitBackend):
    """Initialise a project for use with an existing Docker Compose stack."""

    name = "docker"
    label = "Docker Compose"

    def pre_init(
        self, ctx: click.Context, target: Path, version: str, **options: Any
    ) -> None:
        """Check that the Docker tooling and requested compose file are available."""
        _ensure_tool("docker")
        try:
            subprocess.run(
                ["docker", "compose", "version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            raise click.ClickException(
                "Docker Compose plugin is required for init-docker."
            ) from exc

        cli_params = getattr(ctx, "params", {}) if ctx else {}
        compose_file = cli_params.get("compose_file") or options.get("compose_file")
        if compose_file and not (target / compose_file).is_file():
            raise click.ClickException(
                f"Compose file '{compose_file}' not found in {target}."
            )

    def setup_environment(
        self,
        ctx: click.Context,
        target: Path,
        osh_dir: Path,
        sources: dict[str, Path | None],
        version: str,
        **options: Any,
    ) -> bool:
        """Write ``.osh/docker.toml`` for the project."""
        cli_params = getattr(ctx, "params", {}) if ctx else {}
        service = cli_params.get("service") or options.get("service")
        command = cli_params.get("command") or options.get("command")
        compose_file = cli_params.get("compose_file") or options.get("compose_file")
        _save_docker_config(target, service, command, compose_file)
        click.echo(
            f"Wrote Docker backend config to {target / _DOCKER_TOML}.",
            err=True,
        )
        if not service:
            click.echo(
                "Warning: no --service provided; defaulting to 'odoo'. "
                f"Edit {target / _DOCKER_TOML} if your compose service is named differently.",
                err=True,
            )
        return True

    def smoke_test(
        self, ctx: click.Context, target: Path, osh_dir: Path, **options: Any
    ) -> bool:
        """Run the configured container command with ``--version`` as a smoke test."""
        cfg = _load_docker_config(target)
        service = cfg.get("service")
        command = _docker_command(service, cfg.get("command"))
        if not service:
            click.echo(
                "Warning: no Docker service configured; skipping smoke test.",
                err=True,
            )
            return True
        click.echo("Running quick Odoo smoke test in container…", err=True)
        compose_cmd = _compose_base_command(target, ctx)
        try:
            subprocess.run(
                [*compose_cmd, "run", "--rm", service, *command, "--version"],
                cwd=target,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except subprocess.CalledProcessError as exc:
            stdout = exc.stdout.decode("utf-8", errors="replace") if exc.stdout else ""
            click.echo(
                f"Warning: Odoo smoke test failed (exit status {exc.returncode}).\n"
                f"{stdout}\n"
                "The project is initialised but Odoo may not be usable.",
                err=True,
            )
            return False
        except FileNotFoundError:
            click.echo(
                "Warning: Docker command could not be executed. "
                "The project is initialised but Odoo may not be usable.",
                err=True,
            )
            return False
        return True

    def post_init(
        self, ctx: click.Context, target: Path, osh_dir: Path, **options: Any
    ) -> None:
        """Print post-initialisation hint for Docker users."""
        click.echo(
            f"Run the project with: osh run (in {target})",
            err=True,
        )


class DockerRunBackend(RunBackend):
    """Run Odoo commands through an existing Docker Compose stack."""

    name = "docker"
    label = "Docker Compose"

    def run(
        self,
        ctx: click.Context,
        base: Path,
        args: list[str],
        *,
        dry_run: bool,
        verbose: bool,
    ) -> None:
        """Translate host odoo-bin arguments into a Docker Compose invocation."""
        cfg = _load_docker_config(base)
        service = cfg.get("service")
        command = cfg.get("command")
        if not service:
            raise click.ClickException(
                "No Docker service configured. Run "
                "'osh init-docker --service <name>' or edit "
                f"{base / _DOCKER_TOML}."
            )

        odoo_args = args[1:]  # args[0] is the host executable placeholder
        odoo_command = _docker_command(service, command)
        compose_cmd = _compose_base_command(base, ctx)
        docker_args = [
            *compose_cmd,
            "run",
            "--rm",
            "--service-ports",
            service,
            *odoo_command,
            *odoo_args,
        ]

        if dry_run:
            click.echo(f"Would run: {' '.join(docker_args)}", err=True)
            return

        if verbose:
            click.echo(f"Running: {' '.join(docker_args)}", err=True)
        else:
            click.echo(f"Running {' '.join(docker_args)}", err=True)

        try:
            os.execvp("docker", docker_args)
        except Exception as exc:  # pragma: no cover
            raise click.ClickException(str(exc))
