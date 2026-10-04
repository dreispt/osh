"""``osh <runtime>`` lifecycle commands and the ``osh runtime`` group.

A *runtime* is where Osh runs Odoo and its tools: ``host`` (the built-in
default), ``venv``, ``docker``, ... Runtimes are implemented as
``Runtime`` classes. ``RuntimeCommands`` is the shared base runtime
plugins subclass to expose ``osh <runtime> init``, ``activate`` and
``stop`` — ordinary ``@subcommand`` methods declared under
``[group_commands.<runtime>]`` in ``osh-plugin.toml`` alongside the
runtime's ``[runtimes]`` entry. The runtime a group manages is named by
``_cli_name``: a ``Docker`` handler named ``docker`` binds the ``docker``
runtime.

The core ``osh runtime`` group owns runtime *selection* state: ``status``
and ``list`` report what is active and available, ``activate`` and
``deactivate`` switch it (``deactivate`` is the way back to ``host``) and
``stop`` delegates teardown to the active runtime. ``osh backend`` is a
hidden, deprecated alias of ``osh runtime``.
"""

from pathlib import Path

import click

from .. import echo
from ..cli_utils import handler_group
from ..common import find_project_root
from ..db import (
    deactivate_runtime,
    get_active_runtime_name,
    resolve_runtime,
    set_active_runtime_name,
)
from ..handlers import CommandHandler, subcommand
from ..utils.plugin_loader import get_runtime_class, runtime_meta
from .helpers import check_run_diagnostics
from .init_cmd import (
    Init,
    _rollback_new_osh_dir,
    _split_version_arg,
    base_init,
    run_runtime_init,
)


class RuntimeCtl(CommandHandler):
    """Inspect and change the project's active runtime."""

    _cli_name = "runtime"

    @subcommand
    def status(self):
        """Show the project's active runtime.

        The active runtime is what ``osh odoo``, ``osh shell`` and ``osh db``
        run through — the ``host`` runtime means plain host execution. The
        name matches the one ``osh runtime list`` marks as active.
        """
        base = find_project_root(required=True)
        name = get_active_runtime_name(base)
        if not name or name == "host":
            echo.info("Active runtime: host — commands run on the host.")
            return
        echo.info(f"Active runtime: {name}")
        if name not in runtime_meta():
            echo.warning(f"Runtime '{name}' is not available — is its plugin enabled?")

    @subcommand
    def list(self):
        """List the available runtimes, marking the project's active one.

        Works outside a project too — without a project no runtime is marked
        active.
        """
        base = find_project_root(required=False)
        active = get_active_runtime_name(base) if base else None
        for name, description in sorted(runtime_meta().items()):
            marker = " (active)" if name == active else ""
            echo.info(f"{name}{marker}" + (f" — {description}" if description else ""))

    @subcommand
    @click.argument("runtime_name", metavar="NAME")
    def activate(self):
        """Make runtime NAME the project's active runtime.

        Same as ``osh NAME activate``, and also accepts ``host``: run-phase
        diagnostics abort on errors, then the runtime is recorded as
        ``run.runtime``.
        """
        base = find_project_root(required=True)
        runtime_cls = get_runtime_class(self.runtime_name)
        if runtime_cls is None:
            raise click.ClickException(
                f"No runtime named '{self.runtime_name}' is available."
            )
        runtime = runtime_cls()
        check_run_diagnostics(base, runtime, self.ctx)
        set_active_runtime_name(base, runtime.name)
        echo.info(f"Runtime '{runtime.name}' is now the active runtime.")

    @subcommand
    def deactivate(self):
        """Deactivate the active runtime; commands run on the host again.

        Records ``host`` as the active runtime — the counterpart of
        ``osh <runtime> activate``. Resources the previous runtime left
        running are not stopped; use ``osh <runtime> stop`` for that.
        """
        base = find_project_root(required=True)
        previous = deactivate_runtime(base)
        if previous is None:
            echo.info("No runtime is active; commands already run on the host.")
            return
        echo.info(
            f"Runtime '{previous}' deactivated; commands now run on the host. "
            f"Run 'osh {previous} stop' to stop resources it left running."
        )

    @subcommand
    def stop(self):
        """Stop resources the active runtime left running.

        Delegates to the active runtime's ``stop``: the ``host``/``venv``
        runtimes terminate a host Odoo process on the project's HTTP port,
        ``docker`` runs ``docker compose down``. Runtime-specific options
        (e.g. ``--compose-file``) are on ``osh <runtime> stop``.
        """
        base = find_project_root(required=True)
        resolve_runtime(base).stop(self.ctx, base)


