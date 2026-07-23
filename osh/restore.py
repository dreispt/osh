"""Database restore helpers.

Restores database dumps (``.dump``, ``.sql``, ``.sql.gz``, ``.zip``) into a
fresh PostgreSQL database using credentials from ``.odoorc``. These helpers
are shared by the ``osh restore`` command and by backends that implement
``restore()``.
"""

import gzip
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import click

from . import echo
from .commons import decode_stderr, ensure_tool, get_odoo_data_dir, run_subprocess
from .db import create_db, drop_db, get_pg_credentials


def restore_dump(base, dump_path, target_db, *, dry_run=False):
    """Restore *dump_path* into a freshly created *target_db*."""
    from . import echo

    suffix = _dump_suffix(dump_path)
    conn_args, env = get_pg_credentials(base)

    if dry_run:
        echo.info(
            f"Would drop/create database '{target_db}' and restore {dump_path}",
            err=True,
        )
        return

    drop_db(base, target_db)
    create_db(base, target_db)

    if suffix == ".dump":
        ensure_tool("pg_restore")
        args = [
            "pg_restore",
            "--no-owner",
            "--dbname",
            target_db,
            *conn_args,
            str(dump_path),
        ]
        _run(args, env, "pg_restore")
    elif suffix == ".sql":
        ensure_tool("psql")
        args = ["psql", "-d", target_db, "-f", str(dump_path), *conn_args]
        _run(args, env, "psql")
    elif suffix == ".sql.gz":
        ensure_tool("gunzip")
        ensure_tool("psql")
        _restore_sql_gz(dump_path, target_db, conn_args, env)
    elif suffix == ".zip":
        ensure_tool("psql")
        _restore_zip(base, dump_path, target_db, conn_args, env)
    else:
        raise click.ClickException(f"Unsupported backup format: {suffix}")


def _dump_suffix(path):
    """Return the normalized dump extension (e.g. .sql.gz, .zip, .dump)."""
    name = path.name
    if name.endswith(".sql.gz"):
        return ".sql.gz"
    return path.suffix


def _run(args, env, label):
    returncode, _, stderr = run_subprocess(args, env=env, text=False)
    if returncode is None:
        raise click.ClickException(f"Could not locate `{label}`.")
    if returncode != 0:
        raise click.ClickException(f"{label} failed: {decode_stderr(stderr)}")


def _restore_sql_gz(
    dump_path,
    target_db,
    conn_args,
    env,
):
    """Stream a gzipped SQL dump into psql."""
    try:
        with gzip.open(dump_path, "rb") as gz, subprocess.Popen(
            ["psql", "-d", target_db, *conn_args],
            stdin=subprocess.PIPE,
            env=env,
            stderr=subprocess.PIPE,
        ) as proc:
            shutil.copyfileobj(gz, proc.stdin)  # type: ignore[union-attr]
            proc.stdin.close()  # type: ignore[union-attr]
            ret = proc.wait()
            if ret != 0:
                stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""  # type: ignore[union-attr]
                raise click.ClickException(f"psql failed: {stderr}")
    except FileNotFoundError as exc:
        raise click.ClickException("Could not locate `psql` or `gunzip`.") from exc


def _restore_zip(
    base,
    dump_path,
    target_db,
    conn_args,
    env,
):
    """Restore an Odoo backup zip (dump.sql + filestore/)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(dump_path, "r") as zf:
            zf.extractall(tmp_path)

        dump_sql = tmp_path / "dump.sql"
        if not dump_sql.exists():
            raise click.ClickException("Backup zip does not contain dump.sql")

        args = ["psql", "-d", target_db, "-f", str(dump_sql), *conn_args]
        _run(args, env, "psql")

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
