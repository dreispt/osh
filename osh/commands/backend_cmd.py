"""``osh <backend>`` lifecycle commands and the ``osh backend`` group.

``BackendCommands`` is the shared base backend plugins subclass to
expose ``osh <backend> init``, ``activate`` and ``stop`` — ordinary
``@subcommand`` methods declared under ``[group_commands.<backend>]``
in ``osh-plugin.toml`` alongside the backend's ``[backends]`` entry.
The backend a group manages is named by ``_cli_name``: a ``Docker``
handler named ``docker`` binds the ``docker`` backend.

The core ``osh backend`` group owns backend *selection* state: ``status``
and ``list`` report what is active and available, ``deactivate`` is the
generic way back to the ``none`` backend and ``stop`` delegates teardown
to the active backend.
"""

from pathlib import Path

import click

from .. import echo
from ..cli_utils import handler_group
from ..common import find_project_root
from ..db import (
    deactivate_backend,
    get_active_backend_name,
    resolve_backend,
    set_project_config,
)
from ..handlers import CommandHandler, subcommand
from ..utils.plugin_loader import backend_meta, get_backend_class
from .helpers import check_run_diagnostics
from .init_cmd import (
    Init,
    _rollback_new_osh_dir,
    _split_version_arg,
    base_init,
    run_backend_init,
)


class BackendCtl(CommandHandler):
    """Inspect and change the project's active backend."""

    _cli_name = "backend"

    @subcommand
    def status(self):
        """Show the project's active backend.

        The active backend is what ``osh odoo``, ``osh shell`` and ``osh db``
        run through — the ``none`` backend means plain host execution. The name
        matches the one ``osh backend list`` marks as active.
        """
        base = find_project_root(required=True)
        name = get_active_backend_name(base)
        if not name or name == "none":
            echo.info("Active backend: none — commands run on the host.")
            return
        echo.info(f"Active backend: {name}")
        if name not in backend_meta():
            echo.warning(f"Backend '{name}' is not available — is its plugin enabled?")

    @subcommand
    def list(self):
        """List the available backends, marking the project's active one.

        Works outside a project too — without a project no backend is marked
        active.
        """
        base = find_project_root(required=False)
        active = get_active_backend_name(base) if base else None
        for name, description in sorted(backend_meta().items()):
            marker = " (active)" if name == active else ""
            echo.info(f"{name}{marker}" + (f" — {description}" if description else ""))

    @subcommand
    def deactivate(self):
        """Deactivate the active backend; commands run on the host again.

        Records ``none`` as the run backend — the counterpart of
        ``osh <backend> activate``. Resources the previous backend left running
        are not stopped; use ``osh <backend> stop`` for that.
        """
        base = find_project_root(required=True)
        previous = deactivate_backend(base)
        if previous is None:
            echo.info("No backend is active; commands already run on the host.")
            return
        echo.info(
            f"Backend '{previous}' deactivated; commands now run on the host. "
            f"Run 'osh {previous} stop' to stop resources it left running."
        )

    @subcommand
    def stop(self):
        """Stop resources the active backend left running.

        Delegates to the active backend's ``stop``: the ``none``/``venv``
        backends terminate a host Odoo process on the project's HTTP port,
        ``docker`` runs ``docker compose down``. Backend-specific options
        (e.g. ``--compose-file``) are on ``osh <backend> stop``.
        """
        base = find_project_root(required=True)
        resolve_backend(base).stop(self.ctx, base)


backend = handler_group("backend", BackendCtl)


class BackendCommands(CommandHandler):
    """Base for ``osh <backend>`` command groups — lifecycle as methods.

    A backend plugin subclasses this, sets ``_cli_name`` to the backend
    name and declares the commands under ``[group_commands.<backend>]``
    in ``osh-plugin.toml``. Its own ``@subcommand`` methods add
    backend-specific commands; overriding a lifecycle method (calling
    ``super()``) customizes it.

    Backend-specific parameters come from the backend class's option
    hooks (``get_init_options``, ``get_stop_options``), exposed through
    the ``init_options``/``stop_options`` classmethods; their parsed
    values are collected into kwargs through :meth:`backend_options`.
    """

    @subcommand
    def init(self):
        """Initialise the project for this backend, on top of ``osh init``.

        Combines ``osh init``'s parameters with the backend's
        ``get_init_options()``; runs ``base_init`` first, then
        ``run_backend_init`` for the backend-specific setup.
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
            run_backend_init(
                self.ctx,
                self.backend(),
                target,
                version=version,
                edition=edition,
                assume_yes=self.assume_yes,
                dry_run=self.dry_run,
                **self.backend_options(self.backend_cls().get_init_options()),
            )

    @classmethod
    def init_options(cls):
        """``osh <backend> init`` params: ``osh init``'s plus the backend's."""
        return [*Init.get_options(), *cls.backend_cls().get_init_options()]

    @subcommand
    def activate(self):
        """Record the backend as the project's run target.

        Lighter than ``init``: verifies the backend can run in the project —
        run-phase diagnostics abort on errors — then records it as the
        project's active run backend.
        """
        base = find_project_root(required=True)
        backend = self.backend()
        check_run_diagnostics(base, backend, self.ctx)
        set_project_config(base, "run", "target", backend.name)
        echo.info(f"Backend '{backend.name}' is now the active run backend.")

    @subcommand
    def stop(self):
        """Stop resources the backend left running."""
        self.backend().stop(
            self.ctx,
            find_project_root(required=True),
            **self.backend_options(self.backend_cls().get_stop_options()),
        )

    @classmethod
    def stop_options(cls):
        """``osh <backend> stop`` params: the backend's own options."""
        return list(cls.backend_cls().get_stop_options())

    version = None
    directory = None
    edition = None
    save = False
    assume_yes = False
    dry_run = False
    dev = True

    @classmethod
    def backend_name(cls):
        """Return the name of the backend this group manages."""
        return cls._cli_group or cls._cli_name

    @classmethod
    def backend_cls(cls):
        """Return the ``Backend`` class this group manages."""
        name = cls.backend_name()
        backend = get_backend_class(name) if name else None
        if backend is None:
            raise click.ClickException(f"No backend named '{name}' is available.")
        return backend

    def backend(self):
        """Return the managed backend instance."""
        return self.backend_cls()()

    def backend_options(self, options):
        """Return ``{name: value}`` for the parsed backend-specific *options*."""
        return {param.name: getattr(self, param.name) for param in options}
