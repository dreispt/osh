"""Command handler classes and the subclass-based extension model.

Every extensible command delegates its behaviour to a *handler class* —
a ``CommandHandler`` subclass named by ``_cli_name`` (e.g.
``"db.restore"``). The generated Click command is a thin shell: it
parses ``get_options()`` and delegates to the handler::

    DbRestore(ctx, dump="prod.dump").run()

Plugins extend a handler in place simply by *subclassing* it — a
subclass without its own ``_cli_name`` is an extension of its nearest
named ancestor::

    class DanglingFilestores(Db):
        def extra_sections(self):
            lines = list(super().extra_sections())
            ...
            return lines

A named handler whose methods are marked ``@subcommand`` is a command
*group*: each method is one subcommand, its ``--help`` body the method
docstring and its parameters the method's click decorators (plus a
``<method>_options()`` hook for dynamic params). Extending a group
subcommand is subclassing the group class and overriding the method —
``extends = ["db.list"]`` in ``osh-plugin.toml`` keeps the lazy trigger
at subcommand granularity.

Instantiating a named handler returns an instance of its *effective*
class — the handler plus every loaded extension, layered in plugin
discovery order (later plugins outermost, reached through ``super()``).
When nothing extends it, the literal class is instantiated — no
composition machinery runs.

When the target class is not importable — e.g. another plugin's handler
— ``resolve("name")`` imports the declaring plugin and returns the
class::

    class Mine(resolve("db.restore")):
        ...
"""

import functools

from . import echo

#: Param names that may not collide with handler machinery.
_RESERVED_PARAMS = frozenset({"env", "ctx"})


def subcommand(func=None, **attrs):
    """Mark a ``CommandHandler`` method as a CLI subcommand.

    Used on the methods of a *group* handler — a class whose
    ``_cli_name`` names a command group (``db``). Each marked method
    becomes one subcommand; its ``--help`` body is the method docstring
    and its parameters are ordinary ``@click.option``/``@click.argument``
    decorators stacked below this one::

        @subcommand
        @click.option("--all", "show_all", is_flag=True)
        def list(self):
            ...

    *attrs* customize the generated command: ``name`` (defaults to the
    method name), ``context_settings`` and ``hidden``. For dynamic
    parameters a ``<method>_options()`` classmethod hook is consulted —
    see ``osh.cli_utils.handler_group``.
    """
    if func is None:
        return functools.partial(subcommand, **attrs)
    func._cli_subcommand = attrs
    return func


