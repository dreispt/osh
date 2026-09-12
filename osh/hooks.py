"""Hook point names that plugins can subscribe to.

Plugins declare hook implementations under the ``hooks`` key of their
``OSH_PLUGIN_MANIFEST`` dict, mapping a hook point name to a callable or a
list of callables/objects. Hook points are the extension points where core
commands invoke plugin-provided behaviour.

Hook point names are just namespaced strings — plugins can define their own
hook points (e.g. ``"osh_backup.sources"`` defined by the ``osh_backup``
plugin) for other plugins to subscribe to.
"""

# ``odoo.options`` — items are ``click.Parameter`` instances (typically
# ``click.Option``) appended to the ``osh odoo`` command's parameters at
# parse time. Their values land in ``ctx.params`` like regular options.
HOOK_ODOO_OPTIONS = "odoo.options"

# ``odoo.pre_env`` — items are callables ``hook(ctx, base, env_spec)``
# invoked right before ``Backend.env()`` executes. ``ctx.params`` holds the
# parsed CLI values (including ``extra_args``, ``dry_run`` and any
# plugin-injected options); ``env_spec`` carries the assembled ``EnvSpec``.
# Hooks run for every ``osh odoo`` invocation — including ``--dry-run`` and
# subcommands — and must self-filter. Raising ``click.ClickException``
# aborts the run.
HOOK_ODOO_PRE_ENV = "odoo.pre_env"
