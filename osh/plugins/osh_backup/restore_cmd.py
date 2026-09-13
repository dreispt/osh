"""`osh db restore` command provided by the osh_backup plugin."""

import click

from ... import echo
from ...commands.helpers import collect_diagnostics
from ...commands.odoo_cmd import odoo
from ...common import find_project_root
from ...db import (
    create_db,
    db_exists,
    drop_db,
    get_database_version,
    resolve_db_name,
    resolve_run_target,
    sanitize_db_name,
    set_last_db,
)
from ...utils.odoo_layout import find_odoo_executable
from ...utils.plugin_loader import load_backends
from ...utils.version import get_version_tuple
from . import restore_ops
from .remotes import newest_cache_for_remote


@click.command(name="restore")
@click.argument("dump", required=False)
@click.option(
    "--list",
    "list_backups",
    is_flag=True,
    help="List cached backups instead of restoring.",
)
@click.option(
    "--limit",
    default=20,
    show_default=True,
    help="Maximum number of backups to show (with --list).",
)
@click.option(
    "--reverse",
    is_flag=True,
    help="List oldest backups first (with --list).",
)
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
    "-d",
    "--db",
    "target_db",
    default=None,
    help="Target database name to restore into (defaults to the branch database).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the steps that would be executed without running them.",
)
@click.pass_context
def restore(
    ctx,
    dump,
    list_backups,
    limit,
    reverse,
    force,
    no_neutralize,
    target_db,
    dry_run,
):  # noqa: D401
    """Restore a backup into the current branch's database and neutralize it.

    With no DUMP argument, the newest backup from the project cache is used.
    Use `cache:<id>` to pick a specific entry shown by `osh db restore --list`.

    PostgreSQL credentials are read from ``.osh/odoo.conf`` (or ``.odoorc``)
    for every spawned tool, so no process environment changes are needed.

    The restore tool is chosen based on the backup extension:

    \b
      .dump   -> pg_restore
      .sql    -> psql -f
      .sql.gz -> gunzip -c | psql
      .zip    -> unzip + psql + filestore copy

    For `.zip` backups, the filestore directory is copied into the configured
    Odoo `data_dir` under `filestore/<dbname>/`. If `data_dir` cannot be
    determined, `osh db restore` warns and continues without the filestore.

    After the dump is restored, the database is neutralized. Odoo 16.0+ uses
    `odoo-bin neutralize -d <db>`; older versions rely on `.osh/neutralize/`
    scripts.

    Neutralization hooks:

    Custom `.sql` scripts inside `.osh/neutralize/` run after the built-in
    neutralization step, in sorted order. Use numeric prefixes to control the
    order (e.g. `000_default.sql`, `100_anonymize_partners.sql`). Global
    defaults can be placed in `~/.config/osh/neutralize/` and are copied to
    `.osh/neutralize/` during `osh init`.

    Examples:

    \b
      osh db restore
      osh db restore cache:1
      osh db restore /path/to/backup.zip
      osh db restore /path/to/backup.zip --db prod_restore
      osh db restore /path/to/backup.sql.gz --force
      osh db restore /path/to/backup.sql.gz --db prod_restore --force
      osh db restore --list
    """
    base = find_project_root(required=True)

    if list_backups:
        restore_ops.list_cached_backups(base, limit=limit, reverse=reverse)
        return

    dump_path = newest_cache_for_remote(base, dump)
    if dump_path is None:
        dump_path = restore_ops.resolve_backup_path(base, dump)

    db_name = (
        sanitize_db_name(target_db)
        if target_db
        else resolve_db_name(base, verbose=False)
    )
    if not db_name:
        raise click.ClickException("Could not resolve a target database name.")

    backend_name = resolve_run_target(base, "local", ctx)
    backend_cls = load_backends().get(backend_name)
    if backend_cls is None:
        raise click.ClickException(f"Unknown restore target: {backend_name}")
    backend = backend_cls()

    diagnostics = collect_diagnostics(
        base,
        backend,
        ctx,
        target=backend_name,
        phase="run",
        sections=backend.diagnose_sections_for_phase("run"),
    )
    for warning_msg in diagnostics.warnings:
        echo.warning(warning_msg)
    if diagnostics.errors:
        raise click.ClickException("\n".join(diagnostics.errors))

    if not dry_run:
        set_last_db(base, db_name)

    if db_exists(base, db_name):
        if not force:
            raise click.ClickException(
                f"Database '{db_name}' already exists. Use --force to overwrite."
            )
        if dry_run:
            echo.info(f"Would drop database '{db_name}'", err=True)
        else:
            drop_db(base, db_name)

    if dry_run:
        echo.info(f"Would create database '{db_name}'", err=True)
        restore_ops.restore_dump(base, dump_path, db_name, dry_run=True)
    else:
        create_db(base, db_name)
        restore_ops.restore_dump(base, dump_path, db_name, dry_run=False)

    if not no_neutralize:
        _neutralize(ctx, base, db_name, backend_name, dry_run=dry_run)

    if not dry_run:
        if no_neutralize:
            echo.info(
                f"Restored database '{db_name}' from {dump_path} "
                "(neutralization skipped)",
                err=True,
            )
        else:
            echo.info(
                f"Restored and neutralized database '{db_name}' from {dump_path}",
                err=True,
            )


def _neutralize(ctx, base, db_name, backend_name, *, dry_run=False):
    """Neutralize the restored database using Odoo's command and/or SQL scripts.

    The neutralization method is chosen from the *database* version, not the
    *local* Odoo version. This lets a user restore an older dump (e.g. 14.0)
    into a newer project (e.g. 19.0) without the built-in ``odoo-bin neutralize``
    failing on missing tables/columns.
    """
    if dry_run:
        # The database does not exist in dry-run mode, so just preview the
        # built-in neutralize command. The real method is decided after restore.
        ctx.invoke(
            odoo,
            dry_run=True,
            backend_name=backend_name,
            compose_file=None,
            no_db_filter=True,
            extra_args=("neutralize", "-d", db_name),
        )
        restore_ops.run_project_neutralize_scripts(base, db_name, dry_run=True)
        return

    db_version = get_database_version(base, db_name)
    exe = find_odoo_executable(base)
    local_version = get_version_tuple(exe) if exe else None

    use_odoo = (
        db_version is not None
        and db_version >= (16, 0)
        and local_version is not None
        and db_version == local_version
    )

    if use_odoo:
        ctx.invoke(
            odoo,
            dry_run=False,
            backend_name=backend_name,
            compose_file=None,
            no_db_filter=True,
            extra_args=("neutralize", "-d", db_name),
        )
    else:
        if db_version is None:
            echo.warning(
                f"Could not determine database version for '{db_name}'; "
                "using SQL fallback neutralization."
            )
        elif local_version is not None and db_version != local_version:
            echo.warning(
                f"Database is {db_version[0]}.{db_version[1]}, local Odoo is "
                f"{local_version[0]}.{local_version[1]}; using SQL fallback "
                "neutralization."
            )
        restore_ops.neutralize_with_sql(base, db_name)

    restore_ops.run_project_neutralize_scripts(base, db_name, dry_run=dry_run)
