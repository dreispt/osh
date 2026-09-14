"""Plugin loader for Osh.

Loads built-in plugins from `osh.plugins`, third-party plugins registered as
Python entry points, and user-installed plugins from `~/.config/osh/plugins/`.

A plugin declares what it provides in an `OSH_PLUGIN_MANIFEST` dict with
`commands`, `backends` and `group_commands` keys, plus an optional `hooks`
key mapping hook point names (see `osh.hooks`) to callables or lists of
implementations. Plugins may also define their own hook points — e.g. the
`osh_db_get` plugin discovers backup sources via ``"osh_db_get.sources"``.
Plugins are expected to be Python packages (directories with `__init__.py`)
or a single `osh_plugin.py` file.

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

from .. import echo
from ..config import get_enabled_plugins

try:
    import importlib.metadata as _metadata
except ImportError:  # pragma: no cover
    _metadata = None


def user_plugin_dir():
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
        spec = importlib.util.spec_from_file_location(
            module_name, init_file, submodule_search_locations=[str(plugin_dir)]
        )
    elif module_file.is_file():
        spec = importlib.util.spec_from_file_location(module_name, module_file)
    else:
        return None

    if spec is None or spec.loader is None:
        return None

    cached = sys.modules.get(module_name)
    if cached is not None and getattr(cached, "__file__", None) in (
        str(init_file),
        str(module_file),
    ):
        return cached

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def plugin_source_name(name):
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
    except (ImportError, AttributeError, TypeError) as exc:
        echo.warning(f"Could not scan entry points: {exc}", err=True)
        return
    try:
        selected = eps.select(group=group)
    except AttributeError:
        selected = eps.get(group, [])
    for ep in selected:
        try:
            module = importlib.import_module(ep.value)
            yield ep.name, module
        except Exception as exc:
            echo.error(f"Could not load entry-point plugin '{ep.value}': {exc}")
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
                source = plugin_source_name(module_name)
                yield source, module
            except Exception as exc:
                echo.error(f"Could not load built-in plugin '{module_name}': {exc}")
                continue
    except ImportError:
        pass

    yield from _iter_entry_point_modules()

    plugin_dir = user_plugin_dir()
    if plugin_dir.is_dir():
        for child in sorted(plugin_dir.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            enabled = get_enabled_plugins(child.name)
            if enabled is None or plugin_source_name(child.name) in enabled:
                try:
                    module = _import_plugin_from_dir(child)
                except Exception as exc:
                    echo.error(f"Could not load user plugin '{child}': {exc}")
                    module = None
                if module is not None:
                    yield plugin_source_name(child.name), module
            yield from _iter_subplugins(child, enabled=enabled)


def _iter_subplugins(repo_dir, enabled=None):
    """Yield ``(source, module)`` pairs for plugin packages inside *repo_dir*.

    A plugin directory may also be a "repository" of plugins — like an Odoo
    addons repo: every direct subpackage declaring ``OSH_PLUGIN_MANIFEST``
    is a plugin of its own, so multi-plugin repos need no aggregation code
    and the repo root does not even need an ``__init__.py``.

    *enabled* is the repo's configured enabled-plugin list (see
    ``config.get_enabled_plugins``); ``None`` enables everything. Disabled
    subplugins are skipped before import, so their code never executes.
    """
    prefix = f"osh_user_plugin_{_plugin_name_from_path(repo_dir)}"
    for child in plugin_subdirs(repo_dir):
        source = plugin_source_name(child.name)
        if enabled is not None and source not in enabled:
            continue
        try:
            module = _import_plugin_from_dir(child, prefix=prefix)
        except Exception as exc:
            echo.error(f"Could not load plugin '{child}': {exc}")
            continue
        if module is not None and _plugin_manifest(module):
            yield source, module


def plugin_subdirs(directory):
    """Yield direct subdirectories of *directory* that are Python packages."""
    try:
        children = sorted(directory.iterdir())
    except OSError:
        return
    for child in children:
        if (
            child.is_dir()
            and not child.name.startswith(".")
            and child.name.isidentifier()
            and (child / "__init__.py").is_file()
        ):
            yield child


def _plugin_manifest(module):
    """Return the plugin's ``OSH_PLUGIN_MANIFEST`` dict (empty if absent)."""
    manifest = getattr(module, "OSH_PLUGIN_MANIFEST", None)
    return manifest if isinstance(manifest, dict) else {}


