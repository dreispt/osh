"""`osh init-docker` command implementation."""

from __future__ import annotations

from pathlib import Path

import click

from ...db import _record_run_target
from .backends import DockerInitBackend


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
@click.pass_context
def init_docker(
    ctx: click.Context,
    version: str | None,
    directory: Path | None,
    service: str | None,
    command: str | None,
    compose_file: str | None,
) -> None:  # noqa: D401
    """Initialise a project directory for use with an existing Docker Compose stack.

    VERSION: Odoo version (optional; not used by Docker, accepted for symmetry).
    DIRECTORY: Project directory to initialise (defaults to current directory).
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

    backend = DockerInitBackend()
    backend.pre_init(
        ctx,
        target,
        version or "",
        service=service,
        command=command,
        compose_file=compose_file,
    )
    env_ready = backend.setup_environment(
        ctx,
        target,
        osh_dir,
        {},
        version or "",
        service=service,
        command=command,
        compose_file=compose_file,
    )
    smoke_ok = True
    if env_ready:
        smoke_ok = backend.smoke_test(
            ctx,
            target,
            osh_dir,
            service=service,
            command=command,
            compose_file=compose_file,
        )
    backend.post_init(
        ctx,
        target,
        osh_dir,
        service=service,
        command=command,
        compose_file=compose_file,
    )

    _record_run_target(target, "docker")

    if not env_ready or not smoke_ok:
        click.echo(
            f"Initialised project directory at {target} "
            "(Docker setup incomplete; see warnings above).",
            err=True,
        )
    else:
        click.echo(f"Initialised project directory at {target}")
