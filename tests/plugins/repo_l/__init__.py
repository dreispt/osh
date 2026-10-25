"""Plugin contributing a runtime, a backup source and group commands."""

import click

from osh.runtimes import Runtime
from osh.backup_sources import BackupSource
from osh.handlers import CommandHandler, subcommand


class MyRuntime(Runtime):
    name = "myruntime"


class MyRuntimeCommands(CommandHandler):
    """``osh myruntime`` group — the plugin's own command group."""

    _cli_name = "myruntime"

    @subcommand
    @click.argument("project", required=False)
    def stop(self):
        """Stop my runtime's resources."""


class MySource(BackupSource):
    scheme = "myscheme"


class MySub(CommandHandler):
    _cli_name = "db.mysub"

    def run(self):
        pass
