"""Plugin loader — stage 2 of the two-stage loading model.

Stage 1 — metadata discovery and the plugin registry — lives in
``plugin_registry``. This module resolves *contributions*: it imports a
plugin's module the first time something it provides is needed — its
command invoked, a handler it extends executed, its backend selected, or
its backup source scheme used — and scans the loaded module for
self-described classes (``CommandHandler`` subclasses, ``Backend`` /
``BackupSource`` subclasses, ``@plugin_group``-stamped groups).

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
    PLUGIN_MARKER,
    PluginRegistry,
    PluginSpec,
    _decl_help,
    _decl_hidden,
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

    Lazy plugins contribute ``LazyCommand`` stubs; unmarked (eager) plugins
    contribute the commands discovered in their imported modules.
    """
    commands = []
    for spec in plugin_registry().specs.values():
        if spec.lazy:
            for name, decl in spec.declared_commands().items():
                commands.append((spec.name, _lazy_command(spec, None, name, decl)))
            continue
        module = _load_eager(spec)
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
        module = _load_eager(spec)
        if module is None:
            continue
        for (group, _name), cmd in _module_commands(module).items():
            if group is not None:
                result.setdefault(group, []).append((spec.name, cmd))
    return result


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
    for source, cls in iter_plugin_subclasses(Backend):
        if getattr(cls, "name", None):
            _register_backend(result, source, cls)
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


def iter_plugin_subclasses(base):
    """Yield ``(source, cls)`` for *base* subclasses in loaded plugins.

    Iterating triggers non-lazy plugin imports; lazy plugins contribute
    once loaded — callers that need a specific contribution call
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
    from ..handlers import (
        CommandHandler,
        _declared_name,
        _subcommand_methods,
        _walk_subclasses,
    )

    for cls in _walk_subclasses(CommandHandler):
        if name := _declared_name(cls):
            provided.add(name)
            provided.update(f"{name}.{method}" for method in _subcommand_methods(cls))
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

    is_group = _decl_is_group(decl)

    def loader():
        return spec.resolve_command(group, name)

    cls = LazyGroup if is_group else LazyCommand
    return cls(
        name,
        loader,
        plugin=spec.name,
        short_help=_decl_help(decl),
        hidden=_decl_hidden(decl),
    )


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


def _load_eager(spec):
    """Import an unmarked (non-lazy) plugin, warning on failure."""
    try:
        return spec.load()
    except Exception as exc:
        echo.error(f"Could not load plugin '{spec.name}': {exc}")
        return None


def _iter_plugin_modules():
    """Yield ``(source, module)`` for eager and already-loaded plugins."""
    for spec in plugin_registry().specs.values():
        if spec.lazy and not spec.loaded:
            continue
        module = _load_eager(spec)
        if module is not None:
            yield spec.name, module


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


def _spec_declared_names(spec):
    """Return every handler name *spec* declares in its metadata."""
    names = set(spec.declared_commands())
    for group, decls in spec.declared_group_commands().items():
        names.update(f"{group}.{name}" for name in decls)
    names.update(spec.declared_handlers())
    return names


def _module_commands(module):
    """Return ``{(group|None, name): click.Command}`` discovered in *module*.

    Covers named ``CommandHandler`` subclasses, ``@subcommand`` methods
    on group handlers and ``@plugin_group``-stamped groups.
    """
    from ..cli_utils import method_command
    from ..handlers import (
        CommandHandler,
        _declared_name,
        _nearest_named,
        _subcommand_methods,
    )

    commands = {}
    for cls in _module_subclasses(module, CommandHandler):
        mod = cls.__module__ or ""
        if mod != module.__name__ and not mod.startswith(module.__name__ + "."):
            # Foreign handler classes — imported to subclass or invoke them
            # — are not this plugin's commands.
            continue
        methods = _subcommand_methods(cls)
        declared = _declared_name(cls)
        if methods and (declared is None or "." not in declared):
            # A method-bearing class named ``docker`` contributes its
            # methods as commands of the ``docker`` group (auto-created
            # when needed); an anonymous subclass contributes only the
            # methods its nearest named ancestor does not provide.
            nearest = cls if declared is not None else _nearest_named(cls)
            if nearest is None:
                continue
            group_name = nearest._cli_group or _declared_name(nearest)
            inherited = {} if nearest is cls else _subcommand_methods(nearest)
            for method, attrs in methods.items():
                if method in inherited:
                    continue
                command = method_command(cls, method, attrs, handler=nearest)
                commands.setdefault((group_name, command.name), command)
            continue
        group_name, command = cls.cli_command(module.__name__)
        if command is not None:
            commands.setdefault((group_name, command.name), command)
    for impl in list(vars(module).values()):
        parent = getattr(impl, "_plugin_group", None)
        if parent is not None and isinstance(impl, click.Group):
            commands.setdefault((parent or None, impl.name), impl)
    return commands


def _module_subclasses(module, base):
    """Yield ``base`` subclasses among *module*'s attributes."""
    # Snapshot: resolving a class may import submodules, which mutates the
    # package's attribute dict.
    for impl in list(vars(module).values()):
        if isinstance(impl, type) and impl is not base and issubclass(impl, base):
            yield impl


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
