"""Database configuration and PostgreSQL helpers for Osh.

Tracks the preferred database per git branch in `.osh/config.toml` and provides
shared helpers for running PostgreSQL CLI tools with credentials from `.odoorc`.
"""

import configparser
import fnmatch
import re
import shlex
import sys
import tarfile
import tempfile
from pathlib import Path

import click

from . import config as _config
from . import echo
from .common import (
    decode_stderr,
    find_project_repos,
    get_odoo_config_path,
    get_osh_odoo_config_path,
    git_current_branch,
)


def sanitize_db_name(name):
    """Return a name that is safe for PostgreSQL and Odoo's --db-filter."""
    name = (name or "").strip().lower()
    name = re.sub(r"[^a-z0-9_]+", "-", name)
    name = name.strip("-")
    return name or "db"


def _require_db_name(name):
    """Return a sanitized database name or raise if one is not given."""
    if not name or not str(name).strip():
        raise click.ClickException("A database name is required.")
    name = sanitize_db_name(name)
    if name == "auto":
        # ``auto`` is rejected so legacy/hand-written configs can be told
        # apart from real database names when resolving.
        raise click.ClickException("'auto' is a reserved database name.")
    return name


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


def unset_project_config(base, section, option):
    """Remove *option* from *section* in ``.osh/config`` if it exists."""
    _config.unset_project_config(base, section, option)


def get_current_branch(base):
    """Return the current git branch, or None if it cannot be determined.

    When the project root is not a git repository itself, the repositories
    discovered below it are considered: the branch name is returned when
    every repository is on the same branch, and ``None`` when they disagree
    or no repository is found.
    """
    branches = {
        branch
        for repo in find_project_repos(base)
        if (branch := git_current_branch(repo))
    }
    return branches.pop() if len(branches) == 1 else None


def get_active_env(base):
    """Return the active environment name of a git-less project, or None.

    Stored in ``.osh/local.toml`` — per-machine state, not the shared
    project config — by ``osh switch`` when there is no git repository.
    """
    return _config.get_local_config(base, "env", "active")


def set_active_env(base, name):
    """Record *name* as the active environment of a git-less project."""
    _config.set_local_config(base, "env", "active", name)


def resolve_branch(base, branch):
    """Return *branch*, or the current branch/environment, or ``default``.

    The environment is the git branch when inside a repository, or the
    git-less active environment name recorded by ``osh switch``.
    """
    if branch is not None:
        return branch
    return get_current_branch(base) or get_active_env(base) or "default"


def get_pg_env(base):
    """Return PostgreSQL connection variables from the Odoo config as a dict.

    Reads ``.osh/odoo.conf`` if it exists, otherwise falls back to ``.odoorc``.
    Maps ``db_host``, ``db_port``, ``db_user`` and ``db_password`` to the
    standard ``PGHOST``, ``PGPORT``, ``PGUSER`` and ``PGPASSWORD`` environment
    variables so tools like ``psql`` and ``pg_restore`` connect automatically
    to the same database Odoo uses.
    """
    odoo_rc = get_osh_odoo_config_path(base)
    if not odoo_rc.exists():
        odoo_rc = get_odoo_config_path(base)
    env = {}
    if not odoo_rc.exists():
        return env

    cfg = configparser.ConfigParser()
    cfg.read(odoo_rc, encoding="utf-8")
    if not cfg.has_section("options"):
        return env

    mapping = {
        "db_host": "PGHOST",
        "db_port": "PGPORT",
        "db_user": "PGUSER",
        "db_password": "PGPASSWORD",
    }
    options = cfg["options"]
    for key, var in mapping.items():
        value = options.get(key)
        if value:
            env[var] = value
    return env


def normalize_backend_name(name):
    """Return *name*, mapping the legacy ``local`` backend name to ``none``."""
    return "none" if name == "local" else name


