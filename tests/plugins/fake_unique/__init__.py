import click

from osh.handlers import CommandHandler


class Unique(CommandHandler):
    _cli_name = "unique"

    def run(self):
        click.echo("from fake")
