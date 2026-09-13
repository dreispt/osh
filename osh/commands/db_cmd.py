"""`osh db` command for managing databases and branch-to-database mappings."""

import click

from .. import echo
from ..cli_utils import NaturalOrderGroup
from ..common import find_project_root
from ..db import (
    _require_db_name,
    copy_db,
    db_exists,
    get_current_branch,
    resolve_db_name,
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

    Use ``auto`` for a mapping value to mean the generated default.

    Examples:

    \b
      osh db show
      osh db use myproject-main --branch main
      osh db use auto --branch feature/new-thing
      osh db copy myproject-main myproject-fix-123
      osh db unpin
      osh db unpin --branch feature/old-thing
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
    branch = get_current_branch(base) or "default"
    db_name = resolve_db_name(base, verbose=False)
    exists = db_exists(base, db_name)
    echo.info(f"Branch:   {branch}")
    echo.info(f"Database: {db_name}")
    echo.info(f"Exists:   {'yes' if exists else 'no'}")


def _set_branch_db(base, db_name, branch):
    """Record *db_name* as the database for *branch* and return both names."""
    if branch is None:
        branch = get_current_branch(base) or "default"
    value = _require_db_name(db_name)
    set_project_config(base, "db", branch, value)
    return branch, value


@db.command(name="use")
@click.argument("db_name")
@click.option(
    "--branch",
    help="Branch to use the database for (defaults to current branch). May be a glob pattern.",
)
@click.pass_context
def use(ctx, db_name, branch):  # noqa: D401
    """Use a database for the current or specified branch.

    The branch can be an exact git branch name or a glob pattern such as
    ``feature/*``. Use ``auto`` for DB_NAME to let the branch use the generated
    ``<project>-<branch>`` database name.

    The name is sanitized before it is stored to keep it safe for PostgreSQL
    and Odoo's ``--db-filter``.

    Examples:

    \b
      osh db use myproject-main
      osh db use myproject-shared --branch staging
      osh db use auto --branch "feature/*"
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
    if not db_exists(base, from_name):
        raise click.ClickException(f"Source database '{from_name}' does not exist.")
    copy_db(base, from_name, to_name)
    echo.info(f"Copied database '{from_name}' to '{to_name}'")


@db.command(name="unpin")
@click.option(
    "--branch",
    help="Branch to unpin (defaults to current branch).",
)
@click.pass_context
def unpin(ctx, branch):  # noqa: D401
    """Unpin a branch and let it fall back to the generated default.

    Removes the exact branch mapping from ``.osh/config.toml``. If a glob
    pattern still matches the branch, that pattern will continue to apply. To
    override a pattern for one specific branch, set it with ``osh db use``.

    Examples:

    \b
      osh db unpin
      osh db unpin --branch feature/old-thing
    """
    base = find_project_root(required=True)
    if branch is None:
        branch = get_current_branch(base) or "default"

    unset_project_config(base, "db", branch)
    echo.info(f"Unpinned branch '{branch}'")
