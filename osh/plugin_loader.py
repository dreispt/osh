"""Plugin loader for Osh.

Loads built-in plugins from `osh.plugins`, third-party plugins registered as
Python entry points, and user-installed plugins from `~/.config/osh/plugins/`.

A plugin must expose a `get_commands()` function returning a list of Click
commands, or a `COMMANDS` list. Plugins are expected to be Python packages
(directories with `__init__.py`) or a single `osh_plugin.py` file.

`load_plugins()` returns ``(source, command)`` pairs so callers can resolve
command-name collisions by prefixing the command with its plugin source.
"""

import importlib
import importlib.util
import os
import pkgutil
import re
import sys
from pathlib import Path

import click

from . import echo

try:
    import importlib.metadata as _metadata
except ImportError:  # pragma: no cover
    _metadata = None


def _user_plugin_dir():
    """Return the directory where user plugins are installed."""
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        base = Path(config_home)
    else:
        base = Path.home() / ".config"
    return base / "osh" / "plugins"


def _plugin_name_from_path(path):
    """Return a valid Python module name for a plugin directory."""
    name = path.name
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", name)
    name = name.strip("_")
    if name[0].isdigit():
        name = f"plugin_{name}"
    return name or "plugin"


def _import_plugin_from_dir(plugin_dir, prefix="osh_user_plugin"):
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


def _plugin_source_name(name):
    """Return a CLI-friendly source identifier from a plugin module/directory name."""
    name = re.sub(r"^osh\.plugins\.", "", name)
    name = re.sub(r"[^a-zA-Z0-9]+", "-", name)
    return name.strip("-") or "plugin"


def _iter_entry_point_modules(group="osh.plugins"):
    """Yield ``(source, module)`` pairs from Python entry points.

    Distributions can register plugins under the ``osh.plugins`` entry point
    group. The entry point value must be an importable module path.
    """
    if _metadata is None:
        return
    try:
        eps = _metadata.entry_points()
    except Exception:
        return
    try:
        selected = eps.select(group=group)
    except AttributeError:
        selected = eps.get(group, [])
    for ep in selected:
        try:
            module = importlib.import_module(ep.value)
            yield ep.name, module
        except Exception:
            # Skip broken entry-point plugins.
            continue


def _iter_plugin_modules():
    """Yield ``(source, module)`` pairs for built-in, entry-point and user plugins."""
    try:
        import osh.plugins as plugins_pkg

        for _, module_name, _ in pkgutil.iter_modules(
            plugins_pkg.__path__, prefix="osh.plugins."
        ):
            try:
                module = importlib.import_module(module_name)
                source = _plugin_source_name(module_name)
                yield source, module
            except Exception:
                # Skip broken built-in plugins.
                continue
    except ImportError:
        pass

    yield from _iter_entry_point_modules()

    plugin_dir = _user_plugin_dir()
    if plugin_dir.is_dir():
        for child in plugin_dir.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            try:
                module = _import_plugin_from_dir(child)
                if module is not None:
                    source = _plugin_source_name(child.name)
                    yield source, module
            except Exception:
                # Skip broken user plugins.
                continue


def _load_commands_from_module(module):
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


def _load_backends_from_module(module, backend_type):
    """Return backend classes of *backend_type* exposed by a plugin module."""
    from .backends import Backend

    if hasattr(module, "get_backends"):
        backends = module.get_backends()
    elif hasattr(module, "BACKENDS"):
        backends = module.BACKENDS
    else:
        backends = []

    if not isinstance(backends, list):
        backends = [backends]

    base_map = {"backend": Backend}
    base_cls = base_map.get(backend_type)
    if base_cls is None:
        return []

    valid = []
    for backend in backends:
        if (
            isinstance(backend, type)
            and issubclass(backend, base_cls)
            and backend is not base_cls
            and getattr(backend, "backend_type", None) == backend_type
            and getattr(backend, "name", None)
        ):
            valid.append(backend)
    return valid


def load_plugins():
    """Return ``(source, command)`` pairs for all loaded plugins."""
    commands = []
    for source, module in _iter_plugin_modules():
        commands.extend((source, cmd) for cmd in _load_commands_from_module(module))
    return commands


def load_backends(backend_type=None):
    """Return a mapping of backend name to class.

    *backend_type* is accepted for compatibility and is ignored; only the
    unified ``Backend`` interface is loaded.
    """
    if backend_type is None:
        backend_type = "backend"
    result = {}
    for source, module in _iter_plugin_modules():
        for backend in _load_backends_from_module(module, backend_type):
            name = getattr(backend, "name")
            if not name:
                continue
            if name in result:
                echo.warning(
                    f"backend '{name}' from '{source}' conflicts with "
                    f"an existing backend and is ignored.",
                    err=True,
                )
                continue
            result[name] = backend
    return result
