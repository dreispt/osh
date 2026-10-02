"""Built-in `osh backup` plugin for Osh.

Provides the `osh backup` group with the `get`, `restore`, `list` and
`remote` subcommands, and the bundled backup source schemes (db://, http(s)://,
odoosh://, ssh://).

Commands self-describe through ``_cli_name``-named ``CommandHandler``
classes — ``DbGet``/``DbRestore``/``BackupList`` are leaf commands and ``DbRemote`` a
``@subcommand``-method group; sources register by subclassing
``BackupSource`` — the same channel third-party plugins use.
The classes are re-exported here so the loader can discover them among
the module's attributes once the plugin is imported.
"""

from .backup_cmd import DbGet  # noqa: F401 — re-exported for handler discovery
from .remotes import DbRemote  # noqa: F401 — re-exported for handler discovery
from .restore_cmd import (  # noqa: F401 — re-exported for handler discovery
    BackupList,
    DbRestore,
)
from .sources import (  # noqa: F401 — re-exported for source discovery
    DbSource,
    HttpSource,
    HttpsSource,
    OdooshSource,
    SshSource,
)
