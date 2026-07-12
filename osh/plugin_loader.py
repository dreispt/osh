"""Plugin loader for Osh.

Loads built-in plugins from `osh.plugins` and user-installed plugins from
`~/.config/osh/plugins/`.

A plugin must expose a `get_commands()` function returning a list of Click
commands, or a `COMMANDS` list. Plugins are expected to be Python packages
(directories with `__init__.py`) or a single `osh_plugin.py` file.
"""
from __future__ import annotations

import importlib.util
import os
import pkgutil
import re
import sys
from pathlib import Path
from typing import Any

import click


def _user_plugin_dir() -> Path:
    """Return the directory where user plugins are installed."""
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        base = Path(config_home)
    else:
        base = Path.home() / ".config"
    return base / "osh" / "plugins"


def _plugin_name_from_path(path: Path) -> str:
    """Return a valid Python module name for a plugin directory."""
    name = path.name
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", name)
    name = name.strip("_")
    if name[0].isdigit():
        name = f"plugin_{name}"
    return name or "plugin"


def _load_commands(module: Any) -> list[click.Command]:
    """Return Click commands exposed by a plugin module."""
    if hasattr(module, "get_commands"):
        commands = module.get_commands()
    elif hasattr(module, "COMMANDS"):
        commands = module.COMMANDS
    else:
        commands = []

    if not isinstance(commands, list):
        commands = [commands]
    return [cmd for cmd in commands if isinstance(cmd, click.Command)]


def _import_plugin_from_dir(plugin_dir: Path, prefix: str = "osh_user_plugin") -> Any | None:
    """Import a plugin package or `osh_plugin.py` from a directory."""
    if not plugin_dir.is_dir():
        return None

    init_file = plugin_dir / "__init__.py"
    module_file = plugin_dir / "osh_plugin.py"
    module_name = f"{prefix}_{_plugin_name_from_path(plugin_dir)}"

    if init_file.is_file():
        spec = importlib.util.spec_from_file_location(module_name, init_file)
    elif module_file.is_file():
        spec = importlib.util.spec_from_file_location(module_name, module_file)
    else:
        return None

    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_builtin_plugins() -> list[click.Command]:
    """Load built-in plugins from the `osh.plugins` package."""
    commands: list[click.Command] = []
    try:
        import osh.plugins as plugins_pkg

        for _, module_name, _ in pkgutil.iter_modules(plugins_pkg.__path__, prefix="osh.plugins."):
            try:
                module = importlib.import_module(module_name)
                commands.extend(_load_commands(module))
            except Exception:
                # Skip broken built-in plugins.
                continue
    except ImportError:
        pass
    return commands


def _load_user_plugins() -> list[click.Command]:
    """Load user-installed plugins from `~/.config/osh/plugins/`."""
    commands: list[click.Command] = []
    plugin_dir = _user_plugin_dir()
    if not plugin_dir.is_dir():
        return commands

    for child in plugin_dir.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        try:
            module = _import_plugin_from_dir(child)
            if module is not None:
                commands.extend(_load_commands(module))
        except Exception:
            # Skip broken user plugins.
            continue
    return commands


def load_plugins() -> list[click.Command]:
    """Return all plugin commands from built-in and user plugins."""
    commands = _load_builtin_plugins()
    commands.extend(_load_user_plugins())
    return commands
