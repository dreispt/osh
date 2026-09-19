"""Click helpers shared by the root CLI and command groups."""

import inspect

import click


class ExtensibleCommand(click.Command):
    """Click command delegating to a handler class.

    Parameters come from the handler's ``get_options()`` — looked up on
    the *effective* class at parse time, so extending plugins can inject
    options — and the callback delegates to ``handler(ctx, **params).run()``.
    ``format_cli_help`` on the effective class appends extra help sections.

    The handler is a ``CommandHandler`` subclass (*handler*) or a
    qualified name (*handler_name*, resolved lazily through
    ``osh.handlers.resolve``).
    """

    handler = None
    handler_name = None

    def __init__(self, *args, handler=None, handler_name=None, **kwargs):
        super().__init__(*args, **kwargs)
        if handler is not None:
            self.handler = handler
        if handler_name is not None:
            self.handler_name = handler_name

    def _handler_cls(self):
        """Return the effective handler class, or ``None``."""
        if self.handler is not None:
            return self.handler.effective()
        if self.handler_name is not None:
            from .handlers import resolve

            return resolve(self.handler_name).effective()
        return None

    def get_params(self, ctx):
        """Append the handler's declared options to the base parameters."""
        params = [*super().get_params(ctx)]
        handler = self._handler_cls()
        if handler is not None:
            params.extend(
                param
                for param in handler.get_options()
                if isinstance(param, click.Parameter)
            )
        return params

    def format_help(self, ctx, formatter):
        """Write standard help followed by handler-provided sections."""
        super().format_help(ctx, formatter)
        handler = self._handler_cls()
        if handler is not None:
            handler.format_cli_help(formatter)


def handler_command(name, cls):
    """Build the ``click.Command`` exposing handler *cls* as *name*."""

    @click.pass_context
    def callback(ctx, **kwargs):
        cls(ctx, **ctx.params).run()

    callback.__module__ = cls.__module__
    callback.__name__ = cls.__name__
    callback.__doc__ = inspect.getdoc(cls)

    return ExtensibleCommand(
        name=name,
        callback=callback,
        params=[],
        help=inspect.getdoc(cls),
        context_settings=cls._cli_context_settings,
        handler=cls,
    )


class LazyCommand(click.Command):
    """Click command stub importing its real implementation on first use.

    Registered at startup from plugin metadata, so ``osh --help`` lists the
    command without evaluating plugin code — ``help``/``short_help`` come
    from the metadata. Any real use — parsing, executing, or rendering
    ``osh <cmd> --help`` — delegates to the loaded command's own
    ``make_context``, which owns everything downstream.
    """

    def __init__(self, name, loader, *, plugin=None, **kwargs):
        kwargs.setdefault("params", [])
        kwargs.setdefault("callback", lambda: None)
        super().__init__(name, **kwargs)
        self._loader = loader
        self._plugin = plugin
        self._resolved = None

    def load(self):
        """Import the plugin and return the real ``click.Command``."""
        if self._resolved is None:
            try:
                command = self._loader()
            except click.ClickException:
                raise
            except Exception as exc:
                src = f"plugin '{self._plugin}' " if self._plugin else ""
                raise click.ClickException(
                    f"Could not load {src}command '{self.name}': {exc}"
                ) from exc
            if not isinstance(command, click.Command):
                src = f"plugin '{self._plugin}' " if self._plugin else ""
                raise click.ClickException(
                    f"{src}did not provide a '{self.name}' command."
                )
            self._resolved = command
        return self._resolved

    def get_short_help_str(self, limit=45):
        """Return the resolved command's short help, or the metadata's."""
        if self._resolved is not None:
            return self._resolved.get_short_help_str(limit)
        return super().get_short_help_str(limit)

    def make_context(self, info_name, args, parent=None, **extra):
        # Delegation is the lazy import — the real command owns parsing,
        # help rendering and invocation from here on.
        return self.load().make_context(info_name, args, parent=parent, **extra)

    def invoke(self, ctx):
        # Safety net: ``ctx.command`` is only this stub when make_context
        # was bypassed.
        if ctx.command is self:
            return self.load().invoke(ctx)
        return ctx.command.invoke(ctx)


class LazyGroup(LazyCommand, click.Group):
    """Click group stub importing its real implementation on first use."""

    def get_command(self, ctx, cmd_name):
        """Return a subcommand of the resolved group, loading it first."""
        group = self.load()
        if isinstance(group, click.Group):
            return group.get_command(ctx, cmd_name)
        return None

    def list_commands(self, ctx):
        """List the resolved group's subcommands, loading it first."""
        group = self.load()
        if isinstance(group, click.Group):
            return group.list_commands(ctx)
        return []


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
    """Write a Backends help section listing each backend name and description.

    *backends* maps names to backend classes (read ``description``) or to
    plain description strings — the metadata form lets help render without
    importing backend plugins.
    """
    if not backends:
        return
    records = [
        (
            name,
            (
                entry
                if isinstance(entry, str)
                else getattr(entry, "description", "") or ""
            ),
        )
        for name, entry in sorted(backends.items())
    ]
    with formatter.section("Backends"):
        formatter.write_dl(records)
