"""Plugin registry — stage 1 of the two-stage loading model.

Stage 1 — discovery without import: ``PluginRegistry`` scans plugin
*metadata* only — ``osh-plugin.toml`` files for built-in and directory
plugins, entry-point names/values for installed distributions — and the
loaders register lazy Click stubs (see ``osh.cli_utils.LazyCommand``).
Plugin modules are never evaluated while commands are listed or
``osh --help`` renders.

Stage 2 — import on demand — lives in ``plugin_loader``: a plugin module
is imported the first time one of its contributions is needed — its
command invoked, a handler it extends executed, its backend selected, or
its backup source scheme used.

``osh-plugin.toml`` declares a plugin's surface for stage 1::

    description = "Short plugin description."
    extends = ["db.restore"]              # handlers the plugin extends
    handlers = ["my_plugin.cmd"]          # named non-CLI handlers provided
    depends = ["osh-db-get"]              # plugins imported before this one

    [commands]                            # top-level commands
    scan = "Scan things."
    [group_commands.db]                   # subcommands of an existing group
    audit = "Audit the db."
    remote = { group = true, help = "Manage remotes." }
    [group_commands.docker]               # ``osh docker`` lifecycle commands
    init = "Initialise for the docker backend."
    [backends]                            # backend classes provided
    docker = "Run inside Docker."
    [sources]                             # BackupSource schemes provided
    s3 = "S3 backups."

Declaration values are short help strings, or tables with ``help`` and
(commands only) ``group = true`` for nested ``click.Group``
contributions.

Plugin modules still self-describe on import — ``CommandHandler``
subclasses, ``Backend``/``BackupSource`` subclasses and
``@plugin_group``-stamped groups are discovered among module attributes
— the toml only says *when* the module is worth importing.

Compatibility: plugins without ``osh-plugin.toml`` — a bare root package
(``__init__.py``/``osh_plugin.py``) in the user plugin dir, or an entry
point without a ``:attr`` target — still load eagerly; their
self-describing classes are discovered on import. That eager import is
the cost of the undeclared contract.
"""

import importlib
import importlib.util
import os
import pkgutil
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import click

from .. import echo
from ..config import get_enabled_plugins

try:
    import importlib.metadata as _metadata
except ImportError:  # pragma: no cover
    _metadata = None


#: Name of the marker file identifying a plugin package in a repository.
PLUGIN_MARKER = "osh-plugin.toml"


