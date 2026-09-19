"""Plugin loader — stage 2 of the two-stage loading model.

Stage 1 — metadata discovery and the plugin registry — lives in
``plugin_registry``. This module resolves *contributions*: it imports a
plugin's module the first time something it provides is needed — its
command invoked, a handler it extends executed, its backend selected, or
its backup source scheme used — and scans the loaded module for
self-described classes (``CommandHandler`` subclasses, ``Backend`` /
``BackupSource`` subclasses, ``@plugin_group``-stamped groups, legacy
``_extends`` mixins).

Command/handler semantics live with the classes themselves —
``CommandHandler.cli_command()`` derives placement from ``_cli_name``,
``CommandHandler.effective()`` composes extensions. The loader only
decides *when* a module is worth importing and *what* attributes it
exposes.

``load_plugins()`` returns ``(source, command)`` pairs so callers can
resolve command-name collisions by prefixing the command with its plugin
source.
"""

import click

from .. import echo

# Re-exported: the discovery machinery moved to ``plugin_registry`` but
# remains reachable here for existing imports.
from .plugin_registry import (  # noqa: F401
    _BACKEND_SECTION,
    PLUGIN_MARKER,
    PluginRegistry,
    PluginSpec,
    _decl_help,
    _decl_is_group,
    _is_plugin_dir,
    plugin_meta,
    plugin_registry,
    plugin_source_name,
    plugin_subdirs,
    reset_plugin_registry,
    user_plugin_dir,
)


def ensure_declared(meta_key, name=None):
    """Import lazy plugins declaring *meta_key*, optionally entry *name*.

    This is the stage-2 trigger for non-command contributions: composing
    handler *name* imports plugins listing it under ``extends``; resolving
    backend *name* or source scheme *name* imports the plugins declaring it.
    """

    def wanted(spec):
        declared = spec.meta.get(meta_key)
        if not declared:
            return False
        if name is None:
            return True
        if isinstance(declared, dict):
            return name in declared
        declared = declared if isinstance(declared, list) else [declared]
        return name in declared

    _ensure_specs(wanted)


def ensure_handler(name):
    """Import the lazy plugin declaring handler/command *name*.

    Maps qualified handler names to declared metadata: ``db.get`` →
    ``[group_commands.db] get``, ``scan`` → ``[commands] scan``, and
    ``my_plugin.cmd`` → ``handlers``. Lets ``resolve(name)`` find
    handlers whose plugin has not been imported yet.
    """
    head, _, tail = name.partition(".")

    def wanted(spec):
        if name in spec.declared_commands() or name in spec.declared_handlers():
            return True
        return tail in (spec.declared_group_commands().get(head) or {})

    _ensure_specs(wanted)


# Deprecated alias — the pre-handler vocabulary.
ensure_operation = ensure_handler


def declared_meta(meta_key):
    """Merge ``{name: help}`` declarations of *meta_key* across all specs."""
    result = {}
    for spec in plugin_registry().specs.values():
        declared = spec.meta.get(meta_key)
        if not isinstance(declared, dict):
            continue
        for name, decl in declared.items():
            result.setdefault(name, _decl_help(decl))
    return result


def load_plugins():
    """Return ``(source, command)`` pairs for all registered plugins.

    Lazy plugins contribute ``LazyCommand`` stubs; legacy plugins contribute
    the commands discovered in their (eagerly imported) modules.
    """
    commands = []
    for spec in plugin_registry().specs.values():
        if spec.lazy:
            for name, decl in spec.declared_commands().items():
                commands.append((spec.name, _lazy_command(spec, None, name, decl)))
            continue
        module = _load_legacy(spec)
        if module is None:
            continue
        for (group, _name), cmd in _module_commands(module).items():
            if group is None:
                commands.append((spec.name, cmd))
    return commands


def load_group_commands():
    """Return ``{group_name: [(source, command)]}`` for all plugins."""
    result = {}
    for spec in plugin_registry().specs.values():
        if spec.lazy:
            for group_name, decls in spec.declared_group_commands().items():
                for name, decl in decls.items():
                    result.setdefault(group_name, []).append(
                        (spec.name, _lazy_command(spec, group_name, name, decl))
                    )
            continue
        module = _load_legacy(spec)
        if module is None:
            continue
        for (group, _name), cmd in _module_commands(module).items():
            if group is not None:
                result.setdefault(group, []).append((spec.name, cmd))
    return result