def _load_commands_from_module(module):
    """Return Click commands exposed by a plugin module."""
    commands = _plugin_manifest(module).get("commands", [])
    if not isinstance(commands, list):
        commands = [commands]
    return [cmd for cmd in commands if isinstance(cmd, click.Command)]


def _load_backend_commands_from_module(module):
    """Return Click groups declared as backend commands by a plugin module."""
    commands = _plugin_manifest(module).get("backend_commands", [])
    if not isinstance(commands, list):
        commands = [commands]
    return [cmd for cmd in commands if isinstance(cmd, click.Group)]


def load_backend_commands():
    """Return ``(source, group)`` pairs for plugin-declared backend commands.

    Backend plugins declare a Click group named after their backend (e.g.
    ``docker``) under the ``backend_commands`` manifest key. These groups
    carry the backend's lifecycle commands (``init``, ``doctor``, ``down``,
    plus any extras) and are listed in a separate help section.
    """
    commands = []
    for source, module in _iter_plugin_modules():
        commands.extend(
            (source, cmd) for cmd in _load_backend_commands_from_module(module)
        )
    return commands


def _load_backends_from_module(module):
    """Return ``Backend`` subclasses exposed by a plugin module."""
    from ..backends import Backend

    backends = _plugin_manifest(module).get("backends", [])
    if not isinstance(backends, list):
        backends = [backends]

    return [
        backend
        for backend in backends
        if isinstance(backend, type)
        and issubclass(backend, Backend)
        and backend is not Backend
        and getattr(backend, "name", None)
    ]


def load_plugins():
    """Return ``(source, command)`` pairs for all loaded plugins."""
    commands = []
    for source, module in _iter_plugin_modules():
        commands.extend((source, cmd) for cmd in _load_commands_from_module(module))
    return commands


def _load_group_commands_from_module(module):
    """Return the ``{group_name: [click.Command]}`` mapping from a plugin."""
    groups = _plugin_manifest(module).get("group_commands", {})
    if not isinstance(groups, dict):
        return {}
    result = {}
    for group_name, commands in groups.items():
        if not isinstance(commands, list):
            commands = [commands]
        valid = [cmd for cmd in commands if isinstance(cmd, click.Command)]
        if valid:
            result.setdefault(group_name, []).extend(valid)
    return result


def load_group_commands():
    """Return ``{group_name: [(source, command)]}`` for all loaded plugins.

    Plugins attach subcommands to existing command groups (e.g. ``db``) via
    the ``group_commands`` key of their ``OSH_PLUGIN_MANIFEST``.
    """
    result = {}
    for source, module in _iter_plugin_modules():
        for group_name, commands in _load_group_commands_from_module(module).items():
            result.setdefault(group_name, []).extend((source, c) for c in commands)
    return result


def _iter_hook_entries():
    """Yield ``(source, hook_name, impl)`` for every hook item in plugins."""
    for source, module in _iter_plugin_modules():
        hooks = _plugin_manifest(module).get("hooks", {})
        if not isinstance(hooks, dict):
            continue
        for hook_name, impl in hooks.items():
            items = impl if isinstance(impl, list) else [impl]
            for item in items:
                yield source, hook_name, item


def load_hooks(name=None):
    """Aggregate the ``hooks`` manifest key across all plugins.

    With *name*, return the flat list of implementations registered for that
    hook point. Without it, return the full ``{name: [impls]}`` dict.
    Single (non-list) values are normalized to lists; a non-dict ``hooks``
    entry is ignored.
    """
    result = {}
    for _source, hook_name, impl in _iter_hook_entries():
        result.setdefault(hook_name, []).append(impl)
    return result if name is None else result.get(name, [])


def load_hook_entries(name):
    """Return ``(source, impl)`` pairs for hook point *name*.

    Like ``load_hooks`` but keeps the contributing plugin's source name —
    useful when the consumer reports which plugin provided what (e.g. for
    collision errors).
    """
    return [(s, i) for s, n, i in _iter_hook_entries() if n == name]


def load_backends():
    """Return a mapping of backend name to class.

    Always includes the built-in ``none`` backend — the default used when
    no other backend is configured. Plugin-provided backends follow; a plugin
    backend reusing an existing name is skipped with an error.
    """
    from ..backends import NoneBackend

    result = {"none": NoneBackend}
    for source, module in _iter_plugin_modules():
        for backend in _load_backends_from_module(module):
            name = getattr(backend, "name")
            if not name:
                continue
            if name in result:
                echo.error(
                    f"backend '{name}' from '{source}' conflicts with "
                    f"an existing backend and is ignored."
                )
                continue
            result[name] = backend
    return result
