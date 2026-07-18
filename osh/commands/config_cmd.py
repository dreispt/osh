"""`osh config` command group for managing project settings."""

from __future__ import annotations

import click

from ..commons import find_project_root, get_osh_config_path
from ..db import (
    get_current_branch,
    load_osh_config,
    sanitize_db_name,
    save_osh_config,
    set_project_config,
)
from ..userconfig import save_user_preference


@click.group(name="config")
@click.pass_context
def config(ctx: click.Context) -> None:  # noqa: D401
    """Manage Osh project settings stored in `.osh/config`."""


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
    """Set the preferred database for a branch.

    By default the current git branch is used. Use --branch to target another
    branch. Use --default to also store this database as the last used default.

    Examples:

    \b
      osh config db myproject-dev
      osh config db myproject-dev --branch main
      osh config db myproject-dev --default
    """

    base = find_project_root(required=True)

    if branch is None:
        branch = get_current_branch(base) or "default"

    db_name = sanitize_db_name(db_name)
    if not db_name:
        raise click.ClickException("A database name is required.")

    set_project_config(base, "db", branch, db_name)
    if set_default:
        set_project_config(base, "db", "last", db_name)

    click.echo(f"Set database for branch '{branch}' to '{db_name}'")


@config.command(name="show")
@click.pass_context
def show(ctx: click.Context) -> None:  # noqa: D401
    """Show the current Osh project configuration."""

    base = find_project_root(required=True)

    cfg = load_osh_config(base)
    config_path = get_osh_config_path(base)
    click.echo(f"Configuration file: {config_path}")

    if cfg.has_section("db"):
        click.echo("Database configuration:")
        for key, value in cfg.items("db"):
            click.echo(f"  {key} = {value}")
    else:
        click.echo("  No database configuration.")

    if cfg.has_section("user"):
        click.echo("User preferences:")
        for key, value in cfg.items("user"):
            # Format boolean values nicely
            if value.lower() in ("true", "false"):
                display = "on" if value.lower() == "true" else "off"
                click.echo(f"  {key} = {display}")
            else:
                click.echo(f"  {key} = {value}")
    else:
        click.echo("  No user preferences.")


@config.group(name="user")
@click.pass_context
def user(ctx: click.Context) -> None:  # noqa: D401
    """Manage user preferences for this project."""


@user.command(name="verbosity")
@click.argument(
    "level", type=click.Choice(["quiet", "normal", "friendly", "verbose", "debug"])
)
@click.option(
    "--global",
    "global_setting",
    is_flag=True,
    help="Set globally in ~/.config/osh/config.toml instead of project-specific.",
)
@click.pass_context
def verbosity(
    ctx: click.Context,
    level: str,
    global_setting: bool,
) -> None:  # noqa: D401
    """Set the verbosity level for Osh commands.

    Levels:
      quiet     - Only errors
      normal    - Essential information (default for experienced users)
      friendly  - Helpful guidance and next steps (default for new users)
      verbose   - Detailed information about what's happening
      debug     - Maximum detail for troubleshooting

    Examples:

    \b
      osh config user verbosity normal
      osh config user verbosity quiet --global
    """
    if global_setting:
        # Set in global user config
        save_user_preference("verbosity", level)
        click.echo(f"Set global verbosity to: {level}")
    else:
        # Set in project config
        base = find_project_root(required=True)
        cfg = load_osh_config(base)
        cfg.set("user", "verbosity", level)
        save_osh_config(base, cfg)
        click.echo(f"Set project verbosity to: {level}")


@user.command(name="emoji")
@click.argument("enabled", type=click.Choice(["on", "off"]))
@click.option(
    "--global",
    "global_setting",
    is_flag=True,
    help="Set globally in ~/.config/osh/config.toml instead of project-specific.",
)
@click.pass_context
def emoji(
    ctx: click.Context,
    enabled: str,
    global_setting: bool,
) -> None:  # noqa: D401
    """Enable or disable emoji prefixes in output.

    For those who prefer a more serious terminal experience.

    Examples:

    \b
      osh config user emoji off
      osh config user emoji on --global
    """
    value = enabled == "on"
    if global_setting:
        # Set in global user config
        save_user_preference("emoji", value, section="user")
        click.echo(f"Set global emoji to: {enabled}")
    else:
        # Set in project config
        base = find_project_root(required=True)
        cfg = load_osh_config(base)
        cfg.set("user", "emoji", str(value))
        save_osh_config(base, cfg)
        click.echo(f"Set project emoji to: {enabled}")
