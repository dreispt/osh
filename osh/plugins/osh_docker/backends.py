"""Docker Compose backend implementation for ``osh init`` and ``osh run``."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import click

from ...backends import Backend
from ...commons import ensure_tool
from ...odoo_layout import build_addons_paths
from ...sources import ensure_osh_sources
from .utils import (
    _COMPOSE_FILE,
    _DOCKER_TOML,
    _compose_base_command,
    _docker_command,
    _generate_compose_file,
    _load_docker_config,
    _save_docker_config,
)


class DockerBackend(Backend):
    """Unified Docker Compose backend for ``osh init`` and ``osh run``."""

    name = "docker"
    label = "Docker Compose"
    backend_type = "backend"
    neutralize_supported = True
    description = (
        "Run Odoo inside a Docker Compose stack; generates a compose file if missing."
    )
    help_text = (
        "Writes ``.osh/docker.toml`` with the service name, command, and optional "
        "compose file path. If no compose file exists, generates ``.osh/docker-compose.yml`` "
        "with a standard Odoo + PostgreSQL stack using the requested version as the "
        "image tag.\n\n"
        "Requires Docker and the Docker Compose plugin on PATH."
    )

    @classmethod
    def get_init_options(cls) -> list[click.Option]:
        opts = [
            click.Option(
                ["--service"],
                help="Docker Compose service name for the Odoo container.",
            ),
            click.Option(
                ["--command"],
                help="Shell-quoted command to run inside the container "
                "(e.g. 'odoo' or 'python3 -m odoo').",
            ),
            click.Option(
                ["--compose-file"],
                help="Docker Compose file to use (e.g. devel.yaml for Doodba).",
            ),
        ]
        for o in opts:
            o.target_group = cls.name
        return opts

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
        target: Path,
        *,
        version: str = "",
        edition: str = "ce",
        dry_run: bool = False,
        **options: Any,
    ) -> bool:
        """Set up the project to run Odoo with Docker Compose."""
        service = options.get("service")
        command = options.get("command")
        compose_file = options.get("compose_file")

        source_kwargs = {
            k: options[k]
            for k in ("odoo_source", "enterprise_source", "themes_source")
            if k in options
        }

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
            compose_file = str(_COMPOSE_FILE)

        if dry_run:
            click.echo(
                f"Would write {target / _DOCKER_TOML}: "
                f"service={service or 'odoo'}, command={command or 'odoo'}, "
                f"compose_file={compose_file or '<none>'}, "
                f"version={version!r}, edition={edition!r}.",
                err=True,
            )
            ensure_osh_sources(
                target,
                version,
                edition,
                dry_run=True,
                skip_odoo=True,
                assume_yes=True,
                **source_kwargs,
            )
            return True

        ensure_tool("docker")
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

        _save_docker_config(
            target, service, command, compose_file, version=version, edition=edition
        )
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

        ensure_osh_sources(
            target,
            version,
            edition,
            dry_run=False,
            skip_odoo=True,
            assume_yes=True,
            **source_kwargs,
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

        click.echo("Running quick Odoo smoke test in container\u2026", err=True)
        compose_cmd = _compose_base_command(target, compose_file=compose_file)
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

        edition = options.get("edition") or cfg.get("edition") or "ce"
        version = cfg.get("version", "")

        if edition in ("ee", "sh") and not version:
            required = ["enterprise"]
            if edition == "sh":
                required.append("design-themes")
            missing = [name for name in required if not (base / ".osh" / name).exists()]
            if missing:
                raise click.ClickException(
                    "Project is missing required source copies and no version is "
                    "configured. Run 'osh init ...' first."
                )

        ensure_osh_sources(
            base,
            version,
            edition,
            dry_run=dry_run,
            skip_odoo=True,
            assume_yes=True,
        )

        odoo_args = args[1:]  # args[0] is the host executable placeholder

        if "--addons-path" not in odoo_args:
            addons_paths = build_addons_paths(base, include_themes=True)
            container_paths = [
                f"/mnt/extra-addons/{p.relative_to(base)}"
                for p in addons_paths
                if p.is_relative_to(base)
            ]
            if container_paths:
                odoo_args.extend(["--addons-path", ",".join(container_paths)])

        odoo_command = _docker_command(service, command)
        cli_params = getattr(ctx, "params", {}) or {}
        compose_cmd = _compose_base_command(
            base, compose_file=cli_params.get("compose_file")
        )
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
        force: bool = False,
        no_neutralize: bool = False,
        dry_run: bool = False,
        **options: Any,
    ) -> None:
        """Restore a backup into the target database through this backend."""
        raise click.ClickException("Docker restore is not yet implemented.")

    def neutralize(
        self,
        ctx: click.Context,
        base: Path,
        db_name: str,
        *,
        dry_run: bool = False,
    ) -> None:
        """Neutralize *db_name* by running ``odoo-bin neutralize`` in the container."""
        self.run(
            ctx,
            base,
            ["odoo", "-d", db_name, "neutralize"],
            dry_run=dry_run,
            verbose=False,
        )

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
        raise click.ClickException("Docker prune is not yet implemented.")