def load_backend_commands():
    """Return ``(source, group)`` pairs for plugin backend command groups.

    Each group renders ``osh <name>`` lifecycle commands (``init``,
    ``activate``, ``stop``), built post-import by ``Backend.get_cli_group()``
    — the default calls ``backend_group(cls)`` — or declared by the
    deprecated ``backend_commands`` manifest key.
    """
    commands = []
    for spec in plugin_registry().specs.values():
        if spec.lazy:
            for name, decl in spec.declared_backend_commands().items():
                commands.append(
                    (
                        spec.name,
                        _lazy_command(spec, _BACKEND_SECTION, name, decl),
                    )
                )
            continue
        module = _load_legacy(spec)
        if module is None:
            continue
        for _name, group in _module_backend_groups(module).items():
            commands.append((spec.name, group))
    return commands


def get_backend_class(name):
    """Return the backend class registered as *name*, or ``None``.

    Only the plugin declaring *name* is imported — resolving the ``venv``
    backend never touches the ``docker`` plugin.
    """
    from ..backends import Backend, NoneBackend

    if name == "none":
        return NoneBackend
    ensure_declared("backends", name)
    for _source, cls in iter_plugin_subclasses(Backend):
        if getattr(cls, "name", None) == name:
            return cls
    return None


def backend_meta():
    """Return ``{name: description}`` for all backends — without importing.

    Combines ``[backends]`` declarations with classes already loaded, so
    help sections can render without evaluating backend plugins.
    """
    from ..backends import Backend

    meta = {"none": "Run on the host (default)."}
    for name, desc in declared_meta("backends").items():
        meta.setdefault(name, desc)
    for _source, cls in _iter_loaded_subclasses(Backend):
        name = getattr(cls, "name", None)
        if name:
            meta[name] = getattr(cls, "description", "") or meta.get(name, "")
    return meta


def load_backends():
    """Return a mapping of backend name to class, importing all declarers.

    Always includes the built-in ``none`` backend. For resolving the
    project's active backend prefer ``get_backend_class(name)``, which
    imports only the plugin providing it.
    """
    from ..backends import Backend, NoneBackend

    ensure_declared("backends")
    result = {"none": NoneBackend}
    seen = set()
    for source, cls in iter_plugin_subclasses(Backend):
        if getattr(cls, "name", None):
            seen.add(id(cls))
            _register_backend(result, source, cls)
    for source, module in _iter_plugin_modules():
        for backend in _load_backends_from_module(module):
            if id(backend) in seen:
                continue
            _register_backend(result, source, backend)
    return result


def get_source_class(scheme):
    """Return the ``BackupSource`` class for *scheme*, or ``None``.

    Only the plugin declaring *scheme* is imported.
    """
    from ..backup_sources import BackupSource

    ensure_declared("sources", scheme)
    found = None
    for source, cls in iter_plugin_subclasses(BackupSource):
        if getattr(cls, "scheme", None) != scheme:
            continue
        if found is None:
            found = cls
        else:
            echo.error(
                f"backup source '{scheme}' from '{source}' conflicts with "
                "an existing source and is ignored."
            )
    return found


def source_meta():
    """Return ``{scheme: description}`` for backup sources — no imports."""
    from ..backup_sources import BackupSource

    meta = dict(declared_meta("sources"))
    for _source, cls in _iter_loaded_subclasses(BackupSource):
        scheme = getattr(cls, "scheme", None)
        if scheme:
            meta[scheme] = (
                getattr(cls, "description", "")
                or (cls.__doc__ or "").strip().split("\n")[0]
            )
    return meta


def load_extensions(name=None):
    """Aggregate legacy ``_extends`` extension mixins across all plugins.

    With *name*, return the list of mixin classes marked for that
    handler name, in plugin load order. Without it, return the full
    ``{handler_name: [classes]}`` dict.
    """
    result = {}
    for _source, op_name, impl in _iter_extension_entries(name):
        result.setdefault(op_name, []).append(impl)
    return result if name is None else result.get(name, [])


def load_extension_entries(name):
    """Return ``(source, impl)`` pairs for handler *name*.

    Like ``load_extensions`` but keeps the contributing plugin's source
    name, for diagnostics.
    """
    return [(s, i) for s, n, i in _iter_extension_entries(name) if n == name]


