import click
from osh.commands.runtime_cmd import RuntimeCtl


class Recorder(RuntimeCtl):
    def list(self):
        click.echo("extension")
        super().list()
