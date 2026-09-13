"""Osh CLI sub-commands packaged as individual modules.

Importing this package provides the list `COMMANDS` that can be registered
with the root click group in `cli.py`.
"""

from . import (
    config_cmd,
    db_cmd,
    doctor_cmd,
    init_cmd,
    odoo_cmd,
    plug_cmd,
    shell_cmd,
    update_cmd,
    version_cmd,
)

COMMANDS = [
    init_cmd.init,
    doctor_cmd.doctor,
    shell_cmd.shell,
    odoo_cmd.odoo,
    update_cmd.update,
    config_cmd.config,
    db_cmd.db,
    plug_cmd.plug,
    version_cmd.version,
]

__all__ = ["COMMANDS"]
