"""`osh db` command for managing databases and branch-to-database mappings."""

import click

from .. import echo
from ..backends import EnvSpec
from ..cli_utils import NaturalOrderGroup
from ..common import find_project_root
from ..db import (
    _require_db_name,
    copy_db,
    db_exists,
    resolve_backend,
    resolve_branch,
    resolve_db_name,
    run_in_backend,
    sanitize_db_name,
    set_project_config,
    unset_project_config,
)
from ..hooks import HOOK_DB_LIST_SECTIONS
from ..utils.plugin_loader import load_hooks
from .helpers import check_run_diagnostics
from .shell_cmd import parse_explicit_db, prepare_env_context


@click.group(name="db", cls=NaturalOrderGroup)
def db():  # noqa: D401
    """Manage databases and branch-to-database mappings.

    Each git branch can be mapped to a specific PostgreSQL database, or left
    unmapped to fall back to the generated ``<project>-<branch>`` name. Mappings
    are stored in ``.osh/config.toml`` under the ``[db]`` section.

    Branches are matched in this order:

    1. Exact branch name (e.g. ``main``).
    2. Longest matching glob pattern (e.g. ``feature/*``).
    3. The special ``default`` key.
    4. Generated ``<project>-<branch>`` if nothing is configured.

    Examples:

    \b
      osh db list
      osh db show
      osh db set myproject-main --branch main
      osh db copy myproject-main myproject-fix-123
      osh db shell
      osh db shell psql
      osh db unset
      osh db unset --branch feature/old-thing
    """


@db.command(name="show")
@click.pass_context
def show(ctx):  # noqa: D401
    """Show the database for the current branch.

    Prints the current git branch, the resolved database name, and whether the
    database already exists in PostgreSQL. This is a quick way to check what
    ``osh odoo`` would use before starting Odoo.
    """
    base = find_project_root(required=True)
    branch = resolve_branch(base, None)
    db_name = resolve_db_name(base, verbose=False)
    exists = db_exists(base, db_name, ctx=ctx)
    echo.info(f"Branch:   {branch}")
    echo.info(f"Database: {db_name}")
    echo.info(f"Exists:   {'yes' if exists else 'no'}")


@db.command(name="list")
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="List all databases, not only this project's.",
)
@click.pass_context
def list_dbs(ctx, show_all):  # noqa: D401
    """List PostgreSQL databases, filtered to this project by default.

    Runs ``psql -l`` inside the project's runtime environment — the same
    context ``osh shell`` provides — and keeps only databases whose name
    starts with the generated ``<project>-`` prefix. Use ``--all`` to list
    every database on the server.

    Examples:

    \b
      osh db list
      osh db list --all
    """
    base = find_project_root(required=True)
    returncode, stdout, stderr = run_in_backend(ctx, base, ["psql", "-l"])
    if returncode is None:
        raise click.ClickException("Could not locate `psql`. Is PostgreSQL installed?")
    if returncode != 0:
        raise click.ClickException(f"Could not list databases: {stderr.strip()}")
    prefix = f"{sanitize_db_name(base.name)}-"
    if show_all:
        click.echo(stdout, nl=False)
    else:
        click.echo(_filter_db_listing(stdout, prefix), nl=False)

    # Filestore directories with no matching database — e.g. leftovers of
    # dropped databases. The full (unfiltered) name set decides whether a
    # filestore dangles; the prefix only filters what is displayed.
    # Extra listing sections contributed by plugins — e.g. `osh_db_drop`
    # reports filestore directories with no matching database.
    db_names = set(_list_db_names(stdout))
    for hook in load_hooks(HOOK_DB_LIST_SECTIONS):
        for line in hook(ctx, base, db_names, prefix, show_all) or []:
            click.echo(line)


def _list_db_names(output):
    """Return the database names listed in ``psql -l`` output."""
    lines = output.splitlines()
    sep = next(
        (
            i
            for i, line in enumerate(lines)
            if line.strip() and set(line.strip()) <= {"-", "+"}
        ),
        None,
    )
    if sep is None:
        return []
    return [line.split("|", 1)[0].strip() for line in lines[sep + 1 :] if "|" in line]


def _filter_db_listing(output, prefix):
    """Keep the ``psql -l`` header and rows whose name starts with *prefix*.

    The ``(N rows)`` footer is recomputed for the filtered set. Output that
    does not look like a ``psql -l`` table (no ``---+---`` separator line)
    is returned unchanged.
    """
    lines = output.splitlines()
    sep = next(
        (
            i
            for i, line in enumerate(lines)
            if line.strip() and set(line.strip()) <= {"-", "+"}
        ),
        None,
    )
    if sep is None:
        return output
    rows = [
        line
        for line in lines[sep + 1 :]
        if "|" in line and line.split("|", 1)[0].strip().startswith(prefix)
    ]
    footer = f"({len(rows)} row{'s' if len(rows) != 1 else ''})"
    return "\n".join([*lines[: sep + 1], *rows, footer]) + "\n"


