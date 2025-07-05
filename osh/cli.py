"""Command-line interface entry-point for Osh.

Provides the root Click group and attaches sub-commands that live in
`osh.commands`.
"""
from __future__ import annotations

import click

from .commands import COMMANDS


class NaturalOrderGroup(click.Group):
    """Click group subclass that prints commands in the order declared."""

    def list_commands(self, ctx: click.Context) -> list[str]:  # noqa: D401
        return list(self.commands)  # retain insertion order


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@click.group(context_settings=CONTEXT_SETTINGS, cls=NaturalOrderGroup)
@click.version_option(package_name="osh")
@click.pass_context
def main(ctx: click.Context) -> None:  # noqa: D401
    """Odoo Shell – hack on your Odoo server from the comfort of your terminal."""


# Register all sub-commands from the dedicated package
for _cmd in COMMANDS:
    main.add_command(_cmd)
