"""Command-line interface entry-point for Osh.

Provides the root Click group and attaches sub-commands that live in
`osh.commands`.
"""
from __future__ import annotations

import click

from . import __version__
from .commands import COMMANDS


class NaturalOrderGroup(click.Group):
    """Click group subclass that prints commands in the order declared."""

    def list_commands(self, ctx: click.Context) -> list[str]:  # noqa: D401
        return list(self.commands)  # retain insertion order


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@click.group(context_settings=CONTEXT_SETTINGS, cls=NaturalOrderGroup)
@click.version_option(version=__version__, prog_name="osh", help="Show the version and exit.")
@click.pass_context
def main(ctx: click.Context) -> None:  # noqa: D401
    """Odoo Shell – Odoo wrapper to accelerate your development and staging workflows."""


# Register all sub-commands from the dedicated package
for _cmd in COMMANDS:
    main.add_command(_cmd)
