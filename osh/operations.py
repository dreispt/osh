"""Command operation classes and the extension registry.

Every extensible command delegates its behaviour to an *operation class*
registered under a stable name (e.g. ``"db.list"``). The Click command
function is a thin wrapper: it declares the CLI parameters and delegates to
``registry[<name>]`` for the effective operation class.

Plugins extend an operation in place — Odoo ``_inherit``-style — by
marking mixin classes with the :func:`extends` decorator, so no manifest
entry is needed::

    @extends("db.list")
    class ExtraSection:
        def extra_sections(self):
            lines = list(super().extra_sections())
            ...
            return lines

``registry[<name>]`` layers the mixins onto the registered base class in
plugin load order: each successive mixin is outermost, so a later plugin
overrides earlier ones and reaches them through ``super()``. Extension
methods must call ``super()`` to preserve the rest of the chain.

An :class:`Env` binds a Click context to the registry — ``env["db.list"]``
returns a bound operation *instance* (the Odoo ``env["model.name"]``
equivalent), ready to be configured with params and run::

    Env(ctx)["db.list"](show_all=True).run()
"""

from collections.abc import Mapping

from . import echo
from .utils.plugin_loader import _iter_extension_entries

_OPERATIONS = {}
_COMPOSED = {}
_WARNED_UNKNOWN = set()
_RESERVED_PARAMS = frozenset({"env", "ctx", "operation_name"})


class Operation:
    """Base class for command operations.

    Operations are bound to an :class:`Env`: ``self.env`` is the
    environment and ``self.ctx`` its Click context. The parsed parameters
    become instance attributes (``self.show_all``, ``self.dry_run``, ...)
    — set via ``env[<name>](**params)`` or the constructor. ``run()`` is
    the entry point; operations decompose their behaviour into methods so
    extensions can override any step, and they can delegate to other
    operations through ``self.env["other.op"](...)``.
    """

    operation_name = None

    def __init__(self, env, **params):
        self.env = env
        self.ctx = env.ctx
        self._set_params(params)

    def __call__(self, **params):
        """Set command params as instance attributes; return self."""
        self._set_params(params)
        return self

    def run(self):
        """Entry point — subclasses implement the command behaviour."""
        raise NotImplementedError

    def _set_params(self, params):
        """Set each param as an instance attribute.

        Param names must not shadow the operation machinery: names
        starting with ``_``, reserved attributes (``env``, ``ctx``,
        ``operation_name``) and any name bound to a method, property or
        other callable class attribute are rejected with ``TypeError``.
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
                    f"operation parameter '{key}' conflicts with a reserved name"
                )
            setattr(self, key, value)


class Env:
    """Per-invocation environment binding a Click context to the registry.

    ``env[<name>]`` returns a fresh operation instance bound to this env —
    its ``self.env`` and ``self.ctx`` point back to it. Call the instance
    with the command's params to configure it, then ``run()``.
    """

    def __init__(self, ctx):
        self.ctx = ctx

    def __getitem__(self, name):
        return registry[name](self)


def extends(*names):
    """Class decorator marking an operation extension mixin.

    The decorated class is layered onto each operation registered under
    *names* — Odoo ``_inherit``-style — so no manifest entry is needed::

        @extends("db.list")
        class DanglingFilestores:
            ...

    The decorator stamps the ``_extends`` marker on the class; the
    plugin loader discovers marked classes among each plugin's
    module-level attributes.
    """

    def decorator(cls):
        existing = getattr(cls, "_extends", ())
        if isinstance(existing, str):
            existing = (existing,)
        cls._extends = (*existing, *names)
        return cls

    return decorator


def operation(name):
    """Class decorator registering an operation class under *name*."""

    def decorator(cls):
        if name in _OPERATIONS:
            echo.error(f"operation '{name}' is already registered; overriding.")
        cls.operation_name = name
        _OPERATIONS[name] = cls
        return cls

    return decorator


class _OperationRegistry(Mapping):
    """Dict-style lookup of effective operation classes.

    ``registry["db.list"]`` returns the composed operation class for that
    name — the Odoo ``env["model.name"]`` equivalent. Membership tests and
    iteration cover the registered operation names. Composed classes are
    cached per (base, extensions) pair, so repeated lookups return the
    same class object.
    """

    def __getitem__(self, name):
        """Return the composed operation class for *name*."""
        base = _OPERATIONS.get(name)
        if base is None:
            raise KeyError(f"No operation registered as '{name}'.")

        entries = list(_iter_extension_entries())
        _warn_unknown_operations(entries)
        exts = [(s, e) for s, n, e in entries if n == name]
        ext_classes = [e for _, e in exts]

        # Cache by identity — extension objects may be unhashable.
        cached = _COMPOSED.get(name)
        if (
            cached is not None
            and cached[0] is base
            and len(cached[1]) == len(ext_classes)
            and all(e is ce for e, ce in zip(ext_classes, cached[1]))
        ):
            return cached[2]

        cls = base
        for source, ext in exts:
            if not isinstance(ext, type):
                echo.error(
                    f"'{source}' contributed a non-class extension for "
                    f"operation '{name}'; ignored."
                )
                continue
            try:
                cls = type(
                    f"{base.__name__}__{ext.__name__}",
                    (ext, cls),
                    {"__module__": base.__module__},
                )
            except TypeError as exc:
                echo.error(
                    f"'{source}' extension for '{name}' could not be "
                    f"composed: {exc}; ignored."
                )
        _COMPOSED[name] = (base, ext_classes, cls)
        return cls

    def __contains__(self, name):
        return name in _OPERATIONS

    def __iter__(self):
        return iter(_OPERATIONS)

    def __len__(self):
        return len(_OPERATIONS)


registry = _OperationRegistry()


def _warn_unknown_operations(entries):
    """Report extension targets matching no registered operation, once each."""
    unknown = {n for _, n, _ in entries} - set(_OPERATIONS) - _WARNED_UNKNOWN
    for ext_name in unknown:
        echo.error(f"plugin extends unknown operation '{ext_name}'; ignored.")
    _WARNED_UNKNOWN.update(unknown)