@dataclass
class PluginSpec:
    """Stage-1 metadata for a plugin — enough to register and decide.

    Everything needed to render help and place commands lives in *meta*
    (the ``osh-plugin.toml`` contents) and the spec fields; the plugin's
    module is only touched by ``load()``.

    *target_ref* is an importable module path (``"osh.plugins.osh_db_get"``,
    ``"osh_aws.cli"``), optionally ``"module:attr"`` for entry-point
    plugins, or a filesystem path for directory plugins. *lazy* is False
    for unmarked plugins, which must import eagerly because nothing
    declares their contributions beforehand.
    """

    name: str
    target_ref: str
    kind: str = "user"  # "builtin" | "entry_point" | "user"
    meta: dict = field(default_factory=dict)
    path: Path = None
    prefix: str = "osh_user_plugin"
    lazy: bool = True
    help_text: str = ""
    version: str = ""
    _module: object = None
    _loaded: bool = False
    _loading: bool = False

    @property
    def loaded(self):
        """Whether ``load()`` has already run (successfully or not)."""
        return self._loaded

    def load(self):
        """Stage 2: import the plugin's declared dependencies, then its module.

        Plugins named in ``depends`` are imported first, recursively; an
        unknown, disabled or failing dependency fails this plugin too.
        A ``min_osh`` marker newer than the running osh also fails.
        """
        if not self._loaded:
            try:
                min_osh = str(self.meta.get("min_osh") or "")
                if min_osh and not min_osh_ok(min_osh):
                    from .. import __version__

                    raise RuntimeError(
                        f"requires osh >= {min_osh} (running {__version__})"
                    )
                self._load_dependencies()
                self._module = self._import()
            finally:
                self._loaded = True
        return self._module

    def _load_dependencies(self):
        """Import every plugin named in ``depends`` before this plugin."""
        deps = self.declared_depends()
        if not deps:
            return
        self._loading = True
        try:
            specs = plugin_registry().specs
            for dep in deps:
                spec = specs.get(dep)
                if spec is None:
                    raise RuntimeError(
                        f"depends on '{dep}', which is not installed or enabled"
                    )
                if spec._loading:
                    raise RuntimeError(f"circular dependency on '{dep}'")
                try:
                    module = spec.load()
                except Exception as exc:
                    raise RuntimeError(
                        f"depends on '{dep}', which failed to load: {exc}"
                    ) from exc
                if module is None:
                    raise RuntimeError(f"depends on '{dep}', which failed to load")
        finally:
            self._loading = False

    def _import(self):
        if self.kind == "user":
            return _import_plugin_from_dir(self.path, prefix=self.prefix)
        module_name = self.target_ref.split(":", 1)[0]
        return importlib.import_module(module_name)

    def declared_commands(self):
        """Return the ``{name: declaration}`` of top-level commands."""
        declared = self.meta.get("commands") or {}
        if not declared and self.kind == "entry_point" and not self.meta:
            # Unmarked ``module:attr`` entry points surface as one command
            # named after the entry point; a marker's declarations are
            # authoritative instead.
            declared = {self.name: self.help_text}
        return declared

    def declared_group_commands(self):
        """Return the ``{group: {name: declaration}}`` of subcommands."""
        return self.meta.get("group_commands") or {}

    def declared_backends(self):
        """Return the ``{name: declaration}`` of provided backends."""
        return self.meta.get("backends") or {}

    def declared_sources(self):
        """Return the ``{scheme: declaration}`` of provided backup sources."""
        return self.meta.get("sources") or {}

    def declared_extends(self):
        """Return the handler names the plugin extends."""
        extends = self.meta.get("extends") or []
        return [extends] if isinstance(extends, str) else list(extends)

    def declared_handlers(self):
        """Return the named non-CLI handlers the plugin provides."""
        handlers = self.meta.get("handlers") or []
        return [handlers] if isinstance(handlers, str) else list(handlers)

    def declared_depends(self):
        """Return the source names of plugins to import before this one."""
        depends = self.meta.get("depends") or []
        return [depends] if isinstance(depends, str) else list(depends)

    def resolve_command(self, group, name):
        """Return the real command for *(group, name)*, importing the plugin.

        *group* is ``None`` for top-level commands and the group name for
        subcommands. Returns ``None`` when the plugin does not provide it.
        The resolved command's ``short_help`` is stamped with the declared
        help, so listings show the same text before and after the import.
        """
        from .plugin_loader import _callable_command, _module_commands

        module = self.load()
        if module is None:
            return None
        command = None
        if group is None and ":" in self.target_ref:
            target = getattr(module, self.target_ref.rsplit(":", 1)[1], None)
            if isinstance(target, click.Command):
                command = target
            elif callable(target):
                command = _callable_command(target, name)
        if command is None:
            command = _module_commands(module).get((group, name))
        if command is not None:
            decl = self._command_decl(group, name)
            short = _decl_help(decl)
            if short:
                command.short_help = short
            if _decl_hidden(decl):
                command.hidden = True
        return command

    def _command_decl(self, group, name):
        """Return the declared metadata for *(group, name)*, if any."""
        if group is None:
            return self.declared_commands().get(name)
        return (self.declared_group_commands().get(group) or {}).get(name)


