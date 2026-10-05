"""Osh CLI sub-commands packaged as individual modules.

Importing this package provides the list `COMMANDS` that can be registered
with the root click group in `cli.py`.
"""

from . import addon_cmd, config_cmd, db_cmd, init_cmd, odoo_cmd, shell_cmd, stop_cmd

COMMANDS = [
    init_cmd.init,
    stop_cmd.stop,
    odoo_cmd.odoo,
    shell_cmd.shell,
    db_cmd.db,
    addon_cmd.addon,
    config_cmd.config,
]

__all__ = ["COMMANDS"]