class CommandHandler:
    """Base class for command handlers.

    A handler implements the behaviour behind a CLI command. It is bound
    to an :class:`Env`: ``self.env`` is the environment and ``self.ctx``
    its Click context. The parsed parameters become instance attributes
    (``self.show_all``, ``self.dry_run``, ...) — set via the constructor
    or ``op(**params)``. ``run()`` is the entry point; handlers
    decompose their behaviour into methods so extensions can override
    any step, and they can delegate to other handlers by instantiating
    them directly or through ``resolve("name")``.

    Declaring ``_cli_name`` in the class's own body makes a handler
    *named*: it becomes an extension target and a CLI command. The
    dotted name carries the placement: ``db.restore`` becomes the
    ``restore`` command of the ``db`` group; a plain name registers a
    top-level command. A name with a ``_``-prefixed segment (``_util.fmt``,
    ``db._fmt``) is programmatic-only — no command is generated; declare
    it under ``handlers`` in ``osh-plugin.toml`` so ``resolve()`` finds
    it. The ``_cli_*`` class attributes customize that wiring:

    - ``_cli_name``: qualified name — identity, extension target and CLI
      placement (``<group>.<command>``).
    - ``_cli_group``: target group override (``""`` forces top level).
    - ``_cli_context_settings``: dict passed to the ``click.Command``.
    - ``_cli_hidden``: truthy hides the command from ``--help`` listings.

    Help text has two sources with disjoint roles: the plugin's
    ``osh-plugin.toml`` declaration is the short description shown in
    command listings, and the class docstring is the command's
    ``--help`` body.

    Subclassing a named handler *without* declaring a ``_cli_name``
    extends it in place — see :meth:`effective`.
    """

    _cli_name = None
    _cli_group = None
    _cli_context_settings = None
    _cli_hidden = False

    def __new__(cls, *args, **kwargs):
        # Instantiating a named handler yields its effective class — the
        # handler plus loaded extensions. Composed classes and anonymous
        # subclasses declare no name of their own, so no recursion.
        if _declared_name(cls):
            cls = cls.effective()
        return super().__new__(cls)

    def __init__(self, env=None, **params):
        if not isinstance(env, Env):
            env = Env(env)
        self.env = env
        self.ctx = env.ctx
        self._set_params(params)

    def __call__(self, **params):
        """Set command params as instance attributes; return self."""
        self._set_params(params)
        return self

    @classmethod
    def get_options(cls):
        """Return the ``click.Parameter``s of the generated CLI command.

        By default the parameters are the ``@click.option`` /
        ``@click.argument`` decorators stacked on ``run()``, merged over
        the MRO (base class first). Extension point — subclasses override
        this and append to ``super().get_options()`` when parameters are
        dynamic, e.g. backend-provided options.
        """
        params = []
        for klass in reversed(cls.__mro__):
            method = vars(klass).get("run")
            # Decorators append bottom-up; click.Command expects the
            # top-down declaration order — same as click.command() does.
            params.extend(reversed(getattr(method, "__click_params__", None) or []))
        return params

    @classmethod
    def format_cli_help(cls, formatter):
        """Write extra help sections after the command's ``--help`` body."""

    def run(self):
        """Entry point — subclasses implement the command behaviour."""
        raise NotImplementedError

    @classmethod
    def effective(cls):
        """Return the effective handler class — *cls* plus its extensions.

        Plugins declaring the handler's name under ``extends`` in
        ``osh-plugin.toml`` are imported first. Every loaded
        ``CommandHandler`` subclass without its own ``_cli_name`` extends
        its nearest named ancestor, in plugin discovery order. Returns
        *cls* itself when nothing extends it.
        """
        from .utils.plugin_loader import (
            _iter_plugin_modules,
            _module_subclasses,
            ensure_declared,
        )

        name = _declared_name(cls)
        if name:
            ensure_declared("extends", name)

        exts = []
        seen = set()
        for _source, module in _iter_plugin_modules():
            for sub in _module_subclasses(module, CommandHandler):
                if (
                    sub is cls
                    or _declared_name(sub)
                    or id(sub) in seen
                    or not issubclass(sub, cls)
                    or _nearest_named(sub) is not cls
                ):
                    continue
                seen.add(id(sub))
                exts.append(sub)

        if not exts:
            return cls
        cached = cls.__dict__.get("_effective")
        if (
            cached is not None
            and len(cached[0]) == len(exts)
            and all(e is ce for e, ce in zip(exts, cached[0]))
        ):
            return cached[1]

        composed = cls
        for ext in exts:
            try:
                composed = type(
                    f"{cls.__name__}__{ext.__name__}",
                    (ext, composed),
                    {"__module__": cls.__module__},
                )
            except TypeError as exc:
                echo.error(
                    f"extension for '{name or cls.__name__}' could not be "
                    f"composed: {exc}; ignored."
                )
        cls._effective = (tuple(exts), composed)
        return composed

    @classmethod
    def cli_command(cls, source=None):
        """Return ``(group_name, click.Command)`` for this named handler.

        *group_name* is ``None`` for top-level commands. Placement
        derives from the dotted ``_cli_name`` (``db.restore`` → group
        ``db``, command ``restore``); ``_cli_group`` overrides it (``""``
        forces top level). A class with ``@subcommand`` methods produces
        a ``click.Group`` — ``db.remote`` becomes the ``remote`` subgroup
        of ``db``. Returns ``(None, None)`` when the handler is
        unnamed or ``_``-named (programmatic-only).
        """
        from .cli_utils import handler_command, handler_group

        name = _declared_name(cls)
        if name is None:
            return None, None
        if any(part.startswith("_") for part in name.split(".")):
            return None, None
        head, sep, tail = name.partition(".")
        group = cls._cli_group
        if group is None:
            group = head if sep else None
        elif not group:
            group = None
        cmd_name = tail if sep else head
        if "." in cmd_name:
            echo.error(
                f"plugin '{source}' handler '{name}' cannot derive a "
                "command name; use a '<group>.<command>' name."
            )
            return None, None
        if _subcommand_methods(cls):
            # A class with ``@subcommand`` methods is itself the group.
            return group, handler_group(cmd_name, cls)
        return group, handler_command(cmd_name, cls)

    def _set_params(self, params):
        """Set each param as an instance attribute.

        Param names must not shadow the handler machinery: names
        starting with ``_`` — which cover every ``_cli_*`` attribute —
        reserved attributes (``env``, ``ctx``) and
        any name bound to a method, property or other callable class
        attribute are rejected with ``TypeError``.
        """
        for key, value in params.items():
            attr = getattr(type(self), key, None)
            if (
                key.startswith("_")
                or key in _RESERVED_PARAMS
                or callable(attr)
                or isinstance(attr, property)
            ):
                raise TypeError(
                    f"handler parameter '{key}' conflicts with a reserved name"
                )
            setattr(self, key, value)


