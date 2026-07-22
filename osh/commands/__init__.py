"""Osh CLI sub-commands packaged as individual modules.

Importing this package provides the list `COMMANDS` that can be registered
with the root click group in `cli.py`.
"""

from . import (
    backup_cmd,
    config_cmd,
    doctor_cmd,
    init_cmd,
    odoo_cmd,
    plug_cmd,
    restore_cmd,
    run_cmd,
    venv_cmd,
    version_cmd,
)

COMMANDS = [
    init_cmd.init,
    doctor_cmd.doctor,
    run_cmd.run,
    odoo_cmd.odoo,
    venv_cmd.venv,
    restore_cmd.restore,
    backup_cmd.backup,
    config_cmd.config,
    plug_cmd.plug,
    version_cmd.version,
]

__all__ = ["COMMANDS"]
