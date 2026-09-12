"""Built-in backup plugin for Osh.

Provides the `osh backup` command, the `osh db restore` group subcommand,
and the bundled backup source schemes (db://, http(s)://, odoosh://, ssh://).

The bundled sources are registered through this plugin's own
``osh_backup.sources`` hook point — the same channel third-party plugins use
to add schemes.
"""

from .backup_cmd import backup
from .registry import SOURCES_HOOK
from .restore_cmd import restore
from .sources import DbSource, HttpSource, HttpsSource, OdooshSource, SshSource

OSH_PLUGIN_MANIFEST = {
    "commands": [backup],
    "group_commands": {"db": [restore]},
    "hooks": {
        SOURCES_HOOK: [DbSource, HttpSource, HttpsSource, OdooshSource, SshSource],
    },
}
