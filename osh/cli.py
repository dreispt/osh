"""Command-line interface entry-point for Osh.

Provides the root Click group and attaches sub-commands that live in
`osh.commands`.
"""

from __future__ import annotations

import click

from . import __version__
from .commands import COMMANDS
from .plugin_loader import load_plugins


class NaturalOrderGroup(click.Group):
    """Click group subclass that prints commands in the order declared."""

    def list_commands(self, ctx: click.Context) -> list[str]:  # noqa: D401
        return list(self.commands)  # retain insertion order


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@click.group(context_settings=CONTEXT_SETTINGS, cls=NaturalOrderGroup)
@click.version_option(
    version=__version__, prog_name="osh", help="Show the version and exit."
)
@click.pass_context
def main(ctx: click.Context) -> None:  # noqa: D401
    """Odoo Shell – Odoo wrapper to accelerate your development and staging workflows."""


# Register all sub-commands from the dedicated package
for _cmd in COMMANDS:
    main.add_command(_cmd)

# Register commands from built-in and user-installed plugins.
# A plugin command whose name collides with a command that is already
# registered (core command or an earlier plugin) is prefixed with its
# plugin source, so both commands remain available in the CLI.
for _plugin_source, _plugin_cmd in load_plugins():
    _name = _plugin_cmd.name
    if _name in main.commands:
        _prefixed = f"{_plugin_source}-{_name}"
        if _prefixed in main.commands:
            click.echo(
                f"Warning: plugin command '{_name}' from '{_plugin_source}' conflicts with an existing command and is ignored.",
                err=True,
            )
            continue
        _name = _prefixed
    main.add_command(_plugin_cmd, name=_name)
