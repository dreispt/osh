"""Docker Compose backend implementation for ``osh init`` and ``osh run``."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

import click

from ...backends import Backend
from ...utils import _ensure_tool

_DOCKER_TOML = Path(".osh") / "docker.toml"
_COMPOSE_FILE = Path(".osh") / "docker-compose.yml"


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


def _default_compose_content(version: str) -> str:
    """Return a generated Docker Compose file for a standard Odoo stack."""
    image = f"odoo:{version}" if version else "odoo:latest"
    return f"""services:
  odoo:
    image: {image}
    depends_on:
      - db
    ports:
      - "8069:8069"
    environment:
      HOST: db
      USER: odoo
      PASSWORD: myodoo
      PORT: 5432
    volumes:
      - odoo-web-data:/var/lib/odoo
      - ..:/mnt/extra-addons
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: odoo
      POSTGRES_PASSWORD: myodoo
      POSTGRES_DB: postgres
    volumes:
      - odoo-db-data:/var/lib/postgresql/data

volumes:
  odoo-web-data:
  odoo-db-data:
"""


def _generate_compose_file(target: Path, version: str) -> None:
    """Write ``.osh/docker-compose.yml`` if it does not already exist."""
    compose_path = target / _COMPOSE_FILE
    compose_path.parent.mkdir(parents=True, exist_ok=True)
    compose_path.write_text(_default_compose_content(version))


class DockerBackend(Backend):
    """Unified Docker Compose backend for ``osh init`` and ``osh run``."""

    name = "docker"
    label = "Docker Compose"
    backend_type = "backend"

    def status(
        self, ctx: click.Context, target: Path, *, verbose: bool = False
    ) -> list[str]:
        """Return diagnostic lines for ``osh doctor`` and the init plan."""
        cfg = _load_docker_config(target)
        return [
            f"compose: {cfg.get('compose_file', '<none>')}",
            f"service: {cfg.get('service', 'odoo')}",
            f"command: {cfg.get('command', 'odoo')}",
        ]

    def init(
        self,
        ctx: click.Context,
        target: Path,
        *,
        version: str = "",
        edition: str = "ce",
        dry_run: bool = False,
        **options: Any,
    ) -> bool:
        """Set up the project to run Odoo with Docker Compose."""
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

        service = options.get("service")
        command = options.get("command")
        compose_file = options.get("compose_file")

        if compose_file and not (target / compose_file).is_file():
            raise click.ClickException(
                f"Compose file '{compose_file}' not found in {target}."
            )

        if not compose_file:
            osh_compose = target / _COMPOSE_FILE
            if not osh_compose.is_file():
                if dry_run:
                    click.echo(
                        f"Would generate {osh_compose} with "
                        f"odoo/{version or 'latest'} and postgres:16 services.",
                        err=True,
                    )
                else:
                    _generate_compose_file(target, version)
                    click.echo(f"Generated {osh_compose}.", err=True)
            if not dry_run:
                compose_file = str(_COMPOSE_FILE)

        if dry_run:
            click.echo(
                f"Would write {target / _DOCKER_TOML}: "
                f"service={service or 'odoo'}, command={command or 'odoo'}, "
                f"compose_file={compose_file or '<none>'}.",
                err=True,
            )
            return True

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

        cfg = _load_docker_config(target)
        svc = cfg.get("service")
        cmd = _docker_command(svc, cfg.get("command"))
        if not svc:
            click.echo(
                "Warning: no Docker service configured; skipping smoke test.",
                err=True,
            )
            return True

        click.echo("Running quick Odoo smoke test in container…", err=True)
        compose_cmd = _compose_base_command(target, ctx)
        try:
            subprocess.run(
                [*compose_cmd, "run", "--rm", svc, *cmd, "--version"],
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

        click.echo(f"Run the project with: osh run (in {target})", err=True)
        return True

    def run(
        self,
        ctx: click.Context,
        base: Path,
        args: list[str],
        *,
        dry_run: bool = False,
        verbose: bool = False,
        **options: Any,
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

    def restore(
        self,
        ctx: click.Context,
        base: Path,
        db_name: str,
        dump_path: Path,
        *,
        filestore_path: Path | None = None,
        no_neutralize: bool = False,
        dry_run: bool = False,
        **options: Any,
    ) -> None:
        """Restore a backup into the target database through this backend."""
        raise NotImplementedError("Docker restore is not implemented.")

    def prune(
        self,
        ctx: click.Context,
        base: Path,
        *,
        aggressive: bool = False,
        dry_run: bool = False,
        **options: Any,
    ) -> None:
        """Run target-specific housekeeping."""
        raise NotImplementedError("Docker prune is not implemented.")
