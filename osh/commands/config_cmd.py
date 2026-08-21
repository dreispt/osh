"""`osh config` command group for managing project settings."""

import click

from .. import echo
from ..common import find_project_root
from ..config import get_project_config_path, save_user_preference
from ..db import (
    get_current_branch,
    load_osh_config,
    save_osh_config,
    set_project_config,
    validate_db_name,
)


@click.group(name="config")
@click.pass_context
def config(ctx):  # noqa: D401
    """Manage Osh project settings stored in `.osh/config.toml`."""


@config.command(name="db")
@click.argument("db_name")
@click.option(
    "--branch",
    help="Branch to associate the database with (defaults to current branch).",
)
@click.pass_context
def db(
    ctx,
    db_name,
    branch,
):  # noqa: D401
    """Set the preferred database for a branch or pattern.

    By default the current git branch is used. Use --branch to target another
    branch or a glob pattern (e.g. ``feature/*``). Set DB_NAME to ``auto`` to
    use the generated ``<project>-<branch>`` name.

    For a friendlier interface, prefer ``osh db pin``.

    Examples:

    \b
      osh config db myproject-dev
      osh config db myproject-dev --branch main
      osh config db auto --branch fix/123
      osh config db auto --branch "feature/*"
    """

    base = find_project_root(required=True)
    if branch is None:
        branch = get_current_branch(base) or "default"

    db_name = validate_db_name(db_name)
    set_project_config(base, "db", branch, db_name)

    echo.info(f"Set database for branch '{branch}' to '{db_name}'")


@config.command(name="show")
@click.pass_context
def show(ctx):  # noqa: D401
    """Show the current Osh project configuration."""

    base = find_project_root(required=True)

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
        echo.info(f"Set global verbosity to: {level}")
    else:
        # Set in project config
        base = find_project_root(required=True)
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
        echo.info(f"Set global emoji to: {enabled}")
    else:
        # Set in project config
        base = find_project_root(required=True)
        cfg = load_osh_config(base)
        cfg.set("user", "emoji", value)
        save_osh_config(base, cfg)
        echo.info(f"Set project emoji to: {enabled}")
