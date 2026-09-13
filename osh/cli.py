"""Command-line interface entry-point for Osh.

Provides the root Click group and attaches sub-commands that live in
`osh.commands`.
"""

import click

from . import __version__, echo
from .cli_utils import NaturalOrderGroup
from .commands import COMMANDS
from .config import get_plugin_aliases
from .utils.plugin_loader import load_group_commands, load_plugins


def _print_version(ctx, param, value):
    """Eager ``--version`` callback printing the version and exiting.

    ``__version__`` already carries the ``+<commit>`` local suffix when osh
    runs from a git checkout (see ``osh.__init__._get_version``).
    """
    if not value or ctx.resilient_parsing:
        return
    click.echo(f"osh, version {__version__}")
    ctx.exit()


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@click.group(context_settings=CONTEXT_SETTINGS, cls=NaturalOrderGroup)
@click.option(
    "--version",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_print_version,
    help="Show the version and exit.",
)
@click.option(
    "--silent",
    is_flag=True,
    help="Only show errors.",
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Show detailed output, including the commands being run.",
)
@click.option(
    "--debug",
    is_flag=True,
    help="Verbose output plus internal diagnostics (exit codes, timing).",
)
@click.pass_context
def main(ctx, silent, verbose, debug):  # noqa: D401
    """
    Odoo Shell – your toolkit for Odoo environments
    to accelerate your development and staging workflows.

    Usage: osh [--silent | --verbose | --debug] <command> [args]

    Use `osh init` to initialize an Odoo environment in a project.
    Use `osh shell` to enter the runtime environment or run any command inside it.
    Use `osh odoo` to run Odoo in that environment, using an available
    backend (local, docker, etc.).
    Add the `--help` option to a command to learn more.
    """
    ctx.ensure_object(dict)
    selected = [
        name
        for name, on in (("silent", silent), ("verbose", verbose), ("debug", debug))
        if on
    ]
    if len(selected) > 1:
        raise click.UsageError(
            "--silent, --verbose and --debug are mutually exclusive."
        )
    verbosity = selected[0] if selected else None
    ctx.obj["verbosity"] = verbosity

    # Reset cache and set configuration based on CLI context
    echo.reset_cache()
    from .common import find_project_root

    base = find_project_root(required=False)
    # Set config with CLI verbosity override
    echo.set_config(verbosity=verbosity, base=base)


# Register all sub-commands from the dedicated package
for _cmd in COMMANDS:
    main.add_command(_cmd)


def _register_plugin_command(group, cmd, source, qualified):
    """Register plugin command *cmd* on *group*, honoring aliases/collisions.

    *qualified* is the alias key stored in the user config — the command name
    for top-level commands, or ``<group>.<name>`` for group subcommands.
    Returns the name the command was registered under, or None if skipped.
    """
    name = cmd.name
    alias = get_plugin_aliases(source).get(qualified)
    if alias is not None:
        if alias in group.commands:
            echo.error(
                f"plugin '{source}' command alias '{alias}' for '{qualified}' "
                "conflicts with an existing command and is ignored."
            )
            return None
        group.add_command(cmd, name=alias)
        return alias
    if name in group.commands:
        fallback = f"{source}-{name}"
        if fallback in group.commands:
            echo.error(
                f"plugin '{source}' command '{qualified}' conflicts with an "
                "existing command and is ignored."
            )
            return None
        echo.warning(
            f"plugin '{source}' command '{qualified}' conflicts with the "
            f"existing '{name}' command; registered as '{fallback}'. "
            f"Choose a permanent name with: osh plug alias {source} "
            f"{qualified} <name>",
            err=True,
        )
        name = fallback
    group.add_command(cmd, name=name)
    return name


# Register commands from built-in and user-installed plugins.
# A plugin command whose name collides with a command that is already
# registered (core command or an earlier plugin) is prefixed with its
# plugin source and reported, so both commands remain available in the CLI.
_plugin_names = set()
for plugin_source, plugin_cmd in load_plugins():
    name = _register_plugin_command(main, plugin_cmd, plugin_source, plugin_cmd.name)
    if name is not None:
        _plugin_names.add(name)
main.plugin_commands = _plugin_names

# Register plugin-provided subcommands on existing command groups
# (``group_commands`` manifest key).
for group_name, entries in load_group_commands().items():
    target = main.commands.get(group_name)
    if not isinstance(target, click.Group):
        for source, _cmd in entries:
            echo.error(
                f"plugin '{source}' group command target '{group_name}' is "
                "not a command group; ignored."
            )
        continue
    for source, _cmd in entries:
        name = _register_plugin_command(
            target, _cmd, source, f"{group_name}.{_cmd.name}"
        )
        if name is not None:
            target.plugin_commands = set(getattr(target, "plugin_commands", ())) | {
                name
            }

# Order the top-level command list to match the documented command surface.
# Plugin-provided commands keep their registration order at the end and are
# listed in a separate help section (see NaturalOrderGroup.format_commands).
_COMMAND_ORDER = [
    "init",
    "odoo",
    "switch",
    "shell",
    "down",
    "test",
    "db",
    "doctor",
    "config",
    "plug",
]

_ordered = {
    name: main.commands[name] for name in _COMMAND_ORDER if name in main.commands
}
_ordered.update(
    (name, cmd) for name, cmd in main.commands.items() if name not in _ordered
)
main.commands = _ordered
