"""Plugin contributing a backend, a backup source and a group command."""

from osh.backends import Backend
from osh.backup_sources import BackupSource
from osh.handlers import CommandHandler


class MyBackend(Backend):
    name = "mybackend"
    backend_type = "backend"


class MySource(BackupSource):
    scheme = "myscheme"


class MySub(CommandHandler):
    _cli_name = "db.mysub"

    def run(self):
        pass
