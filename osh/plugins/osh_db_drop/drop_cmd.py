"""`osh db drop` command implementation."""

import click

from ... import echo
from ...cli_utils import deprecated_option, require_input
from ...common import find_project_root
from ...db import (
    db_exists,
    drop_db,
    get_last_db,
    resolve_db_name,
    sanitize_db_name,
    unset_project_config,
)
from ...handlers import CommandHandler
from .filestore import filestore_exists, remove_filestore


class DbDrop(CommandHandler):
    """Drop a PostgreSQL database and its Odoo filestore.

    Without arguments, drops the current branch's database — the one
    ``osh db show`` reports. A specific name can be given as a positional
    argument (``-d``/``--database`` still works but is deprecated).

    Removes the database and the matching ``filestore/<db>`` directory under
    Odoo's ``data_dir`` (inside the container on Docker runtimes). Asks for
    confirmation unless ``--force`` is given.

    Examples:

    \b
      osh db drop
      osh db drop myproject-fix-123
      osh db drop myproject-fix-123 --force
    """

    _cli_name = "db.drop"

    db_name = None
    database = None
    force = False

    @deprecated_option(
        "-d",
        "--database",
        remove_in="3.0",
        instead="pass the name as a positional argument",
    )
    @click.option(
        "-d",
        "--database",
        help="Deprecated: use the DB_NAME argument instead.",
    )
    @click.option(
        "--force",
        is_flag=True,
        help="Drop without asking for confirmation.",
    )
    @click.argument("db_name", required=False)
    def run(self):
        ctx = self.ctx
        base = find_project_root(required=True)
        requested = self.db_name or self.database
        if self.db_name and self.database:
            raise click.UsageError(
                "Give the database name either as an argument or with "
                "-d/--database, not both."
            )
        name = (
            sanitize_db_name(requested)
            if requested
            else resolve_db_name(base, verbose=True)
        )
        exists = db_exists(base, name, ctx=ctx)
        has_filestore = filestore_exists(ctx, base, name)
        if not exists and not has_filestore:
            echo.info(f"Database '{name}' does not exist — nothing to drop.")
            return
        if not self.force:
            if exists:
                prompt = f"Drop database '{name}' and its filestore?"
            else:
                prompt = f"Remove filestore for '{name}'?"
            require_input(f"confirmation '{prompt}'")
            click.confirm(prompt, default=False, abort=True)
        if exists:
            drop_db(base, name, ctx=ctx)
            if db_exists(base, name, ctx=ctx):
                raise click.ClickException(f"Could not drop database '{name}'.")
            echo.success(f"Dropped database '{name}'")
        remove_filestore(ctx, base, name)
        if get_last_db(base) == name:
            unset_project_config(base, "db", "last_db")
            unset_project_config(base, "db", "last")
