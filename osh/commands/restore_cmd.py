"""`osh restore` command implementation."""

from __future__ import annotations

from pathlib import Path

import click

from ..cache import get_cache_dir, list_cache, resolve_cache_id
from ..commons import find_project_root
from ..db import resolve_db_name, resolve_run_target
from ..plugin_loader import load_backends


@click.command(name="restore")
@click.argument("dump", required=False)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite the target database if it already exists.",
)
@click.option(
    "--no-neutralize",
    is_flag=True,
    help="Skip neutralizing the database after restoring.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the steps that would be executed without running them.",
)
@click.pass_context
def restore(
    ctx: click.Context,
    dump: str | None,
    force: bool,
    no_neutralize: bool,
    dry_run: bool,
) -> None:  # noqa: D401
    """Restore a backup into the current branch's database and neutralize it.

    With no DUMP argument, the newest backup from the project cache is used.
    Use `cache:<id>` to pick a specific entry shown by `osh backup list`.

    Examples:

    \b
      osh restore
      osh restore cache:1
      osh restore /path/to/backup.zip
      osh restore /path/to/backup.sql.gz --force
    """

    base = find_project_root(required=True)

    dump_path = _resolve_dump(base, dump)

    db_name = resolve_db_name(base, verbose=False)
    if not db_name:
        raise click.ClickException("Could not resolve a target database name.")

    backend_name = resolve_run_target(base, "local", ctx)
    backend_cls = load_backends().get(backend_name)
    if backend_cls is None:
        raise click.ClickException(f"Unknown restore target: {backend_name}")
    backend = backend_cls()

    backend.restore(
        ctx,
        base,
        db_name,
        dump_path,
        force=force,
        no_neutralize=no_neutralize,
        dry_run=dry_run,
    )

    if dry_run:
        click.echo(f"Would restore '{db_name}' from {dump_path}", err=True)
    else:
        click.echo(f"Restored database '{db_name}' from {dump_path}", err=True)


def _resolve_dump(base: Path, dump: str | None) -> Path:
    """Resolve a dump argument to an existing file path."""
    cache_dir = get_cache_dir(base)

    if dump is None:
        entries = list_cache(base, limit=1)
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
            return resolve_cache_id(base, cache_id)
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
