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
    plugin_registry,
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
    for line in _plugin_reports():
        click.echo(line)
    ctx.exit()


def _plugin_reports():
    """Return formatted lines listing each pip-installed plugin and what it provides.

    Command names come from the assembled CLI — lazy stubs and collision
    renames included; runtime, source and handler-extension contributions
    come from plugin metadata, so no plugin module is imported here.
    Built-in plugins are not listed — only pip-installed (entry-point)
    ones, with their version and package name.
    """
    provided = {}
    for name, source in main.plugin_commands.items():
        provided.setdefault(source, ([], []))[0].append(name)
    for group_name, group in main.commands.items():
        for name, source in getattr(group, "plugin_commands", {}).items():
            provided.setdefault(source, ([], []))[1].append(f"{group_name}.{name}")

    installed = []
    for spec in plugin_registry().specs.values():
        if spec.kind != "entry_point":
            continue
        top, grouped = provided.get(spec.name, ([], []))
        group_names = {name.split(".")[0] for name in grouped}
        contributions = sorted(grouped + [n for n in top if n not in group_names])
        contributions += [
            f"{r} runtime" for r in sorted(spec.meta.get("runtimes") or {})
        ]
        if schemes := sorted(spec.meta.get("sources") or {}):
            contributions.append(f"sources: {', '.join(schemes)}")
        contributions += [f"extends {t}" for t in sorted(spec.declared_extends())]
        installed.append(
            (spec.name, spec.version, spec.package, ", ".join(contributions))
        )

    if not installed:
        return []
    w_name, w_version, w_package = (
        max(len(col) for col in cols) for cols in zip(*(r[:3] for r in installed))
    )
    lines = ["", "Installed plugins:"]
    for name, version, package, detail in installed:
        row = f"{name:<{w_name}}  {version or '-':<{w_version}}  {package or '-':<{w_package}}"
        lines.append(f"  {row}  {detail}".rstrip())
    return lines


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
    "shell",
    "test",
    "db",
    "backup",
    "config",
]

_ordered = {
    name: main.commands[name] for name in _COMMAND_ORDER if name in main.commands
}
_ordered.update(
    (name, cmd) for name, cmd in main.commands.items() if name not in _ordered
)
main.commands = _ordered
