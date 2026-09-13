"""Update Odoo modules whose code changed since the last update.

Compares a fingerprint of each project module against the fingerprint
recorded in the database after the previous update, and runs ``odoo -u``
on the ones that differ.
"""

import click

from .. import echo
from ..common import find_project_root
from ..db import resolve_db_name_for_run, sanitize_db_name
from ..update import core


@click.command(name="update")
@click.argument("modules", nargs=-1)
@click.option("-d", "--db", "db_name", help="Target database name.")
@click.option(
    "--all",
    "update_all",
    is_flag=True,
    help="Force-update all installed third-party modules.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be updated without running anything.",
)
@click.option(
    "--target",
    "backend_name",
    default=None,
    envvar="OSH_RUN_TARGET",
    help="Execution backend for the update (e.g. local, docker).",
)
@click.option(
    "--compose-file",
    default=None,
    envvar="OSH_COMPOSE_FILE",
    help="Custom docker-compose file for the docker backend.",
)
@click.option(
    "--no-submodules",
    "skip_nested",
    is_flag=True,
    help="Skip modules inside nested git repositories (odoo, submodules).",
)
@click.option(
    "--status",
    is_flag=True,
    help="Report changed modules without updating.",
)
@click.option(
    "-1",
    "--per-line",
    is_flag=True,
    help="List modules one per line instead of comma-separated.",
)
@click.pass_context
def update(
    ctx,
    modules,
    db_name,
    update_all,
    dry_run,
    backend_name,
    compose_file,
    skip_nested,
    status,
    per_line,
):
    """Update Odoo modules whose code changed since the last update.

    With no arguments, fingerprints all project modules and updates the
    installed ones that changed since the last update. MODULES lists
    module names to update unconditionally, e.g. ``osh update sale crm``.
    ``--all`` force-updates every installed third-party module.
    """
    if status and modules:
        raise click.UsageError("--status cannot be combined with module names.")
    if modules and update_all:
        raise click.UsageError("Module names cannot be combined with --all.")

    base = find_project_root(required=True)
    db_name = sanitize_db_name(db_name) if db_name else resolve_db_name_for_run(base)

    if modules:
        targets = sorted(set(modules))
    else:
        targets = core.detect_targets(
            base,
            db_name,
            update_all=update_all,
            status=status,
            dry_run=dry_run,
            skip_nested=skip_nested,
            per_line=per_line,
        )
        if targets is None:
            return
        if not targets:
            echo.success("All modules up to date.")
            return

    core.update_and_record(
        base,
        db_name,
        targets,
        backend_name=backend_name,
        compose_file=compose_file,
        dry_run=dry_run,
        skip_nested=skip_nested,
        per_line=per_line,
    )
