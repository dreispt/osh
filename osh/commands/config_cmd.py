"""`osh config` command group for managing project settings."""

import click

from .. import echo
from ..common import find_project_root
from ..config import get_project_config_path, save_user_preference
from ..db import load_osh_config, save_osh_config


@click.group(name="config")
@click.pass_context
def config(ctx):  # noqa: D401
    """Manage Osh project settings stored in `.osh/config.toml`."""


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
