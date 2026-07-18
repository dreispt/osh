"""`osh init-docker` command implementation."""

from __future__ import annotations

from pathlib import Path

import click

from ...db import set_project_config
from .backends import DockerBackend


@click.command(name="init-docker")
@click.argument("version", required=False)
@click.argument(
    "directory", required=False, type=click.Path(file_okay=False, path_type=Path)
)
@click.option(
    "--service",
    default=None,
    help="Docker Compose service name for the Odoo container.",
)
@click.option(
    "--command",
    default=None,
    help="Shell-quoted command to run inside the container (e.g. 'odoo' or 'python3 -m odoo').",
)
@click.option(
    "--compose-file",
    default=None,
    help="Docker Compose file to use (e.g. devel.yaml for Doodba).",
)
def init_docker(
    version: str | None,
    directory: Path | None,
    service: str | None,
    command: str | None,
    compose_file: str | None,
) -> None:  # noqa: D401
    """Initialise a project directory for use with Docker Compose.

    VERSION: Odoo version (optional; used to select the odoo image tag).
    DIRECTORY: Project directory to initialise (defaults to the current directory).
    """
    target = (directory or Path.cwd()).expanduser().resolve()
    if not target.exists():
        click.echo(f"Creating directory {target}…", err=True)
        target.mkdir(parents=True, exist_ok=True)

    osh_dir = target / ".osh"
    osh_dir.mkdir(exist_ok=True)

    config_path = osh_dir / "config"
    if not config_path.exists():
        config_path.touch()

    backend = DockerBackend()
    ok = backend.init(
        target,
        version=version or "",
        edition="ce",
        dry_run=False,
        service=service,
        command=command,
        compose_file=compose_file,
    )

    set_project_config(target, "run", "target", "docker")

    if not ok:
        click.echo(
            f"Initialised project directory at {target} "
            "(Docker setup incomplete; see warnings above).",
            err=True,
        )
    else:
        click.echo(f"Initialised project directory at {target}")
