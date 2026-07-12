"""`osh config` command group for managing project settings."""
from __future__ import annotations

import click

from ..utils import (
    _find_project_root,
    _get_branch_db,
    _get_current_branch,
    _get_last_db,
    _get_osh_config_path,
    _load_osh_config,
    _sanitize_db_name,
    _set_branch_db,
    _set_last_db,
)


@click.group(name="config")
@click.pass_context
def config(ctx: click.Context) -> None:  # noqa: D401
    """Manage Osh project settings."""


@config.command(name="db")
@click.argument("db_name")
@click.option(
    "--branch",
    help="Branch to associate the database with (defaults to current branch).",
)
@click.option(
    "--default",
    "set_default",
    is_flag=True,
    help="Also set this database as the default/last used.",
)
@click.pass_context
def db(
    ctx: click.Context,
    db_name: str,
    branch: str | None,
    set_default: bool,
) -> None:  # noqa: D401
    """Set the preferred database for a branch."""

    base = _find_project_root()
    if base is None:
        raise click.ClickException(
            "Not inside an Osh project. Run 'osh init <version>' to create one."
        )

    if branch is None:
        branch = _get_current_branch(base) or "default"

    db_name = _sanitize_db_name(db_name)
    if not db_name:
        raise click.ClickException("A database name is required.")

    _set_branch_db(base, branch, db_name)
    if set_default:
        _set_last_db(base, db_name)

    click.echo(f"Set database for branch '{branch}' to '{db_name}'")


@config.command(name="show")
@click.pass_context
def show(ctx: click.Context) -> None:  # noqa: D401
    """Show the Osh project configuration."""

    base = _find_project_root()
    if base is None:
        raise click.ClickException(
            "Not inside an Osh project. Run 'osh init <version>' to create one."
        )

    cfg = _load_osh_config(base)
    config_path = _get_osh_config_path(base)
    click.echo(f"Configuration file: {config_path}")

    if cfg.has_section("db"):
        for key, value in cfg.items("db"):
            click.echo(f"  {key} = {value}")
    else:
        click.echo("  No database configuration.")
