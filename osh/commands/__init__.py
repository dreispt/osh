"""Osh CLI sub-commands packaged as individual modules.

Importing this package provides the list `COMMANDS` that can be registered
with the root click group in `cli.py`.
"""

from __future__ import annotations

from . import config_cmd, init_cmd, odoo_cmd, plug_cmd, run_cmd, status_cmd, version_cmd

COMMANDS = [
    init_cmd.init,
    init_cmd.init_local,
    status_cmd.status,
    run_cmd.run,
    odoo_cmd.odoo,
    config_cmd.config,
    plug_cmd.plug,
    version_cmd.version,
]

__all__ = ["COMMANDS"]
