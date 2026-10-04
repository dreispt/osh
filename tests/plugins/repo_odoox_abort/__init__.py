import click
from osh.commands.odoo_cmd import OdooRun


class Abort(OdooRun):
    def pre_env(self):
        super().pre_env()
        raise click.ClickException("extension says no")
