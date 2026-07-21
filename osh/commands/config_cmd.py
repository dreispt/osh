"""`osh config` command group for managing project settings."""

import click

from ..commons import find_project_root
from ..config import get_project_config_path, save_user_preference
from ..db import (
    get_current_branch,
    load_osh_config,
    sanitize_db_name,
    save_osh_config,
    set_project_config,
)
from ..echo import get_echo


@click.group(name="config")
@click.pass_context
def config(ctx):  # noqa: D401
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
    ctx,
    db_name,
    branch,
    set_default,
):  # noqa: D401
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
    echo = get_echo(ctx, base)

    if branch is None:
        branch = get_current_branch(base) or "default"

    db_name = sanitize_db_name(db_name)
    if not db_name:
        raise click.ClickException("A database name is required.")

    set_project_config(base, "db", branch, db_name)
    if set_default:
        set_project_config(base, "db", "last", db_name)

    echo.info(f"Set database for branch '{branch}' to '{db_name}'")


@config.command(name="show")
@click.pass_context
def show(ctx):  # noqa: D401
    """Show the current Osh project configuration."""

    base = find_project_root(required=True)
    echo = get_echo(ctx, base)

    cfg = load_osh_config(base)
    config_path = get_project_config_path(base)
    echo.info(f"Configuration file: {config_path}")

    if cfg.has_section("db"):
        echo.info("Database configuration:")
        for key, value in cfg.items("db"):
            echo.info(f"  {key} = {value}")
    else:
        echo.info("  No database configuration.")

    if cfg.has_section("user"):
        echo.info("User preferences:")
        for key, value in cfg.items("user"):
            value_str = str(value)
            # Format boolean values nicely
            if value_str.lower() in ("true", "false"):
                display = "on" if value_str.lower() == "true" else "off"
                echo.info(f"  {key} = {display}")
            else:
                echo.info(f"  {key} = {value}")
    else:
        echo.info("  No user preferences.")


@config.group(name="user")
@click.pass_context
def user(ctx):  # noqa: D401
    """Manage user preferences for this project."""


@user.command(name="verbosity")
@click.argument("level", type=click.Choice(["quiet", "normal", "friendly", "verbose"]))
@click.option(
    "--global",
    "global_setting",
    is_flag=True,
    help="Set globally in ~/.config/osh/config.toml instead of project-specific.",
)
@click.pass_context
def verbosity(
    ctx,
    level,
    global_setting,
):  # noqa: D401
    """Set the verbosity level for Osh commands.

    Levels:
      quiet     - Only errors
      normal    - Essential information (default for experienced users)
      friendly  - Helpful guidance and next steps (default for new users)
      verbose   - Detailed information about what's happening

    Examples:

    \b
      osh config user verbosity normal
      osh config user verbosity quiet --global
    """
    if global_setting:
        # Set in global user config
        save_user_preference("verbosity", level)
        echo = get_echo(ctx, None)
        echo.info(f"Set global verbosity to: {level}")
    else:
        # Set in project config
        base = find_project_root(required=True)
        echo = get_echo(ctx, base)
        cfg = load_osh_config(base)
        cfg.set("user", "verbosity", level)
        save_osh_config(base, cfg)
        echo.info(f"Set project verbosity to: {level}")


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
    ctx,
    enabled,
    global_setting,
):  # noqa: D401
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
        echo = get_echo(ctx, None)
        echo.info(f"Set global emoji to: {enabled}")
    else:
        # Set in project config
        base = find_project_root(required=True)
        echo = get_echo(ctx, base)
        cfg = load_osh_config(base)
        cfg.set("user", "emoji", str(value))
        save_osh_config(base, cfg)
        echo.info(f"Set project emoji to: {enabled}")