# Deprecated name of the handler, kept for compatibility.
BackendCtl = RuntimeCtl

runtime = handler_group("runtime", RuntimeCtl)

# ``osh backend`` — hidden, deprecated alias of ``osh runtime``.
backend = handler_group("backend", RuntimeCtl)
backend.hidden = True
backend.deprecated = "Use 'osh runtime' instead."


class RuntimeCommands(CommandHandler):
    """Base for ``osh <runtime>`` command groups — lifecycle as methods.

    A runtime plugin subclasses this, sets ``_cli_name`` to the runtime
    name and declares the commands under ``[group_commands.<runtime>]``
    in ``osh-plugin.toml``. Its own ``@subcommand`` methods add
    runtime-specific commands; overriding a lifecycle method (calling
    ``super()``) customizes it.

    Runtime-specific parameters come from the runtime class's option
    hooks (``get_init_options``, ``get_stop_options``), exposed through
    the ``init_options``/``stop_options`` classmethods; their parsed
    values are collected into kwargs through :meth:`runtime_options`.
    """

    @subcommand
    def init(self):
        """Initialise the project for this runtime, on top of ``osh init``.

        Combines ``osh init``'s parameters with the runtime's
        ``get_init_options()``; runs ``base_init`` first, then
        ``run_runtime_init`` for the runtime-specific setup.
        """
        version, directory = _split_version_arg(self.version, self.directory)
        target = (directory or Path.cwd()).expanduser().resolve()
        with _rollback_new_osh_dir(target):
            edition, version = base_init(
                self.ctx,
                target,
                version=version,
                edition=self.edition,
                save=self.save,
                assume_yes=self.assume_yes,
                dry_run=self.dry_run,
                dev=self.dev,
            )
            run_runtime_init(
                self.ctx,
                self.runtime(),
                target,
                version=version,
                edition=edition,
                assume_yes=self.assume_yes,
                dry_run=self.dry_run,
                **self.runtime_options(self.runtime_cls().get_init_options()),
            )

    @classmethod
    def init_options(cls):
        """``osh <runtime> init`` params: ``osh init``'s plus the runtime's."""
        return [*Init.get_options(), *cls.runtime_cls().get_init_options()]

    @subcommand
    def activate(self):
        """Record the runtime as the project's active runtime.

        Lighter than ``init``: verifies the runtime can run in the project —
        run-phase diagnostics abort on errors — then records it as
        ``run.runtime``.
        """
        base = find_project_root(required=True)
        runtime = self.runtime()
        check_run_diagnostics(base, runtime, self.ctx)
        set_active_runtime_name(base, runtime.name)
        echo.info(f"Runtime '{runtime.name}' is now the active runtime.")

    @subcommand
    def stop(self):
        """Stop resources the runtime left running."""
        self.runtime().stop(
            self.ctx,
            find_project_root(required=True),
            **self.runtime_options(self.runtime_cls().get_stop_options()),
        )

    @classmethod
    def stop_options(cls):
        """``osh <runtime> stop`` params: the runtime's own options."""
        return list(cls.runtime_cls().get_stop_options())

    version = None
    directory = None
    edition = None
    save = False
    assume_yes = False
    dry_run = False
    dev = True

    @classmethod
    def runtime_name(cls):
        """Return the name of the runtime this group manages."""
        return cls._cli_group or cls._cli_name

    @classmethod
    def runtime_cls(cls):
        """Return the ``Runtime`` class this group manages."""
        name = cls.runtime_name()
        runtime = get_runtime_class(name) if name else None
        if runtime is None:
            raise click.ClickException(f"No runtime named '{name}' is available.")
        return runtime

    def runtime(self):
        """Return the managed runtime instance."""
        return self.runtime_cls()()

    def runtime_options(self, options):
        """Return ``{name: value}`` for the parsed runtime-specific *options*."""
        return {param.name: getattr(self, param.name) for param in options}

    # Deprecated aliases kept for plugins written against the backend API.
    backend_name = runtime_name
    backend_cls = runtime_cls
    backend = runtime
    backend_options = runtime_options


# Deprecated alias kept for plugins written against the backend API.
BackendCommands = RuntimeCommands