def iter_plugin_subclasses(base):
    """Yield ``(source, cls)`` for *base* subclasses in loaded plugins.

    Iterating triggers legacy (non-lazy) plugin imports; lazy plugins
    contribute once loaded — callers that need a specific contribution call
    ``ensure_declared`` first.
    """
    seen = set()
    for source, module in _iter_plugin_modules():
        for cls in _module_subclasses(module, base):
            if id(cls) not in seen:
                seen.add(id(cls))
                yield source, cls


def warn_unresolved_meta():
    """Warn about ``extends`` targets and ``depends`` names nothing provides.

    Runs once per registry, after eager plugins have loaded — the CLI
    calls it after assembling commands — so handler names provided by
    non-lazy plugins are known. Lazy plugins contribute their declared
    names and are never imported. Without this check a plugin extending
    a missing handler would simply never load, with no explanation.
    """
    registry = plugin_registry()
    if registry._meta_checked:
        return
    registry._meta_checked = True
    provided = set()
    for spec in registry.specs.values():
        provided.update(_spec_declared_names(spec))
    from ..handlers import CommandHandler, _declared_name, _walk_subclasses

    for cls in _walk_subclasses(CommandHandler):
        if name := _declared_name(cls):
            provided.add(name)
    for spec in registry.specs.values():
        for target in spec.declared_extends():
            if target not in provided:
                echo.warning(
                    f"plugin '{spec.name}' extends '{target}', which no "
                    "installed plugin provides; it will never be imported.",
                    err=True,
                )
        for dep in spec.declared_depends():
            if dep not in registry.specs:
                echo.warning(
                    f"plugin '{spec.name}' depends on '{dep}', which is "
                    "not installed or enabled.",
                    err=True,
                )


def _lazy_command(spec, group, name, decl):
    """Build the click stub delegating to *(group, name)* in *spec*."""
    from ..cli_utils import LazyCommand, LazyGroup

    is_group = group == _BACKEND_SECTION or _decl_is_group(decl)

    def loader():
        return spec.resolve_command(group, name)

    cls = LazyGroup if is_group else LazyCommand
    return cls(name, loader, plugin=spec.name, short_help=_decl_help(decl))


def _callable_command(func, name):
    """Wrap a plain ``func(argv)`` callable as a Click command."""

    @click.command(
        name=name,
        context_settings={
            "ignore_unknown_options": True,
            "allow_extra_args": True,
        },
    )
    @click.argument("args", nargs=-1)
    @click.pass_context
    def command(ctx, args):
        return func([*args, *ctx.args])

    return command


def _ensure_specs(predicate):
    """Import each not-yet-loaded lazy spec matching *predicate*."""
    for spec in plugin_registry().specs.values():
        if spec.loaded or not spec.lazy or not predicate(spec):
            continue
        try:
            spec.load()
        except Exception as exc:
            echo.error(f"Could not load plugin '{spec.name}': {exc}")


def _load_legacy(spec):
    """Import a legacy (non-lazy) plugin, warning on failure or manifest."""
    try:
        module = spec.load()
    except Exception as exc:
        echo.error(f"Could not load plugin '{spec.name}': {exc}")
        return None
    if module is not None:
        _warn_manifest(spec.name, module)
    return module


def _iter_plugin_modules():
    """Yield ``(source, module)`` for legacy and already-loaded plugins."""
    for spec in plugin_registry().specs.values():
        if spec.lazy and not spec.loaded:
            continue
        module = _load_legacy(spec)
        if module is not None:
            yield spec.name, module


def _iter_plugin_attrs():
    """Yield ``(source, obj)`` for every module-level attribute in plugins."""
    for source, module in _iter_plugin_modules():
        for impl in vars(module).values():
            yield source, impl


def _iter_extension_entries(op_name=None):
    """Yield ``(source, name, impl)`` for legacy ``_extends`` mixins.

    A class becomes a handler extension by carrying the ``_extends``
    marker — set by the deprecated ``osh.operations.extends`` or declared
    directly as ``_extends = "handler.name"``. With *op_name*, plugins
    declaring it under ``extends`` are imported first, so composition
    loads only the plugins that extend the handler being executed.
    """
    ensure_declared("extends", op_name)
    seen = set()
    for source, impl in _iter_plugin_attrs():
        names = getattr(impl, "_extends", None)
        if not names:
            continue
        if isinstance(names, str):
            names = (names,)
        for name in names:
            if (name, id(impl)) not in seen:
                seen.add((name, id(impl)))
                yield source, name, impl


