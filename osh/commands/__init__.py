"""Osh CLI sub-commands packaged as individual modules.

Importing this package provides the list `COMMANDS` that can be registered
with the root click group in `cli.py`.
"""

from . import config_cmd, db_cmd, init_cmd, odoo_cmd, shell_cmd, stop_cmd

COMMANDS = [
    init_cmd.init,
    stop_cmd.stop,
    odoo_cmd.odoo,
    shell_cmd.shell,
    db_cmd.db,
    config_cmd.config,
]

__all__ = ["COMMANDS"]
