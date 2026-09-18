"""Built-in `osh db drop` plugin for Osh.

Provides the `osh db drop` group subcommand, which drops a PostgreSQL
database together with its Odoo filestore directory, and extends
`osh db list` to report filestore directories that no longer have a
matching database.
"""

from .drop_cmd import drop
from .filestore import (  # noqa: F401 — re-exported for extension discovery
    DanglingFilestores,
)

OSH_PLUGIN_MANIFEST = {
    "group_commands": {"db": [drop]},
}
