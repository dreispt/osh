"""Database configuration and PostgreSQL helpers for Osh.

Tracks the preferred database per git branch in `.osh/config` and provides
shared helpers for running PostgreSQL CLI tools with credentials from `.odoorc`.
"""

from __future__ import annotations

import configparser
import os
import re
import subprocess
from pathlib import Path


def _sanitize_db_name(name: str) -> str:
    """Return a name that is safe for PostgreSQL and Odoo's --db-filter."""
    name = name.lower()
    name = re.sub(r"[^a-z0-9_]+", "-", name)
    name = name.strip("-")
    return name or "db"


def _get_osh_config_path(base: Path) -> Path:
    """Return path to the Osh project configuration file."""
    return base / ".osh" / "config"


def _load_osh_config(base: Path) -> configparser.ConfigParser:
    """Load or create an Osh project configuration."""
    cfg = configparser.ConfigParser()
    cfg.add_section("db")
    config_path = _get_osh_config_path(base)
    if config_path.exists():
        cfg.read(config_path)
    if not cfg.has_section("db"):
        cfg.add_section("db")
    return cfg


def _save_osh_config(base: Path, cfg: configparser.ConfigParser) -> None:
    """Write the Osh project configuration file."""
    config_path = _get_osh_config_path(base)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w") as f:
        cfg.write(f)


def _get_branch_db(base: Path, branch: str) -> str | None:
    """Return the configured database for *branch*, or None."""
    cfg = _load_osh_config(base)
    return cfg.get("db", branch, fallback=None)


def _set_branch_db(base: Path, branch: str, db_name: str) -> None:
    """Store the preferred database for *branch*."""
    cfg = _load_osh_config(base)
    cfg.set("db", branch, db_name)
    _save_osh_config(base, cfg)


def _get_last_db(base: Path) -> str | None:
    """Return the last used database, or None."""
    cfg = _load_osh_config(base)
    return cfg.get("db", "last", fallback=None)


def _set_last_db(base: Path, db_name: str) -> None:
    """Store the last used database."""
    cfg = _load_osh_config(base)
    cfg.set("db", "last", db_name)
    _save_osh_config(base, cfg)


def _get_current_branch(base: Path) -> str | None:
    """Return the current git branch, or None if not in a git repo."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=base,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _get_odoo_config_path(base: Path) -> Path:
    """Return path to the Odoo configuration file (.odoorc) in the project root."""
    return base / ".odoorc"


def _get_pg_credentials(base: Path) -> tuple[list[str], dict[str, str]]:
    """Return PostgreSQL connection args and an environment dict.

    The returned tuple is ``(args, env)`` where ``args`` can be inserted
    after any ``psql``/``pg_dump``/``pg_restore``/``dropdb``/``createdb``
    command and before the database-specific arguments. ``env`` contains
    ``PGPASSWORD`` when a password is configured.
    """
    odoo_rc = _get_odoo_config_path(base)
    args: list[str] = []
    env: dict[str, str] = dict(os.environ)

    if not odoo_rc.exists():
        return args, env

    cfg = configparser.ConfigParser()
    cfg.read(odoo_rc)
    if not cfg.has_section("options"):
        return args, env

    options = cfg["options"]
    for key, arg in [
        ("db_host", "--host"),
        ("db_port", "--port"),
        ("db_user", "--username"),
    ]:
        value = options.get(key, fallback=None)
        if value:
            args.extend([arg, value])

    password = options.get("db_password", fallback=None)
    if password:
        env["PGPASSWORD"] = password

    return args, env


def _db_exists(base: Path, db_name: str) -> bool:
    """Return True if the PostgreSQL database exists."""
    conn_args, env = _get_pg_credentials(base)
    pg_args = ["psql", "-d", db_name, "-c", "SELECT 1", *conn_args]
    try:
        subprocess.run(
            pg_args,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        # A failed connection means the database does not exist (or psql is
        # missing); in either case the database is not usable here.
        return False


def _drop_db(base: Path, db_name: str) -> None:
    """Drop a PostgreSQL database if it exists."""
    conn_args, env = _get_pg_credentials(base)
    drop_args = ["dropdb", *conn_args, db_name]
    # `dropdb` is expected to fail when the database does not exist. Use
    # `check=False` so callers can call this defensively without handling an
    # error for the common "database is already gone" case.
    try:
        subprocess.run(
            drop_args,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError:
        # The `dropdb` binary itself is missing; the caller will discover this
        # later when it tries to create or restore a database.
        pass


def _create_db(base: Path, db_name: str) -> None:
    """Create a fresh PostgreSQL database."""
    conn_args, env = _get_pg_credentials(base)
    create_args = ["createdb", *conn_args, db_name]
    try:
        subprocess.run(
            create_args,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        raise RuntimeError(f"Could not create database '{db_name}': {stderr}") from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Could not locate `createdb`. Is PostgreSQL installed?"
        ) from exc


def _run_psql_script(base: Path, db_name: str, script_path: Path) -> None:
    """Execute a SQL script against *db_name* using psql."""
    conn_args, env = _get_pg_credentials(base)
    psql_args = ["psql", "-d", db_name, "-f", str(script_path), *conn_args]
    try:
        subprocess.run(
            psql_args,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        raise RuntimeError(
            f"Failed to run SQL script on '{db_name}': {stderr}"
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError("Could not locate `psql`. Is PostgreSQL installed?") from exc
