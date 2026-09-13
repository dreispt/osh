"""Osh CLI sub-commands packaged as individual modules.

Importing this package provides the list `COMMANDS` that can be registered
with the root click group in `cli.py`.
"""

from . import (
    config_cmd,
    db_cmd,
    doctor_cmd,
    down_cmd,
    init_cmd,
    odoo_cmd,
    plug_cmd,
    shell_cmd,
    switch_cmd,
)

COMMANDS = [
    init_cmd.init,
    odoo_cmd.odoo,
    switch_cmd.switch,
    shell_cmd.shell,
    down_cmd.down,
    db_cmd.db,
    doctor_cmd.doctor,
    config_cmd.config,
    plug_cmd.plug,
]

__all__ = ["COMMANDS"]