def _iter_loaded_subclasses(base):
    """Yield ``(source, cls)`` over already-loaded plugin modules only."""
    seen = set()
    for spec in plugin_registry().specs.values():
        if not spec.loaded or spec._module is None:
            continue
        for cls in _module_subclasses(spec._module, base):
            if id(cls) not in seen:
                seen.add(id(cls))
                yield spec.name, cls


def _promised_handlers():
    """Return handler names lazy plugins promise via their declarations."""
    promised = set()
    for spec in plugin_registry().specs.values():
        if not spec.lazy or spec.loaded:
            continue
        promised.update(_spec_declared_names(spec))
    return promised


def _spec_declared_names(spec):
    """Return every handler name *spec* declares in its metadata."""
    names = set(spec.declared_commands())
    for group, decls in spec.declared_group_commands().items():
        names.update(f"{group}.{name}" for name in decls)
    names.update(spec.declared_handlers())
    return names


def _module_commands(module):
    """Return ``{(group|None, name): click.Command}`` discovered in *module*.

    Covers named ``CommandHandler`` subclasses, ``@plugin_group``-stamped
    groups and the deprecated ``commands``/``group_commands`` manifest keys.
    """
    from ..handlers import CommandHandler

    commands = {}
    for cls in _module_subclasses(module, CommandHandler):
        mod = cls.__module__ or ""
        if mod != module.__name__ and not mod.startswith(module.__name__ + "."):
            # Foreign handler classes — imported to subclass or invoke them
            # — are not this plugin's commands.
            continue
        group_name, command = cls.cli_command(module.__name__)
        if command is not None:
            commands.setdefault((group_name, command.name), command)
    for impl in list(vars(module).values()):
        parent = getattr(impl, "_plugin_group", None)
        if parent is not None and isinstance(impl, click.Group):
            commands.setdefault((parent or None, impl.name), impl)
    for cmd in _load_commands_from_module(module):
        commands.setdefault((None, cmd.name), cmd)
    for group_name, cmds in _load_group_commands_from_module(module).items():
        for cmd in cmds:
            commands.setdefault((group_name, cmd.name), cmd)
    return commands


def _module_backend_groups(module):
    """Return ``{name: click.Group}`` backend command groups from *module*."""
    groups = {}
    for cls in _module_backends(module):
        group = cls.get_cli_group()
        if isinstance(group, click.Group):
            groups.setdefault(group.name, group)
    for group in _load_backend_commands_from_module(module):
        groups.setdefault(group.name, group)
    return groups


def _module_subclasses(module, base):
    """Yield ``base`` subclasses among *module*'s attributes."""
    # Snapshot: resolving a class may import submodules, which mutates the
    # package's attribute dict.
    for impl in list(vars(module).values()):
        if isinstance(impl, type) and impl is not base and issubclass(impl, base):
            yield impl


def _module_backends(module):
    """Yield named ``Backend`` subclasses among *module*'s attributes."""
    from ..backends import Backend

    for cls in _module_subclasses(module, Backend):
        if getattr(cls, "name", None):
            yield cls


def _declares_manifest(module):
    """Whether the module declares ``OSH_PLUGIN_MANIFEST`` — even an empty one.

    Deprecated: kept for backwards compatibility while manifests are still
    honored (see ``_warn_manifest``).
    """
    return isinstance(getattr(module, "OSH_PLUGIN_MANIFEST", None), dict)


_WARNED_MANIFESTS = set()


def _warn_manifest(source, module):
    """Warn once per plugin still declaring ``OSH_PLUGIN_MANIFEST``."""
    if not _declares_manifest(module) or module.__name__ in _WARNED_MANIFESTS:
        return
    _WARNED_MANIFESTS.add(module.__name__)
    echo.warning(
        f"plugin '{source}' declares OSH_PLUGIN_MANIFEST, which is "
        f"deprecated. Declare the plugin's surface in {PLUGIN_MARKER} and "
        "let commands, backends and sources self-describe on import."
    )


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


def _load_backend_commands_from_module(module):
    """Return Click groups declared as backend commands by a plugin module."""
    commands = _plugin_manifest(module).get("backend_commands", [])
    if not isinstance(commands, list):
        commands = [commands]
    return [cmd for cmd in commands if isinstance(cmd, click.Group)]


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


def _register_backend(result, source, backend):
    """Register *backend* in *result* unless its name is taken."""
    name = backend.name
    if name in result:
        echo.error(
            f"backend '{name}' from '{source}' conflicts with "
            f"an existing backend and is ignored."
        )
        return
    result[name] = backend
