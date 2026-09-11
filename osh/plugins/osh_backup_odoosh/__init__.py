"""Built-in `odoosh://` backup source plugin for Osh."""

from .sources import OdooshSource

OSH_PLUGIN_MANIFEST = {"backup_sources": [OdooshSource]}
