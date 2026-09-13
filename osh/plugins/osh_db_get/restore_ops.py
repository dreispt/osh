"""Backup restore internals for ``osh db restore``.

These helpers resolve a backup file from the project cache or a path, pick
the right restore tool for its format, restore the dump, copy the filestore
for ``.zip`` backups, and run neutralization SQL scripts.
"""

import gzip
import importlib.resources
import shutil
import tempfile
import zipfile
from pathlib import Path

import click

from ... import echo
from ...common import get_odoo_data_dir
from ...db import restore_cache_dir, run_in_backend, run_psql_script, stage_under_base
from .cache import get_cache_dir, list_cache, read_metadata, resolve_cache_id
from .format_detect import detect_backup_format_by_content

SOURCE_COLUMN_WIDTH = 40
SOURCE_TRUNCATE_AT = SOURCE_COLUMN_WIDTH - len("...")


def resolve_backup_path(base, dump):
    """Resolve a dump argument to an existing file path."""
    cache_dir = get_cache_dir(base)

    if dump is None:
        entries = list_cache(base, limit=1)
        if not entries:
            raise click.ClickException(
                "No cached backup found. Run 'osh db get <source>' first."
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


def list_cached_backups(base, *, limit, reverse):
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


def restore_dump(base, dump_path, target_db, *, dry_run=False, ctx=None):
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

    if dry_run:
        echo.info(
            f"Would restore database '{target_db}' from {dump_path} "
            f"(format: {backup_format})",
            err=True,
        )
        return

    echo.info(
        f"Restoring database '{target_db}' from {dump_path} (format: {backup_format})",
        err=True,
    )

    # Stage the dump under the project root so container backends can see it.
    dump_path = stage_under_base(base, dump_path)

    if backup_format == "dump":
        _run_db_tool(
            ctx,
            base,
            [
                "pg_restore",
                "--verbose",
                "--no-owner",
                "--dbname",
                target_db,
                str(dump_path),
            ],
            "pg_restore failed",
        )
    elif backup_format == "sql":
        _run_db_tool(
            ctx, base, ["psql", "-d", target_db, "-f", str(dump_path)], "psql failed"
        )
    elif backup_format == "sql.gz":
        _restore_sql_gz(ctx, base, dump_path, target_db)
    elif backup_format == "zip":
        _restore_zip(ctx, base, dump_path, target_db)
    else:
        raise click.ClickException(f"Unsupported backup format: {backup_format}")


def neutralize_with_sql(base, db_name, ctx=None):
    """Run the bundled SQL fallback neutralization script."""
    with importlib.resources.path("osh.data", "neutralize_fallback.sql") as script_path:
        run_psql_script(base, db_name, script_path, ctx=ctx)


def run_project_neutralize_scripts(base, db_name, *, dry_run=False, ctx=None):
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
            run_psql_script(base, db_name, script, ctx=ctx)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc


def _run_db_tool(ctx, base, argv, error_msg):
    """Run a database CLI tool inside the active backend environment."""
    returncode, _, stderr = run_in_backend(ctx, base, argv)
    if returncode is None:
        raise click.ClickException(f"{error_msg}: command not found")
    if returncode != 0:
        raise click.ClickException(f"{error_msg}: {stderr}")


def _dump_suffix(path):
    """Return the normalized dump extension (e.g. .sql.gz, .zip, .dump)."""
    name = path.name
    if name.endswith(".sql.gz"):
        return ".sql.gz"
    return path.suffix


def _restore_sql_gz(ctx, base, dump_path, target_db):
    """Decompress a gzipped SQL dump under the project, then restore it."""
    name = dump_path.name
    sql_path = restore_cache_dir(base) / (name[:-3] if name.endswith(".gz") else name)
    with gzip.open(dump_path, "rb") as src, sql_path.open("wb") as out:
        shutil.copyfileobj(src, out)
    _run_db_tool(
        ctx, base, ["psql", "-d", target_db, "-f", str(sql_path)], "psql failed"
    )


def _restore_zip(ctx, base, dump_path, target_db):
    """Restore an Odoo backup zip (dump.sql + filestore/)."""
    # Extract under the project so container backends can read dump.sql.
    with tempfile.TemporaryDirectory(dir=restore_cache_dir(base)) as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(dump_path, "r") as zf:
            zf.extractall(tmp_path)

        dump_sql = tmp_path / "dump.sql"
        if not dump_sql.exists():
            raise click.ClickException("Backup zip does not contain dump.sql")

        _run_db_tool(
            ctx, base, ["psql", "-d", target_db, "-f", str(dump_sql)], "psql failed"
        )

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