def get_active_backend_name(base, default="none"):
    """Return the project's active backend name (``run.target`` config)."""
    return normalize_backend_name(
        get_project_config(base, "run", "target", fallback=default)
    )


def deactivate_backend(base):
    """Record ``none`` as the active backend; return the previous name.

    Returns ``None`` when no managed backend was active. Backend plugins can
    call this from their own deactivate command to keep the run.target
    bookkeeping in one place.
    """
    previous = get_active_backend_name(base, default=None)
    if not previous or previous == "none":
        return None
    set_project_config(base, "run", "target", "none")
    return previous


def resolve_backend(base, default="none"):
    """Instantiate the backend configured for *base*.

    The active backend is the ``run.target`` recorded in the project config
    by ``osh <backend> init`` or ``osh <backend> activate``, falling back to
    *default*. This is the supported way for commands and plugins to obtain
    the active backend instance.
    """
    from .utils.plugin_loader import load_backends

    target = get_active_backend_name(base, default=default)
    backend_cls = load_backends().get(target)
    if backend_cls is None:
        raise click.ClickException(f"Unknown backend: {target}")
    return backend_cls()


def run_in_backend(
    ctx,
    base,
    argv,
    env=None,
    dry_run=False,
    *,
    capture=True,
    input=None,
    stdin=None,
    stdout=None,
    text=True,
):
    """Run *argv* inside the active backend's environment.

    PostgreSQL connection variables from the project Odoo config are merged
    into the command environment, so ``psql``/``createdb``/... connect the
    same way Odoo itself does, in the same execution context Odoo runs in.
    These helpers deliberately know nothing about which backend is active.

    This is the supported public API for plugins that need to run commands
    in the project's execution context (e.g. database tooling).

    With ``capture=True`` (the default) returns ``(returncode, stdout,
    stderr)``; a missing executable reports ``returncode=None``. With
    ``capture=False`` the command's output is streamed and a non-zero exit
    raises ``click.ClickException``.
    """
    from .backends import EnvSpec

    backend = resolve_backend(base)
    merged = {**get_pg_env(base), **(env or {})}
    env_spec = EnvSpec(
        argv=[str(a) for a in argv], env=merged, input=input, stdin=stdin
    )
    result = backend.env(
        ctx,
        base,
        env_spec,
        dry_run=dry_run,
        wait=True,
        capture=capture,
        stdout=stdout,
        text=text,
    )
    # ``None`` comes back from dry-run and streamed (capture=False) runs; a
    # streamed failure already raised inside the backend.
    if result is None:
        return 0, "", ""
    return result


def install_filestore(ctx, base, src_dir, db_name):
    """Install the contents of *src_dir* as the filestore of *db_name*.

    The directory contents are streamed as a tar through stdin, so this works
    identically on host and container backends — the destination may live in
    a container volume unreachable from the host.
    """
    data_dir = resolve_backend(base).odoo_data_dir(base)
    if data_dir is None:
        echo.warning("could not determine Odoo data_dir; filestore not installed.")
        return
    dest_path = f"{data_dir}/filestore/{db_name}"
    dest = shlex.quote(dest_path)
    script = f"rm -rf {dest} && mkdir -p {dest} && tar -xf - -C {dest}"
    with tempfile.NamedTemporaryFile(suffix=".tar") as tar_file:
        with tarfile.open(fileobj=tar_file, mode="w") as tar:
            for item in sorted(Path(src_dir).rglob("*")):
                tar.add(item, arcname=item.relative_to(src_dir).as_posix())
        tar_file.flush()
        tar_file.seek(0)
        returncode, _, stderr = run_in_backend(
            ctx, base, ["sh", "-c", script], stdin=tar_file
        )
    if returncode is None:
        raise RuntimeError("Could not locate `sh`/`tar` in the backend environment.")
    if returncode != 0:
        raise RuntimeError(f"Failed to install filestore for '{db_name}': {stderr}")
    echo.info(f"Installed filestore for '{db_name}' at {dest_path}", err=True)


