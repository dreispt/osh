"""Command-line interface entry-point for Osh.

Provides the root Click group and attaches sub-commands that live in
`osh.commands`.
"""

import click

from . import __version__, echo
from .cli_utils import NaturalOrderGroup
from .commands import COMMANDS
from .utils.plugin_loader import (
    declared_meta,
    load_group_commands,
    load_plugins,
    warn_unresolved_meta,
)


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

    Use `osh init` to initialize an Odoo environment in a project, and
    `osh init --runtime=<name>` to set up a runtime (host, venv, docker, ...).
    Use `osh shell` to enter the runtime environment or run any command inside it.
    Use `osh odoo` to run Odoo in that environment, and `osh stop` to stop
    what the runtime left running.
    Add the `--help` option to a command to learn more.

    Plugins add commands and runtimes; they are Python packages installed
    into the osh environment (e.g. `pipx inject osh osh-contrib`, or
    `pip install` when osh was installed with pip). See PLUGINS.md for
    the plugin system documentation.
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
    """Register plugin command *cmd* on *group*, handling collisions.

    *qualified* is the command name for top-level commands, or
    ``<group>.<name>`` for group subcommands. Returns the name the command
    was registered under, or None if skipped.
    """
    name = cmd.name
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
            f"existing '{name}' command; registered as '{fallback}'.",
            err=True,
        )
        name = fallback
    group.add_command(cmd, name=name)
    return name


# Register commands from built-in and user-installed plugins.
# A plugin command whose name collides with a command that is already
# registered (core command or an earlier plugin) is prefixed with its
# plugin source and reported, so both commands remain available in the CLI.
_plugin_commands = {}
for plugin_source, plugin_cmd in load_plugins():
    name = _register_plugin_command(main, plugin_cmd, plugin_source, plugin_cmd.name)
    if name is not None:
        _plugin_commands[name] = plugin_source
main.plugin_commands = _plugin_commands

# Register plugin-provided subcommands on command groups — plugin
# handlers with dotted names (``backup.restore``) declared under
# ``[group_commands.<group>]``. Missing target
# groups are created on demand: a group named after a declared runtime
# gets the runtime's description as its help; any other auto-created
# group (e.g. the ``backup.get`` handler creating ``backup``) is a
# plugin command. Targeting a non-group command is an error.
for group_name, entries in load_group_commands().items():
    target = main.commands.get(group_name)
    if target is None:
        target = NaturalOrderGroup(
            name=group_name,
            help=declared_meta("runtimes").get(group_name),
        )
        registered = _register_plugin_command(main, target, entries[0][0], group_name)
        if registered is None:
            continue
        main.plugin_commands[registered] = entries[0][0]
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
            target.plugin_commands = {
                **getattr(target, "plugin_commands", {}),
                name: source,
            }

# Flag plugin metadata referencing things nothing provides — a plugin
# extending a missing handler would otherwise never load, silently. The
# check reads declarations and already-loaded classes only.
warn_unresolved_meta()

# Order the top-level command list to match the documented command surface.
# Plugin-provided commands keep their registration order at the end and are
# listed in a separate help section (see NaturalOrderGroup.format_commands).
_COMMAND_ORDER = [
    "init",
    "stop",
    "odoo",
    "switch",
    "shell",
    "test",
    "db",
    "backup",
    "addon",
    "config",
]

_ordered = {
    name: main.commands[name] for name in _COMMAND_ORDER if name in main.commands
}
_ordered.update(
    (name, cmd) for name, cmd in main.commands.items() if name not in _ordered
)
main.commands = _ordered