class PluginRegistry:
    """Stage-1 registry of plugin specs, keyed by source name."""

    def __init__(self):
        self.specs = {}
        self._meta_checked = False

    def discover(self):
        """Populate ``specs`` from built-ins, entry points and user dirs.

        No plugin module is imported: built-ins are listed with
        ``pkgutil.iter_modules``, entry points read their
        ``importlib.metadata`` entries (``ep.load()`` is never called), and
        directory plugins are detected by marker files.
        """
        self._discover_builtins()
        self._discover_entry_points()
        self._discover_user_plugins()
        return self

    def _discover_builtins(self):
        try:
            import osh.plugins as plugins_pkg
        except ImportError:
            return
        for _, module_name, _ispkg in pkgutil.iter_modules(
            plugins_pkg.__path__, prefix="osh.plugins."
        ):
            path = Path(plugins_pkg.__path__[0]) / module_name.rsplit(".", 1)[1]
            self._add_spec(
                plugin_source_name(module_name),
                module_name,
                kind="builtin",
                path=path if path.is_dir() else None,
            )

    def _discover_entry_points(self):
        for ep in _iter_entry_points():
            meta = _ep_meta(ep.value.split(":", 1)[0])
            self._add_spec(
                ep.name,
                ep.value,
                kind="entry_point",
                meta=meta,
                help_text=_ep_summary(ep),
                version=_ep_version(ep),
            )

    def _discover_user_plugins(self):
        plugin_dir = user_plugin_dir()
        if not plugin_dir.is_dir():
            return
        for child in sorted(plugin_dir.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            enabled = get_enabled_plugins(child.name)
            # The dir itself may be a plugin package…
            if (child / "__init__.py").is_file() or (child / "osh_plugin.py").is_file():
                if enabled is None or plugin_source_name(child.name) in enabled:
                    self._add_spec(
                        plugin_source_name(child.name),
                        str(child),
                        kind="user",
                        path=child,
                    )
            # …or a repository of marked plugin packages (see _is_plugin_dir).
            prefix = f"osh_user_plugin_{_plugin_name_from_path(child)}"
            for subdir in plugin_subdirs(child):
                if not _is_plugin_dir(subdir):
                    continue
                source = plugin_source_name(subdir.name)
                if enabled is not None and source not in enabled:
                    continue
                self._add_spec(
                    source,
                    str(subdir),
                    kind="user",
                    path=subdir,
                    prefix=prefix,
                )

    def _add_spec(
        self,
        name,
        target_ref,
        *,
        kind,
        path=None,
        prefix="osh_user_plugin",
        meta=None,
        help_text="",
        version="",
    ):
        if name in self.specs:
            echo.error(f"duplicate plugin source '{name}' ignored: {target_ref}")
            return
        if meta is None:
            meta = plugin_meta(path) if path is not None else {}
        if path is not None:
            has_marker = (path / PLUGIN_MARKER).is_file()
        else:
            has_marker = bool(meta)
        if kind == "entry_point":
            lazy = has_marker or ":" in target_ref
        else:
            lazy = has_marker
        version = version or str(meta.get("version") or "")
        if not version and kind == "builtin":
            from .. import __version__

            version = __version__
        self.specs[name] = PluginSpec(
            name=name,
            target_ref=target_ref,
            kind=kind,
            meta=meta,
            path=path,
            prefix=prefix,
            lazy=lazy,
            help_text=help_text or str(meta.get("description", "")),
            version=version,
        )


_REGISTRY = None


def plugin_registry():
    """Return the lazily-built plugin registry singleton."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = PluginRegistry().discover()
    return _REGISTRY


def reset_plugin_registry():
    """Drop the registry singleton so it is rebuilt on next access."""
    global _REGISTRY
    _REGISTRY = None


def _iter_entry_points(group="osh.plugins"):
    """Yield ``importlib.metadata.EntryPoint`` objects — never ``ep.load()``.

    ``ep.name`` is the plugin's source name and ``ep.value`` its target
    (``"pkg.module"`` or ``"pkg.module:attr"``); a plugin whose package
    ships ``osh-plugin.toml`` at its root gets the full lazy treatment.
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
    yield from selected


def _ep_meta(module_name):
    """Return the ``osh-plugin.toml`` metadata of an installed package.

    Uses ``importlib.util.find_spec`` on the top-level package only, so the
    plugin's module is never imported.
    """
    try:
        spec = importlib.util.find_spec(module_name.split(".", 1)[0])
    except (ImportError, ValueError, AttributeError):
        return {}
    if spec is None or not spec.origin:
        return {}
    return plugin_meta(Path(spec.origin).parent)


def _ep_summary(ep):
    """Return the distribution's Summary metadata for an entry point."""
    try:
        dist = getattr(ep, "dist", None)
        if dist is not None:
            return dist.metadata.get("Summary", "") or ""
    except Exception:
        pass
    return ""


def _ep_version(ep):
    """Return the distribution's Version for an entry point."""
    try:
        dist = getattr(ep, "dist", None)
        if dist is not None:
            return dist.version or ""
    except Exception:
        pass
    return ""


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
    if name and name[0].isdigit():
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


def _is_plugin_dir(path):
    """Whether *path* is marked as a plugin package by ``osh-plugin.toml``.

    Checked without importing the package, so unmarked code never
    executes.
    """
    return (path / PLUGIN_MARKER).is_file()


def plugin_meta(path):
    """Return the metadata dict from a plugin dir's ``osh-plugin.toml``."""
    marker = path / PLUGIN_MARKER
    if not marker.is_file():
        return {}
    try:
        from ..config import tomllib

        return tomllib.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def min_osh_ok(requirement):
    """Whether the running osh satisfies a ``min_osh`` marker value.

    An empty or unparsable *requirement* never blocks the plugin — the
    warning for it lives in ``warn_unresolved_meta``.
    """
    required = _version_tuple(requirement)
    if required == (0, 0, 0):
        return True
    from .. import __version__

    return _version_tuple(__version__) >= required


def _version_tuple(text):
    """Parse ``"1.2.3+local"`` into a ``(1, 2, 3)`` tuple for comparison."""
    match = re.match(r"\d+(?:\.\d+)*", str(text))
    parts = [int(p) for p in match.group(0).split(".")] if match else []
    return tuple(parts + [0] * (3 - len(parts)))


def _decl_help(decl):
    """Return the short help string from a declaration value."""
    if isinstance(decl, dict):
        return str(decl.get("help") or "")
    return str(decl or "")


def _decl_is_group(decl):
    """Whether a command declaration marks a nested ``click.Group``."""
    return isinstance(decl, dict) and bool(decl.get("group"))


def _decl_hidden(decl):
    """Whether a command declaration marks the command as hidden."""
    return isinstance(decl, dict) and bool(decl.get("hidden"))
