"""`osh db` command for managing databases and branch-to-database mappings."""

import sys

import click

from .. import echo
from ..cli_utils import InputRequired, emit_json, handler_group, no_input
from ..common import env_root, find_project_root
from ..db import (
    _list_db_names,
    _psql_table_split,
    choose_db_name,
    copy_db,
    db_exists,
    resolve_branch,
    resolve_db_name,
    run_in_runtime,
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
      osh db exec
      osh db exec psql
      osh db unset
      osh db unset --branch feature/old-thing
    """

    _cli_name = "db"

    @subcommand
    @click.option(
        "--json",
        "as_json",
        is_flag=True,
        help="Print the result as JSON for machine consumption.",
    )
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
        if self.as_json:
            emit_json(
                {
                    "branch": self.branch,
                    "database": self.db_name,
                    "exists": self.exists,
                }
            )
            return
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
    @click.option(
        "--json",
        "as_json",
        is_flag=True,
        help="Print the result as JSON for machine consumption.",
    )
    def list(self):
        """List PostgreSQL databases, filtered to this project by default.

        Runs ``psql -l`` inside the project's runtime environment — the same
        context ``osh exec`` provides — and keeps only databases whose name
        starts with the generated ``<project>-`` prefix. Use ``--all`` to list
        every database on the server.

        Examples:

        \b
          osh db list
          osh db list --all
        """
        self.base = find_project_root(required=True)
        returncode, stdout, stderr = run_in_runtime(self.ctx, self.base, ["psql", "-l"])
        if returncode is None:
            raise click.ClickException(
                "Could not locate `psql`. Is PostgreSQL installed?"
            )
        if returncode != 0:
            raise click.ClickException(f"Could not list databases: {stderr.strip()}")
        self.prefix = f"{sanitize_db_name(env_root(self.base).name)}-"
        names = _list_db_names(stdout)
        self.db_names = set(names)
        if self.as_json:
            emit_json(
                {
                    "project": env_root(self.base).name,
                    "prefix": self.prefix,
                    "all": self.show_all,
                    "databases": (
                        names
                        if self.show_all
                        else [n for n in names if n.startswith(self.prefix)]
                    ),
                    **self.extra_data(),
                }
            )
            return
        if self.show_all:
            click.echo(stdout, nl=False)
        else:
            click.echo(_filter_db_listing(stdout, self.prefix), nl=False)

        for line in self.extra_sections():
            click.echo(line)

    def extra_sections(self):
        """Extra sections printed after the ``db list`` output.

        Extension point — plugins subclass ``Db``, override this and
        append to ``super().extra_sections()``; e.g. ``osh_db_drop``
        reports filestore directories with no matching database.
        """
        return []

    def extra_data(self):
        """Structured extras merged into ``db list --json`` output.

        The JSON counterpart of :meth:`extra_sections` — plugins
        subclass ``Db`` and override this to contribute machine-readable
        fields; the default contributes nothing.
        """
        return {}

    @subcommand
    @click.argument("db_name", required=False)
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
        and Odoo's ``--db-filter``. When *db_name* is omitted, an interactive
        picker lists the existing databases.

        Examples:

        \b
          osh db set myproject-main
          osh db set
          osh db set myproject-shared --branch staging
          osh db set shared-db --branch "feature/*"
        """
        self.base = find_project_root(required=True)
        if self.db_name is None:
            self.db_name = choose_db_name(self.base, ctx=self.ctx)
        self.branch, self.value = _set_branch_db(self.base, self.db_name, self.branch)
        echo.success(f"Branch '{self.branch}' will use database '{self.value}'")

    @subcommand
    @click.argument("from_db", required=False)
    @click.argument("to_db", required=False)
    @click.option(
        "--from-current",
        is_flag=True,
        help="Use the current branch's database as the copy source.",
    )
    @click.option(
        "--to-current",
        is_flag=True,
        help="Use the current branch's database as the copy target — "
        "the name 'osh db show' reports.",
    )
    @click.option(
        "--filestore",
        type=click.Choice(["link", "copy", "none"]),
        default="link",
        show_default=True,
        help="What to do with the target's filestore: 'link' symlinks it "
        "onto the source's (fast, shared), 'copy' gives the target its "
        "own copy (slower, isolated), 'none' leaves it alone.",
    )
    @click.option(
        "--force",
        is_flag=True,
        help="Replace an existing target database without asking.",
    )
    def copy(self):
        """Copy a PostgreSQL database to a new name.

        An existing target is replaced only with ``--force`` (or after
        interactive confirmation) — it is never dropped silently.

        An idle source is cloned near-instantly via ``CREATE DATABASE ...
        TEMPLATE``. When other sessions hold the source open, you are asked
        to close them for the fast clone or keep them and copy with
        ``pg_dump``/``pg_restore`` (which shows progress bars).

        The new database's filestore is linked onto the source's by
        default, so attachments — asset bundles, images — resolve
        without a rebuild. ``--filestore copy`` gives the target its own
        independent copy instead (slower, isolated), and
        ``--filestore none`` leaves the filestore alone.

        Either side may be the current branch's database instead of a
        typed name: ``--from-current`` snapshots it, ``--to-current``
        seeds it (the target is created when missing).
        """
        self.base = find_project_root(required=True)
        from_name, to_name = self._copy_targets()
        if from_name == to_name:
            raise click.UsageError("Source and target database are the same.")
        if not db_exists(self.base, from_name, ctx=self.ctx):
            raise click.ClickException(f"Source database '{from_name}' does not exist.")
        self._confirm_target_replace(to_name)
        copy_db(self.base, from_name, to_name, ctx=self.ctx, filestore=self.filestore)

    def _confirm_target_replace(self, to_name):
        """Gate replacing an existing target behind ``--force``.

        Mirrors ``db drop``/``backup restore``: a TTY asks, a pipe fails
        with instructions, and ``--no-input`` exits 3 naming the flag —
        an existing target is never dropped silently.
        """
        if self.force or not db_exists(self.base, to_name, ctx=self.ctx):
            return
        if no_input():
            raise InputRequired(
                f"Database '{to_name}' already exists; "
                "re-run with --force to replace it."
            )
        if not _stdin_is_tty():
            raise click.ClickException(
                f"Database '{to_name}' already exists. Use --force to replace it."
            )
        click.confirm(
            f"Replace existing database '{to_name}'?", default=False, abort=True
        )

    def _copy_targets(self):
        """Resolve the ``db copy`` FROM/TO names.

        Positional names are sanitised as before; ``--from-current`` /
        ``--to-current`` substitute the current branch's resolved
        database for that side. Conflicting or missing sides are usage
        errors — there is no silent guessing of the target.
        """
        if self.from_current and self.to_current:
            raise click.UsageError(
                "--from-current and --to-current would copy the current "
                "database onto itself."
            )
        from_arg, to_arg = self.from_db, self.to_db
        if self.from_current and to_arg is None and from_arg is not None:
            # ``db copy --from-current DST`` — the lone positional fills
            # the unclaimed TO side even though it binds the FROM slot.
            from_arg, to_arg = None, from_arg
        if self.from_current and from_arg:
            raise click.UsageError("'FROM' and --from-current are mutually exclusive.")
        if self.to_current and to_arg:
            raise click.UsageError("'TO' and --to-current are mutually exclusive.")
        current = None
        if self.from_current or self.to_current:
            current = resolve_db_name(self.base, verbose=False)
        from_name = (
            current
            if self.from_current
            else sanitize_db_name(from_arg) if from_arg else None
        )
        to_name = (
            current if self.to_current else sanitize_db_name(to_arg) if to_arg else None
        )
        missing = [
            side for side, name in (("FROM", from_name), ("TO", to_name)) if not name
        ]
        if missing:
            raise click.UsageError(
                f"Missing {' and '.join(missing)}. Pass "
                f"{'/'.join(missing)} explicitly or use "
                "--from-current/--to-current for the current branch's "
                "database."
            )
        return from_name, to_name

    @subcommand(
        name="exec",
        context_settings=dict(ignore_unknown_options=True),
    )
    def exec(self):
        """Enter the database environment or run a command in it.

        Without arguments this opens an interactive shell where the database
        runs: the Compose ``db`` service container on Docker projects, or the
        project environment itself on host/venv runtimes — where it is
        equivalent to ``osh exec``. PostgreSQL connection variables
        (``PGHOST``, ``PGUSER``, ``PGDATABASE``, ...) are already configured for
        the current branch's database. Any arguments are passed through as a
        command to run in that environment.

        Examples:

        \b
          osh db exec
          osh db exec psql
          osh db exec pg_dump -Fc myproject-main > backup.dump
        """
        ShellRun(
            self.env,
            dry_run=self.dry_run,
            compose_file=self.compose_file,
            extra_args=self.extra_args,
            use_db_env=True,
        ).run()

    @classmethod
    def exec_options(cls):
        """``db exec`` takes ``osh exec``'s parameters verbatim."""
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
        echo.success(f"Unset branch '{self.branch}'")


db = handler_group("db", Db)


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
    value = sanitize_db_name(db_name)
    set_project_config(base, "db", branch, value)
    return branch, value


def _stdin_is_tty():
    """Return True when stdin is an interactive terminal."""
    return sys.stdin.isatty()
