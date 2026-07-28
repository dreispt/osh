"""Command-line interface entry-point for Osh.

Provides the root Click group and attaches sub-commands that live in
`osh.commands`.
"""

import click

from . import __version__, echo
from .commands import COMMANDS
from .utils.plugin_loader import load_plugins


class NaturalOrderGroup(click.Group):
    """Click group subclass that prints commands in the order declared.

    Also allows group-level options (e.g. ``-v``) to appear after the
    subcommand name, so they do not have to be redeclared on every command.
    """

    def list_commands(self, ctx):  # noqa: D401
        return list(self.commands)  # retain insertion order

    def parse_args(self, ctx, args):
        """Move group-level options to the front so Click parses them first."""
        global_names = {
            name
            for param in self.params
            if isinstance(param, click.Option)
            for name in param.opts
        }

        def _is_global_token(arg):
            for name in global_names:
                if arg == name:
                    return True
                if name.startswith("--") and arg.startswith(f"{name}="):
                    return True
            return False

        head = []
        tail = list(args)
        i = 0
        while i < len(tail):
            if _is_global_token(tail[i]):
                head.append(tail.pop(i))
                # One-token options with ``--opt=value`` already include the value.
                if head[-1].startswith("--") and "=" in head[-1]:
                    continue
                # Otherwise the next token is the option's value.
                if i < len(tail):
                    head.append(tail.pop(i))
                continue
            i += 1
        return super().parse_args(ctx, head + tail)


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
    Use `osh env` to enter the runtime environment or run any command inside it.
    Use `osh odoo` to run Odoo in that environment, using an available
    backend (local, docker, etc.).
    Add the `--help` option to a command to learn more.
    """
    ctx.ensure_object(dict)
    ctx.obj["verbosity"] = verbosity

    # Reset cache and set configuration based on CLI context
    echo._reset_cache()
    from .common import find_project_root

    base = find_project_root(required=False)
    # Set config with CLI verbosity override
    echo._set_config(verbosity=verbosity, base=base)


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
            echo.warning(
                f"plugin command '{name}' from '{plugin_source}' conflicts with an existing command and is ignored."
            )
            continue
        name = prefixed
    main.add_command(plugin_cmd, name=name)
