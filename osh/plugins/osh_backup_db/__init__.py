"""Built-in `db://` backup source plugin for Osh."""

from .sources import DbSource

OSH_PLUGIN_MANIFEST = {"backup_sources": [DbSource]}
