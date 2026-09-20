"""`osh db` command for managing databases and branch-to-database mappings."""

import click

from .. import echo
from ..cli_utils import handler_group
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
from ..handlers import CommandHandler, subcommand
from .shell_cmd import ShellRun


class Db(CommandHandler):
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

    _cli_name = "db"

    @subcommand
    def show(self):
        """Show the database for the current branch.

        Prints the current git branch, the resolved database name, and whether
        the database already exists in PostgreSQL. This is a quick way to check
        what ``osh odoo`` would use before starting Odoo.
        """
        self.base = find_project_root(required=True)
        self.branch = resolve_branch(self.base, None)
        self.db_name = resolve_db_name(self.base, verbose=False)
        self.exists = db_exists(self.base, self.db_name, ctx=self.ctx)
        echo.info(f"Branch:   {self.branch}")
        echo.info(f"Database: {self.db_name}")
        echo.info(f"Exists:   {'yes' if self.exists else 'no'}")

    @subcommand
    @click.option(
        "--all",
        "show_all",
        is_flag=True,
        help="List all databases, not only this project's.",
    )
    def list(self):
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
        self.base = find_project_root(required=True)
        returncode, stdout, stderr = run_in_backend(self.ctx, self.base, ["psql", "-l"])
        if returncode is None:
            raise click.ClickException(
                "Could not locate `psql`. Is PostgreSQL installed?"
            )
        if returncode != 0:
            raise click.ClickException(f"Could not list databases: {stderr.strip()}")
        self.prefix = f"{sanitize_db_name(self.base.name)}-"
        if self.show_all:
            click.echo(stdout, nl=False)
        else:
            click.echo(_filter_db_listing(stdout, self.prefix), nl=False)

        self.db_names = set(_list_db_names(stdout))
        for line in self.extra_sections():
            click.echo(line)

    def extra_sections(self):
        """Extra sections printed after the ``db list`` output.

        Extension point — plugins subclass ``Db``, override this and
        append to ``super().extra_sections()``; e.g. ``osh_db_drop``
        reports filestore directories with no matching database.
        """
        return []

    @subcommand
    @click.argument("db_name")
    @click.option(
        "--branch",
        help="Branch to use the database for (defaults to current "
        "branch). May be a glob pattern.",
    )
    def set(self):
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
        self.base = find_project_root(required=True)
        self.branch, self.value = _set_branch_db(self.base, self.db_name, self.branch)
        echo.info(f"Branch '{self.branch}' will use database '{self.value}'")

    @subcommand
    @click.argument("from_db")
    @click.argument("to_db")
    def copy(self):
        """Copy a PostgreSQL database to a new name, replacing the target if it exists."""
        self.base = find_project_root(required=True)
        from_name = _require_db_name(self.from_db)
        to_name = _require_db_name(self.to_db)
        if not db_exists(self.base, from_name, ctx=self.ctx):
            raise click.ClickException(f"Source database '{from_name}' does not exist.")
        copy_db(self.base, from_name, to_name, ctx=self.ctx)
        echo.info(f"Copied database '{from_name}' to '{to_name}'")

    @subcommand(context_settings=dict(ignore_unknown_options=True))
    def shell(self):
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
        ShellRun(
            self.env,
            dry_run=self.dry_run,
            compose_file=self.compose_file,
            extra_args=self.extra_args,
            use_db_env=True,
        ).run()

    @classmethod
    def shell_options(cls):
        """``db shell`` takes ``osh shell``'s parameters verbatim."""
        return ShellRun.get_options()

    @subcommand
    @click.option(
        "--branch",
        help="Branch to unset (defaults to current branch).",
    )
    def unset(self):
        """Unset a branch's database and let it fall back to the generated default.

        Removes the exact branch mapping from ``.osh/config.toml``. If a glob
        pattern still matches the branch, that pattern will continue to apply.
        To override a pattern for one specific branch, set it with
        ``osh db set``.

        Examples:

        \b
          osh db unset
          osh db unset --branch feature/old-thing
        """
        self.base = find_project_root(required=True)
        self.branch = resolve_branch(self.base, self.branch)
        unset_project_config(self.base, "db", self.branch)
        echo.info(f"Unset branch '{self.branch}'")


db = handler_group("db", Db)


def _psql_table_split(output):
    """Split ``psql -l`` output at the ``---+---`` separator line.

    Returns ``(lines, sep_index)``; *sep_index* is None when the output
    does not look like a ``psql -l`` table.
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
    return lines, sep


def _list_db_names(output):
    """Return the database names listed in ``psql -l`` output."""
    lines, sep = _psql_table_split(output)
    if sep is None:
        return []
    return [line.split("|", 1)[0].strip() for line in lines[sep + 1 :] if "|" in line]


def _filter_db_listing(output, prefix):
    """Keep the ``psql -l`` header and rows whose name starts with *prefix*.

    The ``(N rows)`` footer is recomputed for the filtered set. Output that
    does not look like a ``psql -l`` table (no ``---+---`` separator line)
    is returned unchanged.
    """
    lines, sep = _psql_table_split(output)
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
