import click

from osh.handlers import CommandHandler


class Init(CommandHandler):
    _cli_name = "init"

    def run(self):
        click.echo("from fake")
