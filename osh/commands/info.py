"""`osh info` command implementation."""
from __future__ import annotations

import click

from ..utils import _parse_dsn


@click.command(name="info")
@click.option("--dsn", envvar="ODOO_URL", default="http://localhost:8069", show_default=True)
@click.pass_context
def info(ctx: click.Context, dsn: str) -> None:  # noqa: D401
    """Print basic information about the Odoo server."""

    try:
        import odoorpc  # type: ignore
    except ImportError as e:  # pragma: no cover
        click.echo("The \"odoorpc\" package is required for this command.", err=True)
        raise click.ClickException(str(e))

    url, dbname, username, password = _parse_dsn(dsn)
    od = odoorpc.ODOO(url.hostname, port=url.port or 8069)
    if username and password:
        od.login(dbname, username, password)

    click.echo(f"Server version: {od.version().get('server_version', 'unknown')}")
    click.echo(f"Database: {dbname}")
