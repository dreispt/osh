"""Database configuration and PostgreSQL helpers for Osh.

Tracks the preferred database per git branch in `.osh/config.toml` and provides
shared helpers for running PostgreSQL CLI tools with credentials from `.odoorc`.
"""

import configparser
import importlib.resources
import os
import re
import subprocess

import click

from . import config as _config
from .commons import decode_stderr, get_odoo_config_path, resolve_config_file
from .echo import Echo
from .odoo_layout import build_addons_paths
from .version import get_version_from_executable


def sanitize_db_name(name):
    """Return a name that is safe for PostgreSQL and Odoo's --db-filter."""
    name = name.lower()
    name = re.sub(r"[^a-z0-9_]+", "-", name)
    name = name.strip("-")
    return name or "db"


def load_osh_config(base):
    """Load or create an Osh project configuration."""
    return _config.load_project_config(base)


def save_osh_config(base, cfg):
    """Write the Osh project configuration file."""
    _config.save_project_config(base, cfg)


def get_project_config(base, section, option, fallback=None):
    """Return a value from ``.osh/config``, or *fallback* if it is missing."""
    return _config.get_project_config(base, section, option, fallback)


def set_project_config(
    base,
    section,
    option=None,
    value=None,
    *,
    values=None,
):
    """Set one or more values in ``.osh/config``, creating the section if absent."""
    _config.set_project_config(base, section, option, value, values=values)


def resolve_run_target(base, default_target, ctx):
    """Resolve the effective run/init target, remembering explicit choices.

    Explicit ``--target`` or the corresponding env var take precedence. Otherwise
    the last used target from ``.osh/config`` is reused, falling back to
    *default_target*.
    """
    source = ctx.get_parameter_source("backend_name")
    if source in (
        click.core.ParameterSource.COMMANDLINE,
        click.core.ParameterSource.ENVIRONMENT,
    ):
        return default_target

    return get_project_config(base, "run", "target", fallback=default_target)


def get_current_branch(base):
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


def get_pg_credentials(base):
    """Return PostgreSQL connection args and an environment dict.

    The returned tuple is ``(args, env)`` where ``args`` can be inserted
    after any ``psql``/``pg_dump``/``pg_restore``/``dropdb``/``createdb``
    command and before the database-specific arguments. ``env`` contains
    ``PGPASSWORD`` when a password is configured.
    """
    odoo_rc = get_odoo_config_path(base)
    args = []
    env = dict(os.environ)

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


def db_exists(base, db_name):
    """Return True if the PostgreSQL database exists."""
    conn_args, env = get_pg_credentials(base)
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


def drop_db(base, db_name):
    """Drop a PostgreSQL database if it exists."""
    conn_args, env = get_pg_credentials(base)
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


def create_db(base, db_name):
    """Create a fresh PostgreSQL database."""
    conn_args, env = get_pg_credentials(base)
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
        stderr = decode_stderr(exc.stderr)
        raise RuntimeError(f"Could not create database '{db_name}': {stderr}") from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Could not locate `createdb`. Is PostgreSQL installed?"
        ) from exc


def run_psql_script(base, db_name, script_path):
    """Execute a SQL script against *db_name* using psql."""
    conn_args, env = get_pg_credentials(base)
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
        stderr = decode_stderr(exc.stderr)
        raise RuntimeError(
            f"Failed to run SQL script on '{db_name}': {stderr}"
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError("Could not locate `psql`. Is PostgreSQL installed?") from exc


def resolve_db_name(base, verbose=False):
    """Resolve the database name for the current context.

    Returns the configured database for the current branch, or the last used
    database, or a sanitized ``<project>-<branch>`` default so runs on both
    local and Docker targets consistently use the branch name.
    """
    branch = get_current_branch(base) or "default"
    db_name = get_project_config(base, "db", branch)
    if db_name:
        return db_name

    # Fall back to last used database
    last_db = get_project_config(base, "db", "last")
    if last_db:
        if verbose:
            echo = Echo(level="normal", emoji=True)
            echo.info(f"Using last database: {last_db}", err=True)
        return last_db

    return sanitize_db_name(f"{base.name}-{branch}")


def resolve_test_db_name(base, current_db, test_db):
    """Return the test database name to use.

    If *test_db* is provided, it wins. If *current_db* is True, the current
    branch's configured database is used. Otherwise the default
    ``<project>-<branch>-test`` name is returned.
    """
    if test_db:
        return sanitize_db_name(test_db)
    if current_db:
        current = resolve_db_name(base, verbose=False)
        if current:
            return current
    branch = get_current_branch(base) or "default"
    return sanitize_db_name(f"{base.name}-{branch}-test")


_MIN_NEUTRALIZE_VERSION = (16, 0)


def neutralize_database(
    base,
    exe,
    db_name,
    *,
    dry_run=False,
):
    """Neutralize *db_name* using the best available strategy."""
    if dry_run:
        echo = Echo(level="normal", emoji=True)
        echo.info(f"Would neutralize database '{db_name}'", err=True)
        return

    version = _get_odoo_version(exe)
    if version is not None and version >= _MIN_NEUTRALIZE_VERSION:
        _neutralize_with_odoo(base, exe, db_name)
    else:
        _neutralize_with_sql(base, db_name)


def _get_odoo_version(exe):
    """Return the installed Odoo version as a (major, minor) tuple, or None."""
    output = get_version_from_executable(exe)
    if not output:
        return None
    match = re.search(r"(\d+)\.(\d+)", output)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)))


def _neutralize_with_odoo(base, exe, db_name):
    """Run ``odoo-bin neutralize`` against the target database."""
    config_path = resolve_config_file(base, [])

    if config_path:
        args = [exe, f"--config={config_path}"]
    else:
        args = [exe]

    addons_paths = build_addons_paths(base)
    if addons_paths:
        unique_paths = sorted({str(p) for p in addons_paths})
        args.append(f"--addons-path={','.join(unique_paths)}")

    args.extend(["neutralize", "-d", db_name])
    try:
        subprocess.run(args, check=True, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        stderr = decode_stderr(exc.stderr)
        raise click.ClickException(
            f"Database restored but neutralization failed: {stderr}\n"
            f"Run `odoo-bin neutralize -d {db_name}` manually."
        ) from exc
    except FileNotFoundError as exc:
        raise click.ClickException("Could not locate Odoo executable.") from exc


def _neutralize_with_sql(base, db_name):
    """Apply the bundled fallback neutralization SQL script."""
    try:
        with importlib.resources.path(
            "osh.data", "neutralize_fallback.sql"
        ) as script_path:
            run_psql_script(base, db_name, script_path)
    except RuntimeError as exc:
        raise click.ClickException(
            f"Database restored but neutralization failed: {exc}\n"
            f"Run the fallback script manually with psql -d {db_name}."
        ) from exc
