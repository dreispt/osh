"""Built-in `ssh://` backup source plugin for Osh."""

from .sources import SshSource

OSH_PLUGIN_MANIFEST = {"backup_sources": [SshSource]}
