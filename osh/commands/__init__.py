"""Osh CLI sub-commands packaged as individual modules.

Importing this package provides the list `COMMANDS` that can be registered
with the root click group in `cli.py`.
"""
from __future__ import annotations

from . import init_cmd
from . import status_cmd
from . import run_cmd
from . import config_cmd
from . import plug_cmd
from . import version_cmd

COMMANDS = [
    init_cmd.init,
    status_cmd.status,
    run_cmd.run,
    config_cmd.config,
    plug_cmd.plug,
    version_cmd.version,
]

__all__ = ["COMMANDS"]
