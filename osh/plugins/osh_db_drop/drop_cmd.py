"""`osh db drop` command implementation."""

import click

from ... import echo
from ...common import find_project_root
from ...db import (
    _require_db_name,
    db_exists,
    drop_db,
    filestore_exists,
    get_last_db,
    remove_filestore,
    unset_project_config,
)


@click.command(name="drop")
@click.argument("db_name")
@click.option(
    "--force",
    is_flag=True,
    help="Drop without asking for confirmation.",
)
@click.pass_context
def drop(ctx, db_name, force):  # noqa: D401
    """Drop a PostgreSQL database and its Odoo filestore.

    Removes the database and the matching ``filestore/<db>`` directory under
    Odoo's ``data_dir`` (inside the container on Docker backends). Asks for
    confirmation unless ``--force`` is given.

    Examples:

    \b
      osh db drop myproject-fix-123
      osh db drop myproject-fix-123 --force
    """
    base = find_project_root(required=True)
    name = _require_db_name(db_name)
    exists = db_exists(base, name, ctx=ctx)
    has_filestore = filestore_exists(ctx, base, name)
    if not exists and not has_filestore:
        echo.info(f"Database '{name}' does not exist — nothing to drop.")
        return
    if not force:
        if exists:
            prompt = f"Drop database '{name}' and its filestore?"
        else:
            prompt = f"Remove filestore for '{name}'?"
        click.confirm(prompt, default=False, abort=True)
    if exists:
        drop_db(base, name, ctx=ctx)
        if db_exists(base, name, ctx=ctx):
            raise click.ClickException(f"Could not drop database '{name}'.")
        echo.info(f"Dropped database '{name}'")
    remove_filestore(ctx, base, name)
    if get_last_db(base) == name:
        unset_project_config(base, "db", "last_db")
        unset_project_config(base, "db", "last")