def _set_branch_db(base, db_name, branch):
    """Record *db_name* as the database for *branch* and return both names."""
    branch = resolve_branch(base, branch)
    value = _require_db_name(db_name)
    set_project_config(base, "db", branch, value)
    return branch, value


@db.command(name="set")
@click.argument("db_name")
@click.option(
    "--branch",
    help="Branch to use the database for (defaults to current branch). May be a glob pattern.",
)
@click.pass_context
def set_db(ctx, db_name, branch):  # noqa: D401
    """Set the database for the current or specified branch.

    The branch can be an exact git branch name or a glob pattern such as
    ``feature/*``. Use ``osh db unset`` to remove the mapping and let the
    branch fall back to the generated ``<project>-<branch>`` database name.

    The name is sanitized before it is stored to keep it safe for PostgreSQL
    and Odoo's ``--db-filter``.

    Examples:

    \b
      osh db set myproject-main
      osh db set myproject-shared --branch staging
      osh db set shared-db --branch "feature/*"
    """
    base = find_project_root(required=True)
    branch, value = _set_branch_db(base, db_name, branch)
    echo.info(f"Branch '{branch}' will use database '{value}'")


@db.command(name="copy")
@click.argument("from_db")
@click.argument("to_db")
@click.pass_context
def copy(ctx, from_db, to_db):  # noqa: D401
    """Copy a PostgreSQL database to a new name, replacing the target if it exists."""
    base = find_project_root(required=True)
    from_name = _require_db_name(from_db)
    to_name = _require_db_name(to_db)
    if not db_exists(base, from_name, ctx=ctx):
        raise click.ClickException(f"Source database '{from_name}' does not exist.")
    copy_db(base, from_name, to_name, ctx=ctx)
    echo.info(f"Copied database '{from_name}' to '{to_name}'")


@db.command(
    name="shell",
    context_settings=dict(ignore_unknown_options=True),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the assembled command without executing it.",
)
@click.option(
    "--compose-file",
    default=None,
    envvar="OSH_COMPOSE_FILE",
    help="Docker Compose file to use (e.g. devel.yaml for Doodba). "
    "Defaults to $OSH_COMPOSE_FILE.",
)
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def db_shell(ctx, dry_run, compose_file, extra_args):  # noqa: D401
    """Enter the database environment or run a command in it.

    Without arguments this opens an interactive shell where the database
    runs: the Compose ``db`` service container on Docker projects, or the
    project environment itself on host/venv backends — where it is
    equivalent to ``osh shell``. PostgreSQL connection variables
    (``PGHOST``, ``PGUSER``, ``PGDATABASE``, ...) are already configured for
    the current branch's database. Any arguments are passed through as a
    command to run in that environment.

    Examples:

    \b
      osh db shell
      osh db shell psql
      osh db shell pg_dump -Fc myproject-main > backup.dump
    """
    base = find_project_root(required=True)

    backend = resolve_backend(base)

    check_run_diagnostics(base, backend, ctx, compose_file=compose_file)

    args = list(extra_args)
    if args and args[0] == "--":
        args.pop(0)

    conf_path, env_vars, resolved_db = prepare_env_context(
        base,
        backend,
        ctx=ctx,
        db_name=parse_explicit_db(args),
        extra_args=args,
        dry_run=dry_run,
    )
    if conf_path:
        echo.info(f"Using config: {conf_path}")
    if resolved_db:
        echo.info(f"Using database: {resolved_db}")

    env_spec = EnvSpec(
        argv=args,
        env=env_vars,
        db_name=resolved_db,
        config_path=str(conf_path) if conf_path else None,
    )
    backend.db_env(ctx, base, env_spec, dry_run=dry_run)


@db.command(name="unset")
@click.option(
    "--branch",
    help="Branch to unset (defaults to current branch).",
)
@click.pass_context
def unset_db(ctx, branch):  # noqa: D401
    """Unset a branch's database and let it fall back to the generated default.

    Removes the exact branch mapping from ``.osh/config.toml``. If a glob
    pattern still matches the branch, that pattern will continue to apply. To
    override a pattern for one specific branch, set it with ``osh db set``.

    Examples:

    \b
      osh db unset
      osh db unset --branch feature/old-thing
    """
    base = find_project_root(required=True)
    branch = resolve_branch(base, branch)

    unset_project_config(base, "db", branch)
    echo.info(f"Unset branch '{branch}'")
