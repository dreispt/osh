import click
from osh.commands.odoo_cmd import OdooRun


class WithOpen(OdooRun):
    @classmethod
    def get_options(cls):
        return [
            *super().get_options(),
            click.Option(["--open", "open_browser"], is_flag=True),
        ]

    def pre_env(self):
        super().pre_env()
        click.echo(f"odoox open_browser={self.ctx.params.get('open_browser')}")
