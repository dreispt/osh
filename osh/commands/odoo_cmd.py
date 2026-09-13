"""`osh odoo` command implementation.

``osh odoo`` runs the project's Odoo executable inside the active target
environment (local host, virtualenv, or Docker container). It is the main entry point
for executing Odoo, including subcommands such as ``shell``, ``neutralize`` and
``scaffold``.

Environment preparation – addons path, database name and dbfilter – is handled
by ``osh shell`` via the dynamic config in ``.osh/cache/env``.
"""

import os

import click

from .. import echo
from ..backends import EnvSpec
from ..cli_utils import format_targets_section
from ..common import find_project_root, has_arg
from ..config import get_user_preference
from ..db import (
    db_exists,
    get_project_config,
    resolve_backend,
    resolve_db_name,
    set_project_config,
)
from ..hooks import HOOK_ODOO_OPTIONS, HOOK_ODOO_PRE_ENV
from ..utils.plugin_loader import load_backends, load_hooks
from .helpers import check_run_diagnostics
from .shell_cmd import parse_explicit_db, prepare_env_context


class OdooCommand(click.Command):
    """Click command that appends a Targets section to `osh odoo --help`."""

    def get_params(self, ctx):
        """Append plugin-declared options from the ``odoo.options`` hook."""
        params = [*super().get_params(ctx)]
        params.extend(
            param
            for param in load_hooks(HOOK_ODOO_OPTIONS)
            if isinstance(param, click.Parameter)
        )
        return params

    def format_help_text(self, ctx, formatter):
        """Write the docstring followed by the list of available backends."""
        super().format_help_text(ctx, formatter)
        format_targets_section(formatter, load_backends())


@click.command(
    name="odoo",
    cls=OdooCommand,
    context_settings=dict(ignore_unknown_options=True),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the assembled command without executing it.",
)
@click.option(
    "--target",
    "backend_name",
    default="local",
    envvar="OSH_RUN_TARGET",
    help="Execution target: local host, managed venv, or a plugin backend.",
)
@click.option(
    "--compose-file",
    default=None,
    envvar="OSH_COMPOSE_FILE",
    help="Docker Compose file to use (e.g. devel.yaml for Doodba). "
    "Defaults to $OSH_COMPOSE_FILE.",
)
@click.option(
    "--no-dev",
    is_flag=True,
    help="Do not inject the default --dev option.",
)
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def odoo(
    ctx,
    dry_run,
    backend_name,
    compose_file,
    no_dev,
    extra_args,
    no_db_filter=False,
    wait_for_exit=None,
    **_plugin_params,
):  # noqa: D401
    """Run the project's Odoo executable.

    Extra arguments are passed through to odoo-bin. Odoo subcommands such as
    ``shell``, ``neutralize`` or ``scaffold`` are supported.

    Environment preparation – addons path, database name and dbfilter – is handled
    by ``osh shell`` through the dynamic config in ``.osh/cache/env``.
    ``ODOO_RC`` and the ``PG*`` connection variables are already exported into
    the subprocess environment.

    Passing an explicit ``--config``/``-c`` argument suppresses the generated
    config, and passing ``--db-filter`` overrides the one ``osh`` injects –
    same as calling ``odoo-bin`` directly.

    Dev mode is on by default: ``--dev=all`` is appended unless you pass
    ``--dev`` yourself or use ``--no-dev``. The default can be changed with
    ``osh config odoo dev <value>`` (``off`` disables the injection).

    Environment variables:

    \b
      OSH_RUN_TARGET     execution target (same as --target)
      OSH_COMPOSE_FILE   compose file (same as --compose-file)
      OSH_WAIT           wait for the command instead of exec/replacing it

    Examples:

    \b
      osh odoo
      osh odoo -- --http-port=8080 --workers=0
      osh odoo shell
      osh odoo neutralize -d mydb
      osh odoo --target docker --compose-file devel.yaml
    """
    base = find_project_root(required=True)

    backend = resolve_backend(ctx, base, backend_name)
    set_project_config(base, "run", "target", backend.name)

    diagnostics = check_run_diagnostics(base, backend, ctx, compose_file=compose_file)

    # TODO: decide whether non-local --target backends should get the dev
    # default too; for now it is applied uniformly on every backend.
    extra_args = _with_dev_default(base, extra_args, no_dev=no_dev)

    # Odoo creates and initializes a missing ``db_name`` itself, so a
    # missing database is reported with a continue confirmation, not
    # prompted for.
    db_name = parse_explicit_db(extra_args)
    if not db_name and not has_arg(extra_args, "--config", short="-c"):
        db_name = resolve_db_name(base)
        if not db_exists(base, db_name, ctx=ctx, dry_run=dry_run):
            echo.info(
                f"Database '{db_name}' does not exist; "
                "Odoo will create and initialize it."
            )
            if not dry_run:
                echo.confirm("Continue?", default=True, abort=True)

    # Subcommands (e.g. shell, neutralize) do not need dbfilter.
    has_subcommand = extra_args and not extra_args[0].startswith("-")
    if has_subcommand:
        no_db_filter = True

    if backend.host_executable:
        executable = (
            diagnostics.info.get(backend.name, {}).get("odoo_executable") or "odoo-bin"
        )
    else:
        executable = "odoo"

    conf_path, env_vars, resolved_db = prepare_env_context(
        base,
        backend,
        ctx=ctx,
        db_name=db_name,
        no_db_filter=no_db_filter,
        extra_args=extra_args,
        dry_run=dry_run,
    )
    if conf_path:
        echo.info(f"Using config: {conf_path}")
    if resolved_db:
        echo.info(f"Using database: {resolved_db}")

    argv = [executable, *extra_args]
    env_spec = EnvSpec(
        argv=argv,
        env=env_vars,
        db_name=resolved_db,
        config_path=str(conf_path) if conf_path else None,
    )
    if wait_for_exit is None:
        wait_for_exit = _env_flag("OSH_WAIT")
    for hook in load_hooks(HOOK_ODOO_PRE_ENV):
        hook(ctx, base, env_spec)
    backend.env(ctx, base, env_spec, dry_run=dry_run, wait=wait_for_exit)


def _env_flag(name):
    """Return True when environment variable *name* holds a truthy value."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _with_dev_default(base, extra_args, *, no_dev):
    """Append the configured ``--dev`` default unless the user passed one."""
    if has_arg(extra_args, "--dev"):
        return extra_args
    dev = _resolve_dev_default(base, no_dev=no_dev)
    if dev is None:
        return extra_args
    return (*extra_args, f"--dev={dev}")


def _resolve_dev_default(base, *, no_dev):
    """Return the dev-mode value to inject, or None when disabled.

    Precedence: ``--no-dev`` flag > ``[odoo] dev`` in ``.osh/config.toml`` >
    ``[odoo] dev`` in ``~/.config/osh/config.toml`` > ``all``.
    """
    if no_dev:
        return None
    value = (
        get_project_config(base, "odoo", "dev")
        or get_user_preference("dev", section="odoo")
        or "all"
    )
    if str(value).strip().lower() in ("off", "none", "false", "0"):
        return None
    return value
