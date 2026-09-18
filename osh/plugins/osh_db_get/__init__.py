"""Built-in `osh db get` plugin for Osh.

Provides the `osh db get` and `osh db restore` group subcommands, and the
bundled backup source schemes (db://, http(s)://, odoosh://, ssh://).

The bundled sources register automatically by subclassing
``BackupSource`` — the same channel third-party plugins use to add
schemes.
"""

from .backup_cmd import get
from .remotes import remote
from .restore_cmd import restore
from .sources import (  # noqa: F401 — re-exported for source discovery
    DbSource,
    HttpSource,
    HttpsSource,
    OdooshSource,
    SshSource,
)

OSH_PLUGIN_MANIFEST = {
    "group_commands": {"db": [get, restore, remote]},
}
