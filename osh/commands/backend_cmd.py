"""``osh <backend>`` command groups — per-backend lifecycle commands.

``backend_group`` builds a Click group named after a ``Backend`` class with
the standard lifecycle commands — ``init``, ``activate``, ``doctor`` and
``stop`` — wired to the backend API. Backend plugins declare the resulting
group under the ``backend_commands`` manifest key; they may add subcommands
to it or build a fully custom group instead.

The core ``osh backend`` group owns backend *selection* state: ``status``
and ``list`` report what is active and available, ``deactivate`` is the
generic way back to the ``none`` backend and ``stop`` delegates teardown
to the active backend.
"""

from pathlib import Path

import click

from .. import echo
from ..cli_utils import NaturalOrderGroup
from ..common import find_project_root
from ..db import (
    deactivate_backend,
    get_active_backend_name,
    resolve_backend,
    set_project_config,
)
from ..utils.plugin_loader import load_backends
from .helpers import check_run_diagnostics, collect_diagnostics, report_diagnostics
from .init_cmd import (
    _rollback_new_osh_dir,
    _split_version_arg,
    base_init,
    init,
    run_backend_init,
)


@click.group(name="backend", cls=NaturalOrderGroup)
def backend():
    """Inspect and change the project's active backend."""


@backend.command(name="status")
def backend_status():
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
    if name not in load_backends():
        echo.warning(f"Backend '{name}' is not available — is its plugin enabled?")


@backend.command(name="list")
def backend_list():
    """List the available backends, marking the project's active one.

    Works outside a project too — without a project no backend is marked
    active.
    """
    base = find_project_root(required=False)
    active = get_active_backend_name(base) if base else None
    for name, backend_cls in sorted(load_backends().items()):
        marker = " (active)" if name == active else ""
        description = getattr(backend_cls, "description", "")
        echo.info(f"{name}{marker}" + (f" — {description}" if description else ""))


@backend.command(name="deactivate")
def backend_deactivate():
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


@backend.command(name="stop")
@click.pass_context
def backend_stop(ctx):
    """Stop resources the active backend left running.

    Delegates to the active backend's ``stop``: the ``none``/``venv``
    backends terminate a host Odoo process on the project's HTTP port,
    ``docker`` runs ``docker compose down``. Backend-specific options
    (e.g. ``--compose-file``) are on ``osh <backend> stop``.
    """
    base = find_project_root(required=True)
    resolve_backend(base).stop(ctx, base)


def backend_group(backend_cls):
    """Build the ``osh <backend>`` command group for *backend_cls*.

    The group is named after the backend and carries ``init``, ``activate``,
    ``doctor`` and ``stop`` subcommands.
    """
    group = NaturalOrderGroup(
        name=backend_cls.name,
        help=backend_cls.description
        or f"Commands for the '{backend_cls.name}' backend.",
    )
    group.add_command(_init_command(backend_cls))
    group.add_command(_activate_command(backend_cls))
    group.add_command(_doctor_command(backend_cls))
    group.add_command(_stop_command(backend_cls))
    return group


def _init_command(backend_cls):
    """Build the ``osh <backend> init`` command for *backend_cls*.

    Reuses the base ``osh init`` parameters and adds the backend's
    ``get_init_options()``. Runs :func:`base_init` first, then
    :func:`run_backend_init` for the backend-specific setup.
    """

    @click.pass_context
    def callback(
        ctx, version, directory, edition, save, assume_yes, dry_run, dev, **options
    ):
        version, directory = _split_version_arg(version, directory)
        target = (directory or Path.cwd()).expanduser().resolve()
        with _rollback_new_osh_dir(target):
            edition, version = base_init(
                ctx,
                target,
                version=version,
                edition=edition,
                save=save,
                assume_yes=assume_yes,
                dry_run=dry_run,
                dev=dev,
            )
            run_backend_init(
                ctx,
                backend_cls(),
                target,
                version=version,
                edition=edition,
                assume_yes=assume_yes,
                dry_run=dry_run,
                **options,
            )

    return click.Command(
        name="init",
        params=[*init.params, *backend_cls.get_init_options()],
        callback=callback,
        help=f"Initialise the project for the '{backend_cls.name}' backend, "
        "on top of `osh init`.",
    )


def _activate_command(backend_cls):
    """Build the ``osh <backend> activate`` command.

    Lighter than ``init``: verifies the backend can run in the project —
    run-phase diagnostics abort on errors — then records it as the
    project's active run backend.
    """

    @click.pass_context
    def callback(ctx):
        base = find_project_root(required=True)
        backend = backend_cls()
        check_run_diagnostics(base, backend, ctx)
        set_project_config(base, "run", "target", backend.name)
        echo.info(f"Backend '{backend.name}' is now the active run backend.")

    return click.Command(
        name="activate",
        callback=callback,
        help=f"Make '{backend_cls.name}' the project's active run backend.",
    )


def _doctor_command(backend_cls):
    """Build the ``osh <backend> doctor`` diagnostics command."""

    @click.pass_context
    def callback(ctx):
        base = find_project_root(required=True)
        diagnostics = collect_diagnostics(base, backend_cls(), ctx, check_nesting=True)
        report_diagnostics(diagnostics)

    return click.Command(
        name="doctor",
        callback=callback,
        help=f"Show diagnostics for the '{backend_cls.name}' backend.",
    )


def _stop_command(backend_cls):
    """Build the ``osh <backend> stop`` command."""

    @click.pass_context
    def callback(ctx, **options):
        base = find_project_root(required=True)
        backend_cls().stop(ctx, base, **options)

    return click.Command(
        name="stop",
        params=list(backend_cls.get_stop_options()),
        callback=callback,
        help=f"Stop resources left running by the '{backend_cls.name}' backend.",
    )
