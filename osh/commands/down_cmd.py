"""`osh down` — stop resources left running by the active backend."""

import click

from ..common import find_project_root
from ..db import resolve_backend


@click.command(name="down")
@click.option(
    "--target",
    "backend_name",
    default="local",
    envvar="OSH_RUN_TARGET",
    help="Execution backend to stop (e.g. venv, docker).",
)
@click.option(
    "--compose-file",
    default=None,
    envvar="OSH_COMPOSE_FILE",
    help="Custom docker-compose file for the docker backend.",
)
@click.pass_context
def down(ctx, backend_name, compose_file):
    """Stop resources the active backend left running.

    Docker-backed projects keep their Compose stack up after ``osh odoo`` /
    ``osh shell`` / ``osh db`` so follow-up commands are fast; ``osh down``
    stops and removes it. Host backends (``local``, ``venv``) stop a rogue
    Odoo process still listening on the project's HTTP port (default 8069).
    It is always safe to run, regardless of the active backend.
    """
    base = find_project_root(required=True)
    backend = resolve_backend(ctx, base, backend_name)
    backend.down(ctx, base, compose_file=compose_file)
