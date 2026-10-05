import click
from osh.commands.config_cmd import Config


class Recorder(Config):
    def show(self):
        click.echo("extension")
        super().show()
