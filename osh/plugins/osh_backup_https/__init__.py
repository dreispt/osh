"""Built-in `https://`/`http://` backup source plugin for Osh."""

from .sources import HttpSource, HttpsSource

BACKUP_SOURCES = [HttpsSource, HttpSource]
