"""Hook points — a plugin-to-plugin extension mechanism.

Plugins declare hook implementations under the ``hooks`` key of their
``OSH_PLUGIN_MANIFEST`` dict, mapping a hook point name to a callable or a
list of callables/objects. Hook point names are just namespaced strings —
plugins define their own hook points for other plugins to subscribe to
(e.g. ``"osh_db_get.sources"`` defined by the ``osh_db_get`` plugin) and
consume them with ``load_hooks(<name>)`` or ``load_hook_entries(<name>)``
from `osh.utils.plugin_loader`.

Core defines no hook points of its own: core *commands* are extended
through operation classes instead — see `osh.operations` and the
``@extends`` decorator.
"""
