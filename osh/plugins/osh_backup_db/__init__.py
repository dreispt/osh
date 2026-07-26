"""Built-in `db://` backup source plugin for Osh."""

from .sources import DbSource

BACKUP_SOURCES = [DbSource]
