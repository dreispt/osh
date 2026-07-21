"""`osh version` command implementation."""

import click

from .. import __version__
from ..echo import get_echo


@click.command(name="version")
@click.pass_context
def version(ctx):  # noqa: D401
    """Show the installed Osh version.

    Use `osh --version` for the same output.
    """
    echo = get_echo(ctx, None)
    echo.info(f"osh, version {__version__}")
