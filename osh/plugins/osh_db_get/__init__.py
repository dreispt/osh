"""Built-in `osh db get` plugin for Osh.

Provides the `osh db get`, `osh db restore` and `osh db remote` group
subcommands, and the bundled backup source schemes (db://, http(s)://,
odoosh://, ssh://).

Commands self-describe through ``_cli_name``-named ``CommandHandler``
classes and the ``@plugin_group``-stamped ``remote`` group; sources
register by subclassing ``BackupSource`` — the same channel third-party
plugins use.
The classes are re-exported here so the loader can discover them among
the module's attributes once the plugin is imported.
"""

from .backup_cmd import DbGet  # noqa: F401 — re-exported for handler discovery
from .remotes import remote  # noqa: F401 — re-exported for group discovery
from .restore_cmd import DbRestore  # noqa: F401 — re-exported for handler discovery
from .sources import (  # noqa: F401 — re-exported for source discovery
    DbSource,
    HttpSource,
    HttpsSource,
    OdooshSource,
    SshSource,
)
