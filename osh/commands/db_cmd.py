"""`osh db` command for managing databases and branch-to-database mappings."""

import click

from .. import echo
from ..cli_utils import NaturalOrderGroup
from ..common import find_project_root
from ..db import (
    _require_db_name,
    copy_db,
    db_exists,
    resolve_branch,
    resolve_db_name,
    run_in_backend,
    sanitize_db_name,
    set_project_config,
    unset_project_config,
)


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
    if show_all:
        click.echo(stdout, nl=False)
        return
    prefix = f"{sanitize_db_name(base.name)}-"
    click.echo(_filter_db_listing(stdout, prefix), nl=False)


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
