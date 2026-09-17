"""Built-in `osh db drop` plugin for Osh.

Provides the `osh db drop` group subcommand, which drops a PostgreSQL
database together with its Odoo filestore directory, and the
``db.list_sections`` hook reporting filestore directories that no longer
have a matching database.
"""

from ...hooks import HOOK_DB_LIST_SECTIONS
from .drop_cmd import drop
from .filestore import dangling_filestore_lines

OSH_PLUGIN_MANIFEST = {
    "group_commands": {"db": [drop]},
    "hooks": {HOOK_DB_LIST_SECTIONS: [dangling_filestore_lines]},
}
