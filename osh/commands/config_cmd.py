"""`osh config` command group for managing project settings."""

import click

from .. import echo
from ..cli_utils import handler_group
from ..common import find_project_root
from ..config import get_project_config_path, save_user_preference
from ..db import load_osh_config, save_osh_config, set_project_config
from ..handlers import CommandHandler, subcommand


class Config(CommandHandler):
    """Manage Osh project settings stored in `.osh/config.toml`."""

    _cli_name = "config"

    @subcommand
    def show(self):
        """Show the current Osh project configuration."""
        self.base = find_project_root(required=True)

        cfg = load_osh_config(self.base)
        config_path = get_project_config_path(self.base)
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


config = handler_group("config", Config)


class ConfigUser(CommandHandler):
    """Manage user preferences for this project."""

    _cli_name = "config.user"

    @subcommand
    @click.argument(
        "level", type=click.Choice(["silent", "normal", "verbose", "debug"])
    )
    @click.option(
        "--global",
        "global_setting",
        is_flag=True,
        help="Set globally in ~/.config/osh/config.toml instead of "
        "project-specific.",
    )
    def verbosity(self):
        """Set the verbosity level for Osh commands.

        Levels:
          silent    - Only errors
          normal    - Essential information
          verbose   - Detailed information about what's happening
          debug     - Verbose plus internal diagnostics (exit codes, timing)

        Examples:

        \b
          osh config user verbosity normal
          osh config user verbosity silent --global
        """
        if self.global_setting:
            # Set in global user config
            save_user_preference("verbosity", self.level)
            echo.info(f"Set global verbosity to: {self.level}")
        else:
            # Set in project config
            self.base = find_project_root(required=True)
            cfg = load_osh_config(self.base)
            cfg.set("user", "verbosity", self.level)
            save_osh_config(self.base, cfg)
            echo.info(f"Set project verbosity to: {self.level}")


class ConfigOdoo(CommandHandler):
    """Manage Odoo runtime defaults."""

    _cli_name = "config.odoo"

    @subcommand
    @click.argument("value")
    @click.option(
        "--global",
        "global_setting",
        is_flag=True,
        help="Set globally in ~/.config/osh/config.toml instead of "
        "project-specific.",
    )
    def dev(self):
        """Set the default ``--dev`` value injected by ``osh odoo``.

        VALUE is any ``--dev`` option value such as ``all`` or a comma-separated
        list (e.g. ``xml,reload``). ``off`` disables the injection entirely —
        equivalent to always passing ``--no-dev``.

        Examples:

        \b
          osh config odoo dev all
          osh config odoo dev xml,reload
          osh config odoo dev off
          osh config odoo dev all --global
        """
        if self.global_setting:
            save_user_preference("dev", self.value, section="odoo")
            echo.info(f"Set global Odoo dev mode to: {self.value}")
            return
        self.base = find_project_root(required=True)
        set_project_config(self.base, "odoo", "dev", self.value)
        echo.info(f"Set project Odoo dev mode to: {self.value}")


config.add_command(handler_group("user", ConfigUser))
config.add_command(handler_group("odoo", ConfigOdoo))
