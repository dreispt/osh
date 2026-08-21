"""`osh db` command for managing branch-to-database mappings."""

import click

from .. import echo
from ..common import find_project_root
from ..db import (
    db_exists,
    get_current_branch,
    resolve_db_name,
    set_project_config,
    unset_project_config,
    validate_db_name,
)


@click.group(name="db")
def db():  # noqa: D401
    """Manage branch-to-database mappings.

    Each git branch can be pinned to a specific PostgreSQL database, or left
    unpinned to fall back to the generated ``<project>-<branch>`` name.
    Mappings are stored in ``.osh/config.toml`` under the ``[db]`` section.

    Branches are matched in this order:

    1. Exact branch name (e.g. ``main``).
    2. Longest matching glob pattern (e.g. ``feature/*``).
    3. The special ``default`` key.
    4. Generated ``<project>-<branch>`` if nothing is configured.

    Use ``auto`` for a mapping value to mean the generated default.

    Examples:

    \b
      osh db show
      osh db pin myproject-main --branch main
      osh db pin myproject-staging --branch staging
      osh db pin auto --branch feature/new-thing
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


@db.command(name="pin")
@click.argument("db_name")
@click.option(
    "--branch",
    help="Branch to pin (defaults to current branch). May be a glob pattern.",
)
@click.pass_context
def pin(ctx, db_name, branch):  # noqa: D401
    """Pin a branch or pattern to a database.

    The branch can be an exact git branch name or a glob pattern such as
    ``feature/*``. Use ``auto`` for DB_NAME to let the branch use the generated
    ``<project>-<branch>`` database name.

    The name is stored as given, so existing databases with unusual names stay
    reachable. A warning is shown when the name may not be safe for PostgreSQL
    or Odoo's ``--db-filter``.

    Examples:

    \b
      osh db pin myproject-main
      osh db pin myproject-shared --branch staging
      osh db pin auto --branch "feature/*"
      osh config db myproject-dev --branch main
    """
    base = find_project_root(required=True)
    if branch is None:
        branch = get_current_branch(base) or "default"

    value = validate_db_name(db_name)
    set_project_config(base, "db", branch, value)
    echo.info(f"Pinned branch '{branch}' to database '{value}'")


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
    override a pattern for one specific branch, pin it explicitly.

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
