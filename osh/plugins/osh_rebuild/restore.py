"""Database restore helpers for `osh rebuild`."""

from __future__ import annotations

import gzip
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import click

from ...db import _create_db, _drop_db, _get_pg_credentials
from ...utils import _get_odoo_config_path


def _tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def _ensure_tool(name: str) -> None:
    if not _tool_available(name):
        raise click.ClickException(f"Required tool '{name}' is not available on PATH.")


def _restore_dump(
    base: Path, dump_path: Path, target_db: str, *, dry_run: bool = False
) -> None:
    """Restore *dump_path* into a freshly created *target_db*."""
    suffix = _dump_suffix(dump_path)
    conn_args, env = _get_pg_credentials(base)

    if dry_run:
        click.echo(
            f"Would drop/create database '{target_db}' and restore {dump_path}",
            err=True,
        )
        return

    _drop_db(base, target_db)
    _create_db(base, target_db)

    if suffix == ".dump":
        _ensure_tool("pg_restore")
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
        _ensure_tool("psql")
        args = ["psql", "-d", target_db, "-f", str(dump_path), *conn_args]
        _run(args, env, "psql")
    elif suffix == ".sql.gz":
        _ensure_tool("gunzip")
        _ensure_tool("psql")
        _restore_sql_gz(dump_path, target_db, conn_args, env)
    elif suffix == ".zip":
        _ensure_tool("psql")
        _restore_zip(base, dump_path, target_db, conn_args, env)
    else:
        raise click.ClickException(f"Unsupported backup format: {suffix}")


def _dump_suffix(path: Path) -> str:
    """Return the normalized dump extension (e.g. .sql.gz, .zip, .dump)."""
    name = path.name
    if name.endswith(".sql.gz"):
        return ".sql.gz"
    return path.suffix


def _run(args: list[str], env: dict[str, str], label: str) -> None:
    try:
        subprocess.run(args, env=env, check=True, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        raise click.ClickException(f"{label} failed: {stderr}") from exc
    except FileNotFoundError as exc:
        raise click.ClickException(f"Could not locate `{label}`.") from exc


def _restore_sql_gz(
    dump_path: Path,
    target_db: str,
    conn_args: list[str],
    env: dict[str, str],
) -> None:
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
    base: Path,
    dump_path: Path,
    target_db: str,
    conn_args: list[str],
    env: dict[str, str],
) -> None:
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
            data_dir = _data_dir(base)
            if data_dir is None:
                click.echo(
                    "Warning: could not determine Odoo data_dir; filestore not restored.",
                    err=True,
                )
                return
            filestore_dst = data_dir / "filestore" / target_db
            if filestore_dst.exists():
                shutil.rmtree(filestore_dst)
            shutil.copytree(filestore_src, filestore_dst)
            click.echo(f"Restored filestore to {filestore_dst}", err=True)


def _data_dir(base: Path) -> Path | None:
    """Return the Odoo data directory from .odoorc or the default location."""
    odoo_rc = _get_odoo_config_path(base)
    if odoo_rc.exists():
        import configparser

        cfg = configparser.ConfigParser()
        cfg.read(odoo_rc)
        value = cfg.get("options", "data_dir", fallback=None)
        if value:
            return Path(value)
    default = Path.home() / ".local" / "share" / "Odoo"
    return default if default.exists() else None
