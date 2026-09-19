"""Plugin redeclaring the built-in 'db' backup source."""

from osh.backup_sources import BackupSource


class DbAgain(BackupSource):
    scheme = "db"
