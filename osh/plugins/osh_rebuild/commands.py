"""`osh rebuild` command implementation."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import click

from ...db import _db_exists, _create_db, _drop_db
from ...utils import _find_odoo_executable, _find_project_root
from ...commands.run_cmd import _resolve_db_name
from ...plugins.osh_backup.cache import _get_cache_dir, _list_cache, _resolve_cache_id
from .neutralize import _neutralize_database
from .restore import _restore_dump


@click.command(name="rebuild")
@click.argument("dump", required=False)
@click.option(
    "--force",
    is_flag=True,
    help="Drop the target database if it already exists without prompting.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the steps that would be executed without running them.",
)
@click.pass_context
def rebuild(
    ctx: click.Context,
    dump: Optional[str],
    force: bool,
    dry_run: bool,
) -> None:  # noqa: D401
    """Restore a backup into the current branch's database and neutralize it.

    With no DUMP argument, the newest backup from the project cache is used.
    Use `cache:<id>` to pick a specific entry shown by `osh backup list`.

    Examples:

    \b
      osh rebuild
      osh rebuild cache:1
      osh rebuild /path/to/backup.zip
      osh rebuild /path/to/backup.sql.gz --force
    """

    base = _find_project_root()
    if base is None:
        raise click.ClickException(
            "Not inside an Osh project. Run 'osh init <version>' to create one."
        )

    exe = _find_odoo_executable(base)
    if not exe:
        raise click.ClickException(
            "Could not locate Odoo executable. Run 'osh init <version>' to set up the project."
        )

    dump_path = _resolve_dump(base, dump)

    db_name = _resolve_db_name(base, verbose=False)
    if not db_name:
        raise click.ClickException("Could not resolve a target database name.")

    if _db_exists(base, db_name):
        if not force:
            if not dry_run:
                confirm = click.confirm(
                    f"Database '{db_name}' already exists. Drop and rebuild?",
                    default=False,
                    err=True,
                )
                if not confirm:
                    raise click.ClickException("Rebuild aborted.")
        if dry_run:
            click.echo(f"Would drop database '{db_name}'", err=True)
        else:
            _drop_db(base, db_name)

    if dry_run:
        click.echo(f"Would create database '{db_name}'", err=True)
    else:
        try:
            _create_db(base, db_name)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc

    _restore_dump(base, dump_path, db_name, dry_run=dry_run)

    _neutralize_database(base, exe, db_name, dry_run=dry_run)

    if dry_run:
        click.echo(f"Would rebuild '{db_name}' from {dump_path}", err=True)
    else:
        click.echo(f"Rebuilt database '{db_name}' from {dump_path}", err=True)


def _resolve_dump(base: Path, dump: Optional[str]) -> Path:
    """Resolve a dump argument to an existing file path."""
    cache_dir = _get_cache_dir(base)

    if dump is None:
        entries = _list_cache(base, limit=1)
        if not entries:
            raise click.ClickException(
                "No cached backup found. Run 'osh backup download <source>' first."
            )
        return entries[0]["path"]

    if dump.startswith("cache:"):
        try:
            cache_id = int(dump[6:])
        except ValueError:
            raise click.ClickException(
                f"Invalid cache reference: {dump}. Use cache:<number>."
            )
        try:
            return _resolve_cache_id(base, cache_id)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

    path = Path(dump).expanduser()
    if not path.is_absolute() and cache_dir.exists():
        cached = cache_dir / path.name
        if cached.exists():
            return cached

    if not path.exists():
        raise click.ClickException(f"Backup file not found: {path}")

    return path.resolve()
