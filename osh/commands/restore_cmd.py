"""`osh restore` command implementation."""

import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import click

from .. import echo
from ..common import (
    detect_backup_format_by_content,
    ensure_tool,
    find_project_root,
    get_odoo_data_dir,
    run_shell_pipeline,
    run_subprocess,
)
from ..db import (
    create_db,
    db_exists,
    drop_db,
    get_pg_credentials,
    get_project_config,
    resolve_db_name,
    resolve_run_target,
    run_psql_script,
    sanitize_db_name,
)
from ..utils.cache import get_cache_dir, list_cache, read_metadata, resolve_cache_id
from ..utils.plugin_loader import load_backends
from .env_cmd import prepare_env_context
from .helpers import collect_diagnostics
from .odoo_cmd import odoo


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
    Use `cache:<id>` to pick a specific entry shown by `osh restore --list`.

    This command layers on top of ``osh env``: it sets up the same runtime
    environment (``ODOO_RC`` and PostgreSQL connection variables) and uses
    those variables to run the restore tools.

    The restore tool is chosen based on the backup extension:

    \b
      .dump   -> pg_restore
      .sql    -> psql -f
      .sql.gz -> gunzip -c | psql
      .zip    -> unzip + psql + filestore copy

    For `.zip` backups, the filestore directory is copied into the configured
    Odoo `data_dir` under `filestore/<dbname>/`. If `data_dir` cannot be
    determined, `osh restore` warns and continues without the filestore.

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
      osh restore
      osh restore cache:1
      osh restore /path/to/backup.zip
      osh restore /path/to/backup.zip --db prod_restore
      osh restore /path/to/backup.sql.gz --force
      osh restore /path/to/backup.sql.gz --db prod_restore --force
      osh restore --list
    """
    base = find_project_root(required=True)

    if list_backups:
        _list_backups(base, limit=limit, reverse=reverse)
        return

    dump_path = _resolve_dump(base, dump)

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

    # Layer on the same environment preparation as ``osh env``.
    _, env_vars, _ = prepare_env_context(
        base,
        backend_name,
        db_name=db_name,
        no_db_filter=True,
        skip_config=True,
        extra_args=(),
    )
    if env_vars:
        os.environ.update(env_vars)

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
        _restore_dump(base, dump_path, db_name, dry_run=True)
    else:
        create_db(base, db_name)
        _restore_dump(base, dump_path, db_name, dry_run=False)

    if not no_neutralize:
        _neutralize(ctx, base, db_name, backend_name, dry_run=dry_run)

    if dry_run:
        echo.info(f"Would restore '{db_name}' from {dump_path}", err=True)
    else:
        echo.info(f"Restored database '{db_name}' from {dump_path}", err=True)


def _neutralize(ctx, base, db_name, backend_name, *, dry_run=False):
    """Neutralize the restored database using Odoo's command and/or project SQL scripts."""
    version = get_project_config(base, "init", "version", fallback=None)
    major = _major_version_from_string(version)

    # For Odoo >= 16.0 (or unknown versions) try the built-in neutralize subcommand.
    if major is None or major >= 16:
        ctx.invoke(
            odoo,
            dry_run=dry_run,
            backend_name=backend_name,
            compose_file=None,
            no_db_filter=True,
            skip_config=False,
            extra_args=("neutralize", "-d", db_name),
        )

    _run_project_neutralize_scripts(base, db_name, dry_run=dry_run)


def _major_version_from_string(version):
    """Return the first integer in *version*, or None if not found."""
    if not version:
        return None
    match = re.search(r"(\d+)", str(version))
    return int(match.group(1)) if match else None


def _run_project_neutralize_scripts(base, db_name, *, dry_run=False):
    """Run ``.osh/neutralize/*.sql`` scripts in sorted order."""
    neutralize_dir = base / ".osh" / "neutralize"
    if not neutralize_dir.is_dir():
        return

    scripts = sorted(neutralize_dir.glob("*.sql"))
    if not scripts:
        return

    if dry_run:
        for script in scripts:
            echo.info(f"Would run neutralization script: {script.name}", err=True)
        return

    for script in scripts:
        echo.info(f"Running neutralization script: {script.name}", err=True)
        try:
            run_psql_script(base, db_name, script)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc


