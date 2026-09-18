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
from ..common import find_project_root, has_arg, odoo_http_port
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
from ..operations import Env, Operation, operation, registry
from ..utils.plugin_loader import load_backends
from .helpers import check_run_diagnostics
from .shell_cmd import parse_explicit_db, prepare_env_context


class OdooCommand(click.Command):
    """Click command that appends a Backends section to `osh odoo --help`."""

    def get_params(self, ctx):
        """Append options declared by ``odoo`` operation extensions."""
        params = [*super().get_params(ctx)]
        params.extend(
            param
            for param in registry["odoo"].get_options()
            if isinstance(param, click.Parameter)
        )
        return params

    def format_help_text(self, ctx, formatter):
        """Write the docstring followed by the list of available backends."""
        super().format_help_text(ctx, formatter)
        format_backends_section(formatter, load_backends())


@operation("odoo")
class OdooRun(Operation):
    """`osh odoo` operation — prepare the environment and execute Odoo.

    Extensions override the step methods via the ``@extends`` decorator,
    calling ``super()`` to keep the rest of the chain. Command state is on
    ``self``: ``ctx``, the parsed params (``dry_run``, ``extra_args``, ...)
    plus ``base``, ``backend``, ``diagnostics``, ``db_name`` and
    ``env_spec`` as ``run()`` fills them in.
    """

    dry_run = False
    compose_file = None
    no_dev = False
    no_db_filter = False
    wait_for_exit = None
    extra_args = ()

    @classmethod
    def get_options(cls):
        """Extra ``click.Parameter``s appended to ``osh odoo``'s parameters.

        Extension point — plugins override this via ``@extends`` and append
        to ``super().get_options()``; the parsed values land in
        ``ctx.params`` like regular options.
        """
        return []

    def run(self):
        self.base = find_project_root(required=True)
        self.backend = resolve_backend(self.base)
        self.diagnostics = check_run_diagnostics(
            self.base, self.backend, self.ctx, compose_file=self.compose_file
        )
        self.extra_args = _with_dev_default(
            self.base, self.extra_args, no_dev=self.no_dev
        )
        self.publish_http_port()
        self.db_name = self.resolve_db()
        self.executable = self.resolve_executable()
        self.env_spec = self.build_env_spec()
        self.pre_env()
        self.execute()

    @property
    def has_subcommand(self):
        """Whether ``extra_args`` invokes an Odoo subcommand (e.g. ``shell``)."""
        return bool(self.extra_args) and not self.extra_args[0].startswith("-")

    def publish_http_port(self):
        """Publish the requested -p/--http-port to the backend up front.

        For an Odoo server run, pre-run probes (db_exists, env prep) must
        bring the stack up on the right port instead of the configured one.
        """
        if self.ctx.obj is not None and not self.has_subcommand:
            self.ctx.obj["http_port"] = odoo_http_port(self.extra_args)

    def resolve_db(self):
        """Resolve the database Odoo will use, prompting when it is missing.

        Odoo creates and initializes a missing ``db_name`` itself, so
        "create" only records the intent — no createdb is run here.
        """
        db_name = parse_explicit_db(self.extra_args)
        if not db_name and not has_arg(self.extra_args, "--config", short="-c"):
            db_name = resolve_db_name(self.base)
            if not db_exists(self.base, db_name, ctx=self.ctx, dry_run=self.dry_run):
                if sys.stdin.isatty() and not self.dry_run:
                    branch = resolve_branch(self.base, None)
                    last_db = get_last_db(self.base)
                    if last_db == db_name:
                        last_db = None
                    _action, db_name = _prompt_for_missing_db(
                        self.base, branch, db_name, last_db, ctx=self.ctx
                    )
                else:
                    echo.info(
                        f"Database '{db_name}' does not exist; "
                        "Odoo will create and initialize it."
                    )
            elif not self.dry_run:
                # Only an existing database counts as "used"; a missing one is
                # recorded on a later run, once Odoo has created it.
                set_last_db(self.base, db_name)
        return db_name

    def resolve_executable(self):
        """Return the Odoo executable name for the active backend."""
        if self.backend.host_executable:
            return (
                self.diagnostics.info.get(self.backend.name, {}).get("odoo_executable")
                or "odoo-bin"
            )
        return "odoo"

    def build_env_spec(self):
        """Assemble the ``EnvSpec`` passed to ``backend.env()``.

        Subcommands (e.g. shell, neutralize) do not need dbfilter.
        """
        no_db_filter = self.no_db_filter or self.has_subcommand
        conf_path, env_vars, resolved_db = prepare_env_context(
            self.base,
            self.backend,
            ctx=self.ctx,
            db_name=self.db_name,
            no_db_filter=no_db_filter,
            extra_args=self.extra_args,
            dry_run=self.dry_run,
        )
        if conf_path:
            echo.info(f"Using config: {conf_path}")
        if resolved_db:
            echo.info(f"Using database: {resolved_db}")
        return EnvSpec(
            argv=[self.executable, *self.extra_args],
            env=env_vars,
            db_name=resolved_db,
            config_path=str(conf_path) if conf_path else None,
        )

    def pre_env(self):
        """Run after the EnvSpec is assembled, before ``backend.env()``.

        Extension point — plugins override this via ``@extends``; the parsed
        CLI values (including ``extra_args``, ``dry_run`` and
        plugin-injected options) are in ``self.ctx.params`` and
        ``self.env_spec`` carries the assembled ``EnvSpec``. It runs for
        every ``osh odoo`` invocation — including ``--dry-run`` and
        subcommands — so extensions must self-filter. Raising
        ``click.ClickException`` aborts the run. Since ``osh odoo`` execs
        Odoo, it is also the place to spawn detached sidecar processes that
        must outlive the ``osh`` process itself.
        """

    def execute(self):
        """Execute the assembled command through the backend."""
        wait = self.wait_for_exit
        if wait is None:
            wait = _env_flag("OSH_WAIT")
        self.backend.env(
            self.ctx, self.base, self.env_spec, dry_run=self.dry_run, wait=wait
        )


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
    Env(ctx)["odoo"](
        dry_run=dry_run,
        compose_file=compose_file,
        no_dev=no_dev,
        extra_args=extra_args,
        no_db_filter=no_db_filter,
        wait_for_exit=wait_for_exit,
        **_plugin_params,
    ).run()


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
