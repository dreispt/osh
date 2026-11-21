"""Built-in backup plugin for Osh.

Provides the `osh db backup`, `osh db restore`, `osh db backups` and
`osh db remote` commands, and the bundled backup source schemes
(db://, http(s)://, odoosh://, ssh://). The former `osh backup` spellings
remain as hidden deprecated aliases.

Commands self-describe through ``_cli_name``-named ``CommandHandler``
classes — ``DbGet``/``DbRestore``/``DbBackups`` are leaf commands and ``DbRemote`` a
``@subcommand``-method group; sources register by subclassing
``BackupSource`` — the same channel third-party plugins use.
The classes are re-exported here so the loader can discover them among
the module's attributes once the plugin is imported.
"""

from .backup_cmd import (  # noqa: F401 — re-exported for handler discovery
    BackupGet,
    DbGet,
)
from .remotes import (  # noqa: F401 — re-exported for handler discovery
    BackupRemote,
    DbRemote,
)
from .restore_cmd import (  # noqa: F401 — re-exported for handler discovery
    BackupList,
    BackupRestore,
    DbBackups,
    DbRestore,
)
from .sources import (  # noqa: F401 — re-exported for source discovery
    DbSource,
    HttpSource,
    HttpsSource,
    OdooshSource,
    SshSource,
)
