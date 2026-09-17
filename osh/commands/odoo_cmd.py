"""`osh odoo` command implementation.

``osh odoo`` runs the project's Odoo executable inside the active target
environment (local host, virtualenv, or Docker container). It is the main entry point
for executing Odoo, including subcommands such as ``shell``, ``neutralize`` and
``scaffold``.

Environment preparation – addons path, database name and dbfilter – is handled
by ``osh shell`` via the dynamic config in ``.osh/cache/env``.
"""

import os
import sys

import click

from .. import echo
from ..backends import EnvSpec
from ..cli_utils import format_backends_section
from ..common import find_project_root, has_arg
from ..config import get_user_preference
from ..db import (
    _prompt_for_missing_db,
    db_exists,
    get_last_db,
    get_project_config,
    resolve_backend,
    resolve_branch,
    resolve_db_name,
    set_last_db,
)
from ..hooks import HOOK_ODOO_OPTIONS, HOOK_ODOO_PRE_ENV
from ..utils.plugin_loader import load_backends, load_hooks
from .helpers import check_run_diagnostics
from .shell_cmd import parse_explicit_db, prepare_env_context


class OdooCommand(click.Command):
    """Click command that appends a Backends section to `osh odoo --help`."""

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
        format_backends_section(formatter, load_backends())


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

    The execution backend is the one activated for the project — see
    ``osh <backend> init``/``osh <backend> activate`` (e.g. ``osh docker
    activate``).

    Environment variables:

    \b
      OSH_COMPOSE_FILE   compose file (same as --compose-file)
      OSH_WAIT           wait for the command instead of exec/replacing it

    Examples:

    \b
      osh odoo
      osh odoo -- --http-port=8080 --workers=0
      osh odoo shell
      osh odoo neutralize -d mydb
      osh odoo --compose-file devel.yaml
    """
    base = find_project_root(required=True)

    backend = resolve_backend(base)

    diagnostics = check_run_diagnostics(base, backend, ctx, compose_file=compose_file)

    extra_args = _with_dev_default(base, extra_args, no_dev=no_dev)

    # Odoo creates and initializes a missing ``db_name`` itself, so
    # "create" only records the intent — no createdb is run here.
    db_name = parse_explicit_db(extra_args)
    if not db_name and not has_arg(extra_args, "--config", short="-c"):
        db_name = resolve_db_name(base)
        if not db_exists(base, db_name, ctx=ctx, dry_run=dry_run):
            if sys.stdin.isatty() and not dry_run:
                branch = resolve_branch(base, None)
                last_db = get_last_db(base)
                if last_db == db_name:
                    last_db = None
                _action, db_name = _prompt_for_missing_db(
                    base, branch, db_name, last_db, ctx=ctx
                )
            else:
                echo.info(
                    f"Database '{db_name}' does not exist; "
                    "Odoo will create and initialize it."
                )
        elif not dry_run:
            # Only an existing database counts as "used"; a missing one is
            # recorded on a later run, once Odoo has created it.
            set_last_db(base, db_name)

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
