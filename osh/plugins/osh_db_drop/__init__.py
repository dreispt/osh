"""Built-in `osh db drop` plugin for Osh.

Provides the `osh db drop` group subcommand, which drops a PostgreSQL
database together with its Odoo filestore directory.
"""

from .drop_cmd import drop

OSH_PLUGIN_MANIFEST = {
    "group_commands": {"db": [drop]},
}
