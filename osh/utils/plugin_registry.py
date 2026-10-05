"""Plugin registry — stage 1 of the two-stage loading model.

Stage 1 — discovery without import: ``PluginRegistry`` scans plugin
*metadata* only — ``[tool.osh]`` in ``pyproject.toml`` for installed
distributions (or the deprecated ``osh-plugin.toml`` marker), and
``osh-plugin.toml`` files for built-in plugins — and the
loaders register lazy Click stubs (see ``osh.cli_utils.LazyCommand``).
Plugin modules are never evaluated while commands are listed or
``osh --help`` renders.

Stage 2 — import on demand — lives in ``plugin_loader``: a plugin module
is imported the first time one of its contributions is needed — its
command invoked, a handler it extends executed, its runtime selected, or
its backup source scheme used.

``[tool.osh]`` in ``pyproject.toml`` declares a plugin's surface for
stage 1 (the deprecated ``osh-plugin.toml`` marker holds the same keys)::

    [tool.osh]
    description = "Short plugin description."
    extends = ["backup.restore"]          # handlers the plugin extends
    handlers = ["my_plugin.cmd"]          # named non-CLI handlers provided
    depends = ["osh-backup"]              # plugins imported before this one

    [tool.osh.commands]                   # top-level commands
    scan = "Scan things."
    [tool.osh.group_commands.db]          # subcommands of an existing group
    audit = "Audit the db."
    remote = { group = true, help = "Manage remotes." }
    [tool.osh.group_commands.backup]      # subcommands of a new group
    prune = "Prune old backups."
    [tool.osh.runtimes]                   # runtime classes provided
    docker = "Run inside Docker."
    [tool.osh.sources]                    # BackupSource schemes provided
    s3 = "S3 backups."

Declaration values are short help strings, or tables with ``help`` and
(commands only) ``group = true`` for nested ``click.Group``
contributions.

Plugin modules still self-describe on import — ``CommandHandler``
subclasses and ``Runtime``/``BackupSource`` subclasses are discovered
among module attributes — the toml only says *when* the module is worth
importing.

Compatibility: plugins without declarations — an entry point
without a ``:attr`` target — still load eagerly; their
self-describing classes are discovered on import. That eager import is
the cost of the undeclared contract.
"""

import functools
import importlib
import importlib.util
import pkgutil
import re
from dataclasses import dataclass, field
from pathlib import Path

import click

from .. import echo
from ..config import tomllib

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
    (the ``[tool.osh]``/marker contents) and the spec fields; the plugin's
    module is only touched by ``load()``.

    *target_ref* is an importable module path (``"osh.plugins.osh_backup"``,
    ``"osh_aws.cli"``), optionally ``"module:attr"`` for entry-point
    plugins. *lazy* is False for unmarked plugins, which must import
    eagerly because nothing declares their contributions beforehand.
    """

    name: str
    target_ref: str
    kind: str = "builtin"  # "builtin" | "entry_point"
    meta: dict = field(default_factory=dict)
    path: Path | None = None
    legacy_marker: bool = False
    lazy: bool = True
    help_text: str = ""
    version: str = ""
    _module: object | None = None
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
                    raise RuntimeError(f"depends on '{dep}', which is not installed")
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
        from .plugin_loader import _module_commands

        module = self.load()
        if module is None:
            return None
        command = None
        if group is None and ":" in self.target_ref:
            target = getattr(module, self.target_ref.rsplit(":", 1)[1], None)
            if isinstance(target, click.Command):
                command = target
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
        """Populate ``specs`` from built-ins and entry points.

        No plugin module is imported: built-ins are listed with
        ``pkgutil.iter_modules`` and entry points read their
        ``importlib.metadata`` entries (``ep.load()`` is never called).
        """
        self._discover_builtins()
        self._discover_entry_points()
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
            meta, legacy = _ep_meta(ep.value.split(":", 1)[0])
            self._add_spec(
                ep.name,
                ep.value,
                kind="entry_point",
                meta=meta,
                help_text=_ep_summary(ep),
                version=_ep_version(ep),
                legacy_marker=legacy,
            )

    def _add_spec(
        self,
        name,
        target_ref,
        *,
        kind,
        path=None,
        meta=None,
        help_text="",
        version="",
        legacy_marker=False,
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
            legacy_marker=legacy_marker,
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
    (``"pkg.module"`` or ``"pkg.module:attr"``); a plugin declaring
    ``[tool.osh]`` gets the full lazy treatment.
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
    """Return the declared plugin metadata of an installed package.

    Returns ``(meta, legacy)`` — *legacy* marks metadata coming from the
    deprecated ``osh-plugin.toml`` marker; ``[tool.osh]`` in the package's
    ``pyproject.toml`` is the canonical location. Uses
    ``importlib.util.find_spec`` on the top-level package only, so the
    plugin's module is never imported.
    """
    try:
        spec = importlib.util.find_spec(module_name.split(".", 1)[0])
    except (ImportError, ValueError, AttributeError):
        return {}, False
    if spec is None or not spec.origin:
        return {}, False
    package_dir = Path(spec.origin).parent
    meta = _pyproject_meta(package_dir)
    if meta:
        return meta, False
    marker_meta = plugin_meta(package_dir)
    return marker_meta, bool(marker_meta)


def _pyproject_meta(package_dir):
    """Return ``[tool.osh]`` from a ``pyproject.toml`` near *package_dir*.

    Checked at the package dir and two levels up — covering a
    ``pyproject.toml`` shipped inside the package as well as flat and
    ``src/`` layouts in editable installs. The first file declaring
    ``[tool.osh]`` wins.
    """

    for directory in (package_dir, *package_dir.parents[:2]):
        pyproject = directory / "pyproject.toml"
        if not pyproject.is_file():
            continue
        tool = _read_toml(pyproject).get("tool")
        meta = tool.get("osh") if isinstance(tool, dict) else None
        if isinstance(meta, dict):
            return _normalize_meta(meta)
    return {}


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


def plugin_source_name(name):
    """Return a CLI-friendly source identifier from a plugin module/directory name."""
    name = re.sub(r"^osh\.plugins\.", "", name)
    name = re.sub(r"[^a-zA-Z0-9]+", "-", name)
    return name.strip("-") or "plugin"


def plugin_meta(path):
    """Return the metadata dict from a plugin package's ``osh-plugin.toml``."""
    marker = path / PLUGIN_MARKER
    if not marker.is_file():
        return {}
    return _normalize_meta(_read_toml(marker))


@functools.lru_cache(maxsize=256)
def _read_toml(path):
    """Return the parsed TOML dict of *path*; warn and return ``{}`` on failure.

    Results are cached — discovery reads each ``pyproject.toml`` once per
    entry point in a multi-plugin distribution, and the warning fires once
    per file rather than per lookup.
    """
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        echo.warning(f"Could not parse {path}: {exc}", err=True)
        return {}


def _normalize_meta(meta):
    """Return *meta* with the legacy ``[backends]`` table merged into ``[runtimes]``.

    The old section name is deprecated and scheduled for removal in a
    later release. Returns a new dict; the parsed TOML is never mutated —
    it may be shared through the ``_read_toml`` cache.
    """
    backends = meta.get("backends")
    if not isinstance(backends, dict):
        return meta
    meta = dict(meta)
    runtimes = meta.get("runtimes")
    meta["runtimes"] = {**(runtimes if isinstance(runtimes, dict) else {}), **backends}
    del meta["backends"]
    return meta


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
