"""Plugin contributing a backend, a backup source and group commands."""

import click

from osh.backends import Backend
from osh.backup_sources import BackupSource
from osh.handlers import CommandHandler, subcommand


class MyBackend(Backend):
    name = "mybackend"
    backend_type = "backend"


class MyBackendCommands(CommandHandler):
    """``osh mybackend`` group — the plugin's own command group."""

    _cli_name = "mybackend"

    @subcommand
    @click.argument("project", required=False)
    def stop(self):
        """Stop my backend's resources."""


class MySource(BackupSource):
    scheme = "myscheme"


class MySub(CommandHandler):
    _cli_name = "db.mysub"

    def run(self):
        pass