def export_filestore(ctx, base, db_name, dest_dir):
    """Export the filestore of *db_name* into host directory *dest_dir*.

    Streams a tar out of the backend environment, so the source may live in a
    container volume unreachable from the host. Returns False when the data
    dir or the database filestore cannot be found.
    """
    data_dir = resolve_backend(base).odoo_data_dir(base)
    if data_dir is None:
        return False
    src = f"{data_dir}/filestore/{db_name}"
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar") as tar_file:
        returncode, _, _ = run_in_backend(
            ctx,
            base,
            ["tar", "-c", "-C", src, "."],
            stdout=tar_file,
            text=False,
        )
        if returncode != 0:
            return False
        tar_file.flush()
        tar_file.seek(0)
        with tarfile.open(fileobj=tar_file) as tar:
            for member in tar.getmembers():
                member_path = Path(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    continue
                if member.name in ("", "."):
                    continue
                tar.extract(member, dest_dir)
    return True


def remove_filestore(ctx, base, db_name):
    """Remove the filestore directory of *db_name* inside the backend.

    Prints the removed path when a filestore directory existed. Best
    effort: warns and returns when the data dir cannot be determined.
    On container backends the removal runs inside the container, where the
    data dir volume is mounted.
    """
    path = _filestore_path(base, db_name)
    if path is None:
        echo.warning("could not determine Odoo data_dir; filestore not removed.")
        return
    if not filestore_exists(ctx, base, db_name):
        return
    run_in_backend(ctx, base, ["rm", "-rf", path])
    echo.info(f"Removed filestore for '{db_name}' at {path}", err=True)


def filestore_exists(ctx, base, db_name):
    """Return True when *db_name* has a filestore directory."""
    path = _filestore_path(base, db_name)
    if path is None:
        return False
    returncode, _, _ = run_in_backend(ctx, base, ["test", "-d", path])
    return returncode == 0


def _filestore_path(base, db_name):
    """Return the filestore path for *db_name* inside the backend, or None."""
    data_dir = resolve_backend(base).odoo_data_dir(base)
    if data_dir is None:
        return None
    return f"{data_dir}/filestore/{db_name}"


def list_filestore_dirs(ctx, base):
    """Return filestore directory names inside the backend env, or [].

    Returns an empty list when the data dir cannot be determined or the
    filestore directory does not exist.
    """
    data_dir = resolve_backend(base).odoo_data_dir(base)
    if data_dir is None:
        return []
    returncode, stdout, _ = run_in_backend(
        ctx, base, ["ls", "-1", f"{data_dir}/filestore"]
    )
    if returncode != 0 or not stdout:
        return []
    return [line.strip() for line in stdout.splitlines() if line.strip()]


def db_exists(base, db_name, ctx=None, *, dry_run=False):
    """Return True if the PostgreSQL database exists.

    In *dry_run* mode the probe still runs where it is side-effect free
    (host backend); backends where probing would start a container report
    "does not exist" unless the stack is already up.
    """
    returncode, _, _ = run_in_backend(
        ctx, base, ["psql", "-d", db_name, "-c", "SELECT 1"], dry_run=dry_run
    )
    # A failed connection means the database does not exist (or psql is
    # missing); in either case the database is not usable here.
    return returncode == 0


def drop_db(base, db_name, ctx=None):
    """Drop a PostgreSQL database if it exists."""
    # `dropdb` is expected to fail when the database does not exist; the
    # result is ignored so callers can call this defensively.
    run_in_backend(ctx, base, ["dropdb", db_name])


def create_db(base, db_name, ctx=None):
    """Create a fresh PostgreSQL database."""
    returncode, _, stderr = run_in_backend(ctx, base, ["createdb", db_name], text=False)
    if returncode is None:
        raise RuntimeError("Could not locate `createdb`. Is PostgreSQL installed?")
    if returncode != 0:
        raise RuntimeError(
            f"Could not create database '{db_name}': {decode_stderr(stderr)}"
        )


def copy_db(base, from_db, to_db, ctx=None):
    """Copy *from_db* to *to_db*, replacing the target if it exists."""
    drop_db(base, to_db, ctx=ctx)
    create_db(base, to_db, ctx=ctx)
    pipeline = (
        f"pg_dump -Fc {shlex.quote(from_db)}"
        f" | pg_restore --no-owner -d {shlex.quote(to_db)}"
    )
    returncode, _, stderr = run_in_backend(ctx, base, ["sh", "-c", pipeline])
    if returncode is None:
        raise RuntimeError(
            "Could not locate `pg_dump` or `pg_restore`. Is PostgreSQL installed?"
        )
    if returncode != 0:
        raise RuntimeError(
            f"Could not copy database '{from_db}' to '{to_db}': {stderr}"
        )


def run_psql_script(base, db_name, script_path, ctx=None):
    """Execute a SQL script against *db_name* using psql."""
    returncode, _, stderr = run_in_backend(
        ctx,
        base,
        ["psql", "-d", db_name],
        input=script_path.read_text(encoding="utf-8"),
    )
    if returncode is None:
        raise RuntimeError("Could not locate `psql`. Is PostgreSQL installed?")
    if returncode != 0:
        raise RuntimeError(
            f"Failed to run SQL script on '{db_name}': {decode_stderr(stderr)}"
        )


def resolve_db_name(base, verbose=False, branch=None):
    """Resolve the database name for the current context.

    Returns the configured database for the current branch, a database matching
    a glob pattern, the configured default, or a generated ``<project>-<branch>``
    name. There is no global "last used" fallback.
    """
    branch = resolve_branch(base, branch)
    db_name = _resolve_config_db_name(base, branch)
    if db_name is None:
        db_name = _branch_db_name(base, branch)
    if verbose:
        echo.info(f"Using database: {db_name}", err=True)
    return db_name


def get_last_db(base):
    """Return the last used database name recorded for the project, or None.

    The legacy ``last`` key written by older Osh versions is honoured so
    upgraded projects keep their last-used record.
    """
    return get_project_config(base, "db", "last_db") or get_project_config(
        base, "db", "last"
    )


def set_last_db(base, db_name):
    """Record *db_name* as the most recently used database."""
    if not db_name:
        return
    cfg = load_osh_config(base)
    cfg.set("db", "last_db", db_name)
    # Migrate: drop the pre-rename key written by older versions.
    cfg.remove("db", "last")
    save_osh_config(base, cfg)


def resolve_db_name_for_run(base, verbose=False, ctx=None, dry_run=False):
    """Resolve the database name for the current context, prompting if missing.

    If the branch's resolved database does not exist, prompt in an interactive
    terminal to reuse the last used database or create it. In non-interactive
    mode raise a ``click.ClickException`` with instructions. In *dry_run* mode
    the existence probe only runs where it is side-effect free.
    """
    branch = resolve_branch(base, None)
    db_name = resolve_db_name(base, verbose=False, branch=branch)
    if db_exists(base, db_name, ctx=ctx, dry_run=dry_run):
        if verbose:
            echo.info(f"Using database: {db_name}", err=True)
        return db_name

    last_db = get_last_db(base)
    if last_db == db_name:
        last_db = None

    if sys.stdin.isatty():
        action, name = _prompt_for_missing_db(base, branch, db_name, last_db, ctx=ctx)
        if action == "create":
            create_db(base, name, ctx=ctx)
        return name
    _raise_missing_db_error(base, branch, db_name, last_db)


def _prompt_for_missing_db(base, branch, db_name, last_db, ctx=None):
    """Prompt the user when the branch's database is missing.

    Returns ``("use", last_db)`` when the last used database is picked — the
    branch is re-mapped to it — or ``("create", db_name)``. The database
    itself is left for the caller (or Odoo) to create. ``[a] Abort`` raises
    ``click.Abort``.
    """
    last_db_exists = bool(last_db) and db_exists(base, last_db, ctx=ctx)
    options = []
    if last_db_exists:
        options.append(("u", f"Use the last used database '{last_db}'"))
    options.append(("c", f"Create new database '{db_name}'"))
    options.append(("a", "Abort"))

    echo.warning(f"Database '{db_name}' does not exist.")
    for num, (key, label) in enumerate(options):
        default_marker = "  [default]" if num == 0 else ""
        echo.info(f"  [{key}] {label}{default_marker}")

    keys = [key for key, _ in options]
    choice = click.prompt(
        "Choice",
        type=click.Choice(keys, case_sensitive=False),
        default=keys[0],
        show_choices=False,
    ).lower()

    if choice == "a":
        raise click.Abort()
    if choice == "u":
        set_project_config(base, "db", branch, last_db)
        return "use", last_db
    return "create", db_name


def _raise_missing_db_error(base, branch, db_name, last_db):
    """Raise a clear error when the branch's database is missing in non-TTY."""
    lines = [
        f"Database '{db_name}' does not exist.",
        "Use one of:",
        f"  osh db set <db> --branch {branch}",
        f"  osh db copy {last_db or '<from>'} {db_name}",
        "  osh db restore <backup>",
        "  osh odoo -d <db>",
    ]
    raise click.ClickException("\n".join(lines))


def _resolve_config_db_name(base, branch):
    """Return the configured database for *branch*, or None if unconfigured.

    Values are sanitized to keep the resolved name safe for PostgreSQL
    and Odoo's ``--db-filter``.
    """
    cfg = load_osh_config(base)
    if not cfg.has_section("db"):
        return None

    entry = _find_db_entry(cfg, branch)
    if entry is None:
        return None

    key, value = entry
    if not isinstance(value, str) or not value.strip():
        raise click.ClickException(
            f"Invalid database name for '{key}' in the [db] section of "
            f".osh/config.toml: {value!r}. Use a database name."
        )
    if value.strip().lower() == "auto":
        raise click.ClickException(
            f"The 'auto' marker for '{key}' in the [db] section of "
            ".osh/config.toml is no longer supported — an unmapped branch "
            f"already falls back to the generated name. Remove it with: "
            f"osh db unset --branch {key}"
        )
    return sanitize_db_name(value)


def _find_db_entry(cfg, branch):
    """Return the ``(key, value)`` mapping in ``[db]`` that applies to *branch*.

    Priority: exact branch name, then the longest matching glob pattern, then
    the ``default`` key. Returns None when nothing matches.
    """
    if cfg.has_option("db", branch):
        return branch, cfg.get("db", branch)

    matches = [
        (key, value)
        for key, value in cfg.items("db")
        if key != "default" and fnmatch.fnmatchcase(branch, key)
    ]
    if matches:
        return max(matches, key=lambda item: len(item[0]))

    if cfg.has_option("db", "default"):
        return "default", cfg.get("db", "default")

    return None


def _branch_db_name(base, branch):
    """Return the auto-generated database name for a git branch."""
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
    branch = resolve_branch(base, None)
    return sanitize_db_name(f"{base.name}-{branch}-test")


def get_database_version(base, db_name, ctx=None):
    """Return the installed Odoo version of *db_name* as a (major, minor) tuple.

    This reads the ``latest_version`` of the ``base`` module, which tracks the
    Odoo version used when the database was installed/last updated.
    """
    returncode, stdout, _ = run_in_backend(
        ctx,
        base,
        [
            "psql",
            "-d",
            db_name,
            "-t",
            "-A",
            "-c",
            "SELECT latest_version FROM ir_module_module WHERE name = 'base'",
        ],
    )
    if returncode != 0 or not stdout:
        return None
    match = re.search(r"(\d+)\.(\d+)", stdout.strip().splitlines()[0])
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)))