def _resolve_dump(base, dump):
    """Resolve a dump argument to an existing file path."""
    cache_dir = get_cache_dir(base)

    if dump is None:
        entries = list_cache(base, limit=1)
        if not entries:
            raise click.ClickException(
                "No cached backup found. Run 'osh backup <source>' first."
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


SOURCE_COLUMN_WIDTH = 40
SOURCE_TRUNCATE_AT = SOURCE_COLUMN_WIDTH - len("...")


def _list_backups(base, *, limit, reverse):
    """List backups stored in the project cache."""
    entries = list_cache(base, limit=limit, reverse=reverse)
    if not entries:
        echo.info("No cached backups.", err=True)
        return

    echo.info(
        f"{'#':<4} {'Source':<{SOURCE_COLUMN_WIDTH}} {'Created':<20} {'Filename'}"
    )
    for entry in entries:
        source = entry["source"]
        if len(source) > SOURCE_TRUNCATE_AT + len("..."):
            source = source[:SOURCE_TRUNCATE_AT] + "..."
        echo.info(
            f"{entry['id']:<4} {source:<{SOURCE_COLUMN_WIDTH}} "
            f"{entry['created_at']:<20} {entry['filename']}"
        )


def _restore_dump(base, dump_path, target_db, *, dry_run=False):
    """Restore *dump_path* into an existing *target_db*."""
    # Use metadata format when available, falling back to content inspection
    meta = read_metadata(dump_path)
    backup_format = meta.get("format")

    # If metadata format is missing or invalid, try content inspection
    if not backup_format or backup_format not in ("dump", "sql", "sql.gz", "zip"):
        detected_format = detect_backup_format_by_content(dump_path)
        if detected_format:
            backup_format = detected_format
            echo.info(
                f"Detected backup format '{detected_format}' from file content",
                err=True,
            )
        else:
            # Last resort: try file extension
            backup_format = _dump_suffix(dump_path).lstrip(".")
            if backup_format not in ("dump", "sql", "sql.gz", "zip"):
                raise click.ClickException(
                    f"Could not determine backup format from file: {dump_path}"
                )

    echo.info(
        f"Restoring database '{target_db}' from {dump_path} (format: {backup_format})",
        err=True,
    )

    conn_args, env = get_pg_credentials(base)

    if dry_run:
        echo.info(
            f"Would restore database '{target_db}' from {dump_path}",
            err=True,
        )
        return

    if backup_format == "dump":
        ensure_tool("pg_restore")
        args = [
            "pg_restore",
            "--verbose",
            "--no-owner",
            "--dbname",
            target_db,
            *conn_args,
            str(dump_path),
        ]
        # Let pg_restore's verbose progress output go directly to stderr for user visibility
        run_subprocess(args, env=env, stderr=None, error_msg="pg_restore failed")
    elif backup_format == "sql":
        ensure_tool("psql")
        args = ["psql", "-d", target_db, "-f", str(dump_path), *conn_args]
        run_subprocess(args, env=env, error_msg="psql failed")
    elif backup_format == "sql.gz":
        ensure_tool("gunzip")
        ensure_tool("psql")
        _restore_sql_gz(dump_path, target_db, conn_args, env)
    elif backup_format == "zip":
        ensure_tool("psql")
        _restore_zip(base, dump_path, target_db, conn_args, env)
    else:
        raise click.ClickException(f"Unsupported backup format: {backup_format}")


def _dump_suffix(path):
    """Return the normalized dump extension (e.g. .sql.gz, .zip, .dump)."""
    name = path.name
    if name.endswith(".sql.gz"):
        return ".sql.gz"
    return path.suffix


def _restore_sql_gz(dump_path, target_db, conn_args, env):
    """Stream a gzipped SQL dump into psql."""
    run_shell_pipeline(
        [
            ["gzip", "-cd", str(dump_path)],
            ["psql", "-d", target_db, *conn_args],
        ],
        env=env,
        error_msg="psql failed",
        not_found_msg="Could not locate `psql` or `gunzip`.",
    )


def _restore_zip(base, dump_path, target_db, conn_args, env):
    """Restore an Odoo backup zip (dump.sql + filestore/)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(dump_path, "r") as zf:
            zf.extractall(tmp_path)

        dump_sql = tmp_path / "dump.sql"
        if not dump_sql.exists():
            raise click.ClickException("Backup zip does not contain dump.sql")

        args = ["psql", "-d", target_db, "-f", str(dump_sql), *conn_args]
        run_subprocess(args, env=env, error_msg="psql failed")

        filestore_src = tmp_path / "filestore"
        if filestore_src.exists():
            data_dir = get_odoo_data_dir(base)
            if data_dir is None:
                echo.warning(
                    "could not determine Odoo data_dir; filestore not restored."
                )
                return
            filestore_dst = data_dir / "filestore" / target_db
            if filestore_dst.exists():
                shutil.rmtree(filestore_dst)
            shutil.copytree(filestore_src, filestore_dst)
            echo.info(f"Restored filestore to {filestore_dst}", err=True)