class Env:
    """Per-invocation environment binding a Click context to handlers.

    ``env["db.restore"]`` or ``env[DbRestore]`` returns a bound handler
    instance — its ``self.env`` and ``self.ctx`` point back to it.
    Instantiation resolves extensions transparently, so lookups always
    yield the effective class. Call the instance with the command's
    params to configure it, then ``run()``.
    """

    def __init__(self, ctx):
        self.ctx = ctx.ctx if isinstance(ctx, Env) else ctx

    def __getitem__(self, key):
        """Return a bound handler instance — *key* is a name or class."""
        cls = key if isinstance(key, type) else resolve(key)
        return cls(self)


def resolve(name):
    """Return the handler class declared as *name* — the lazy bridge.

    Imports the plugin declaring *name* when needed, then finds the
    class in the ``CommandHandler`` tree — the class hierarchy itself is
    the lookup, not a registration dict. Use it to subclass or invoke a
    handler that cannot be imported directly (e.g. another plugin's).

    A *name* naming a group subcommand (``db.list``) with no handler of
    its own resolves to the group class (``Db``), whose effective class
    owns the method — ``resolve("db.list")`` and ``resolve("db")`` are
    the same class.
    """
    from .utils.plugin_loader import ensure_handler

    ensure_handler(name)
    found = None
    for cls in _resolve_candidates():
        if _declared_name(cls) != name:
            continue
        if found is not None:
            echo.error(
                f"handler '{name}' is declared more than once; ignoring "
                f"{cls.__module__}.{cls.__name__}."
            )
            continue
        found = cls
    if found is not None:
        return found
    head, _, method = name.rpartition(".")
    if head and method:
        group_cls = resolve(head)
        if method in _subcommand_methods(group_cls):
            return group_cls
    raise KeyError(f"No handler registered as '{name}'.")


def _declared_name(cls):
    """Return *cls*'s own declared handler name, or ``None``.

    Only ``_cli_name`` set in the class's own ``__dict__`` counts — a
    subclass inheriting its parent's name is an *extension* of the
    parent, not a new handler.
    """
    return cls.__dict__.get("_cli_name")


def _nearest_named(cls):
    """Return the nearest ancestor declaring a handler name, or ``None``."""
    for base in cls.__mro__[1:]:
        if _declared_name(base):
            return base
    return None


def _subcommand_methods(cls):
    """Return ``{method_name: marker_attrs}`` for *cls*'s subcommand methods.

    Iterates the MRO base-first so inherited methods keep their
    definition order; a method marked again in a subclass keeps its
    position but takes the most-derived marker attributes.
    """
    methods = {}
    for klass in reversed(cls.__mro__):
        for name, member in vars(klass).items():
            mark = getattr(member, "_cli_subcommand", None)
            if mark is not None:
                methods[name] = mark
    return methods


def _resolve_candidates():
    """Yield ``CommandHandler`` subclasses, core commands included.

    Core handlers live in ``osh.commands`` modules which may not be
    imported yet when ``resolve`` runs standalone (e.g. a single command
    module imported directly) — importing the package is a no-op when
    the CLI already loaded it.
    """
    try:
        from . import commands as _commands  # noqa: F401
    except ImportError:
        pass
    yield from _walk_subclasses(CommandHandler)


def _walk_subclasses(base):
    """Yield every class in *base*'s subclass tree (loaded classes only)."""
    for sub in base.__subclasses__():
        yield sub
        yield from _walk_subclasses(sub)
