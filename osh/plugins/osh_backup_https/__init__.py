"""Built-in `https://`/`http://` backup source plugin for Osh."""

from .sources import HttpSource, HttpsSource

OSH_PLUGIN_MANIFEST = {"backup_sources": [HttpsSource, HttpSource]}
