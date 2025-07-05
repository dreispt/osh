"""Osh CLI sub-commands packaged as individual modules.

Importing this package provides the list `COMMANDS` that can be registered
with the root click group in `cli.py`.
"""
from __future__ import annotations

from importlib import import_module

# Names of command modules to import (relative to this package)
_MODULES = [
    "shell",
    "info",
    "init_cmd",  # avoid shadowing built-in `init` when importing
    "status_cmd",
    "run_cmd",
]

COMMANDS = []
for _name in _MODULES:
    mod = import_module(f".{_name}", __package__)
    # Each module exposes a click command object with same basename
    COMMANDS.append(getattr(mod, _name.split("_")[0]))

__all__ = ["COMMANDS"]
