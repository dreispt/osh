"""Deprecated compatibility module — superseded by :mod:`osh.handlers`.

The first-generation plugin API keeps working while plugins migrate:
``Operation`` aliases ``CommandHandler``, the ``@operation`` and
``@extends`` decorators stamp the marker attributes the loader still
honors, and ``Env`` / ``registry`` resolve through the handler class
tree instead of a registration dict.

New code should declare handlers with ``_cli_name`` and extend them by
subclassing — see ``osh.handlers``.
"""

from .handlers import CommandHandler, Env, plugin_group, registry, resolve

__all__ = [
    "CommandHandler",
    "Env",
    "Operation",
    "extends",
    "operation",
    "plugin_group",
    "registry",
    "resolve",
]

#: ``Operation`` is the deprecated name of ``CommandHandler``.
Operation = CommandHandler


def operation(name):
    """Deprecated — declare ``_cli_name = "<group>.<command>"`` instead.

    Stamps both ``_cli_name`` and the legacy ``operation_name`` marker,
    so decorated classes register under the new model unchanged.
    """

    def decorator(cls):
        cls._cli_name = name
        cls.operation_name = name
        return cls

    return decorator


def extends(*names):
    """Deprecated — subclass the handler class you extend instead.

    The decorated class is layered onto each handler named in *names* —
    Odoo ``_inherit``-style — through the ``_extends`` marker, which the
    plugin loader discovers among each plugin's module-level attributes::

        @extends("db.list")
        class DanglingFilestores:
            ...
    """

    def decorator(cls):
        existing = getattr(cls, "_extends", ())
        if isinstance(existing, str):
            existing = (existing,)
        cls._extends = (*existing, *names)
        return cls

    return decorator
