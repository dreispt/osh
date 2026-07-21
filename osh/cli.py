"""Command-line interface entry-point for Osh.

Provides the root Click group and attaches sub-commands that live in
`osh.commands`.
"""

import click

from . import __version__
from .commands import COMMANDS
from .plugin_loader import load_plugins


class NaturalOrderGroup(click.Group):
    """Click group subclass that prints commands in the order declared."""

    def list_commands(self, ctx):  # noqa: D401
        return list(self.commands)  # retain insertion order


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@click.group(context_settings=CONTEXT_SETTINGS, cls=NaturalOrderGroup)
@click.version_option(
    version=__version__, prog_name="osh", help="Show the version and exit."
)
@click.option(
    "--verbosity",
    "-v",
    type=click.Choice(["quiet", "normal", "friendly", "verbose"]),
    default=None,
    help="Output verbosity level (default: auto-detect based on experience)",
)
@click.pass_context
def main(ctx, verbosity):  # noqa: D401
    """
    Odoo Shell – your toolkit for Odoo environments
    to accelerate your development and staging workflows.

    Use `osh init` to initialize an Odoo environment in a project.
    Use `osh run` to run Odoo in that environment, using an available
    backend (local, docker, etc.).
    Add the `--help` option to a command to learn more.
    """
    ctx.ensure_object(dict)
    ctx.obj["verbosity"] = verbosity


# Register all sub-commands from the dedicated package
for _cmd in COMMANDS:
    main.add_command(_cmd)

# Register commands from built-in and user-installed plugins.
# A plugin command whose name collides with a command that is already
# registered (core command or an earlier plugin) is prefixed with its
# plugin source, so both commands remain available in the CLI.
for plugin_source, plugin_cmd in load_plugins():
    name = plugin_cmd.name
    if name in main.commands:
        prefixed = f"{plugin_source}-{name}"
        if prefixed in main.commands:
            click.echo(
                f"Warning: plugin command '{name}' from '{plugin_source}' conflicts with an existing command and is ignored.",
                err=True,
            )
            continue
        name = prefixed
    main.add_command(plugin_cmd, name=name)
