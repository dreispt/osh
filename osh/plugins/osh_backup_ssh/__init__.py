"""Built-in `ssh://` backup source plugin for Osh."""

from .sources import SshSource

BACKUP_SOURCES = [SshSource]
