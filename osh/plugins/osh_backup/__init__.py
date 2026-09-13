"""Built-in backup plugin for Osh.

Provides the `osh db get` and `osh db restore` group subcommands, and the
bundled backup source schemes (db://, http(s)://, odoosh://, ssh://).

The bundled sources are registered through this plugin's own
``osh_backup.sources`` hook point — the same channel third-party plugins use
to add schemes.
"""

from .backup_cmd import get
from .registry import SOURCES_HOOK
from .remotes import remote
from .restore_cmd import restore
from .sources import DbSource, HttpSource, HttpsSource, OdooshSource, SshSource

OSH_PLUGIN_MANIFEST = {
    "group_commands": {"db": [get, restore, remote]},
    "hooks": {
        SOURCES_HOOK: [DbSource, HttpSource, HttpsSource, OdooshSource, SshSource],
    },
}
