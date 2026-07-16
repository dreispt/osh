"""`osh version` command implementation."""

from __future__ import annotations

import click

from .. import __version__


@click.command(name="version")
@click.pass_context
def version(ctx: click.Context) -> None:  # noqa: D401
    """Show the installed Osh version.

    Use `osh --version` for the same output.
    """
    click.echo(f"osh, version {__version__}")
