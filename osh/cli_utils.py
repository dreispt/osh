"""Click helpers shared by the root CLI and command groups."""

import click


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


def format_targets_section(formatter, backends):
    """Write a Targets help section listing each backend name and description."""
    if not backends:
        return
    records = [
        (name, getattr(backends[name], "description", "") or "")
        for name in sorted(backends)
    ]
    with formatter.section("Targets"):
        formatter.write_dl(records)
