"""Click helpers shared by the root CLI and command groups."""

import click


class NaturalOrderGroup(click.Group):
    """Click group subclass that prints commands in the order declared.

    Also allows group-level options (e.g. ``-v``) to appear after the
    subcommand name, so they do not have to be redeclared on every command.

    Commands whose names are in ``plugin_commands`` or ``backend_commands``
    — ``{name: source}`` mappings assigned by ``cli.py`` after plugin
    registration — are listed in separate help sections, annotated with
    their source.
    """

    plugin_commands = {}
    backend_commands = {}

    def list_commands(self, ctx):  # noqa: D401
        return list(self.commands)  # retain insertion order

    def format_commands(self, ctx, formatter):
        """Print core commands and plugin commands in separate sections."""
        commands = []
        for name in self.list_commands(ctx):
            cmd = self.get_command(ctx, name)
            if cmd is None or cmd.hidden:
                continue
            commands.append((name, cmd))
        if not commands:
            return
        limit = formatter.width - 6 - max(len(name) for name, _ in commands)
        core_rows = []
        backend_rows = []
        plugin_rows = []
        for name, cmd in commands:
            source = self.backend_commands.get(name, self.plugin_commands.get(name))
            help_text = cmd.get_short_help_str(
                limit - len(f" [{source}]") if source else limit
            )
            if source:
                help_text = f"{help_text} [{source}]".strip()
            if name in self.backend_commands:
                backend_rows.append((name, help_text))
            elif name in self.plugin_commands:
                plugin_rows.append((name, help_text))
            else:
                core_rows.append((name, help_text))
        if core_rows:
            with formatter.section("Commands"):
                formatter.write_dl(core_rows)
        if backend_rows:
            with formatter.section("Backend Commands"):
                formatter.write_dl(backend_rows)
        if plugin_rows:
            with formatter.section("Plugin Commands"):
                formatter.write_dl(plugin_rows)

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


def format_backends_section(formatter, backends):
    """Write a Backends help section listing each backend name and description."""
    if not backends:
        return
    records = [
        (name, getattr(backends[name], "description", "") or "")
        for name in sorted(backends)
    ]
    with formatter.section("Backends"):
        formatter.write_dl(records)
