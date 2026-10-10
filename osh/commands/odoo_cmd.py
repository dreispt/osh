"""`osh odoo` command implementation.

``osh odoo`` runs the project's Odoo executable inside the active target
environment (local host, virtualenv, or Docker container). It is the main entry point
for executing Odoo, including subcommands such as ``shell``, ``neutralize`` and
``scaffold``.

Environment preparation – addons path, database name and dbfilter – is handled
by ``osh exec`` via the dynamic config in ``.osh/cache/env``.
"""

import os
import socket
import sys

import click

from .. import echo
from ..cli_utils import format_runtimes_section, handler_command
from ..common import find_project_root, get_odoo_port, has_arg, odoo_http_port
from ..config import get_user_preference
from ..db import (
    _prompt_for_missing_db,
    db_exists,
    get_last_db,
    get_project_config,
    resolve_branch,
    resolve_db_name,
    resolve_runtime,
    set_last_db,
)
from ..handlers import CommandHandler
from ..runtimes import EnvSpec
from ..utils.plugin_loader import runtime_meta
from ..utils.version import version_major
from .helpers import check_run_diagnostics
from .shell_cmd import parse_explicit_db, prepare_env_context


class OdooRun(CommandHandler):
    """Run the project's Odoo executable.

    Extra arguments are passed through to odoo-bin. Odoo subcommands such as
    ``shell``, ``neutralize`` or ``scaffold`` are supported.

    Environment preparation – addons path, database name and dbfilter – is handled
    by ``osh exec`` through the dynamic config in ``.osh/cache/env``.
    ``ODOO_RC`` and the ``PG*`` connection variables are already exported into
    the subprocess environment.

    Passing an explicit ``--config``/``-c`` argument suppresses the generated
    config, and passing ``--db-filter`` overrides the one ``osh`` injects –
    same as calling ``odoo-bin`` directly.

    Dev mode is on by default: ``--dev=all`` is appended unless you pass
    ``--dev`` yourself or use ``--no-dev``. The default can be changed with
    ``osh config odoo dev <value>`` (``off`` disables the injection).
    With dev mode on, a database Odoo creates on this run gets demo data
    (``--with-demo`` on Odoo 19+, the default before it); pass
    ``--without-demo`` to skip it.

    The execution runtime is the one activated for the project — see
    ``osh init <runtime>`` (e.g. ``osh init docker``).

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

    Extensions subclass ``OdooRun`` and override the step methods,
    calling ``super()`` to keep the rest of the chain. Command state is
    on ``self``: ``ctx``, the parsed params (``dry_run``, ``extra_args``,
    ...) plus ``dev``, ``base``, ``runtime``, ``diagnostics``, ``db_name``
    and ``env_spec`` as ``run()`` fills them in. ``backend`` is a deprecated
    alias of ``runtime``.
    """

    _cli_name = "odoo"
    _cli_context_settings = dict(ignore_unknown_options=True)

    dry_run = False
    compose_file = None
    no_dev = False
    no_db_filter = False
    wait_for_exit = None
    extra_args = ()
    dev = None

    @classmethod
    def format_cli_help(cls, formatter):
        """Append the list of available runtimes to ``osh odoo --help``.

        Uses declared metadata only, so rendering help never imports
        runtime plugins.
        """
        format_runtimes_section(formatter, runtime_meta())

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
    def run(self):
        self.base = find_project_root(required=True)
        self.runtime = self.backend = resolve_runtime(self.base)
        self.diagnostics = check_run_diagnostics(
            self.base, self.runtime, self.ctx, compose_file=self.compose_file
        )
        self.extra_args, self.dev = _with_dev_default(
            self.base, self.extra_args, no_dev=self.no_dev
        )
        self.publish_http_port()
        self.db_name = self.resolve_db()
        self.executable = self.resolve_executable()
        self.env_spec = self.build_env_spec()
        self.check_http_port()
        self.pre_env()
        self.execute()

    @property
    def has_subcommand(self):
        """Whether ``extra_args`` invokes an Odoo subcommand (e.g. ``shell``)."""
        return bool(self.extra_args) and not self.extra_args[0].startswith("-")

    def publish_http_port(self):
        """Publish the requested -p/--http-port to the runtime up front.

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
                    action, db_name = _prompt_for_missing_db(
                        self.base, branch, db_name, last_db, ctx=self.ctx
                    )
                    if action == "create":
                        self.apply_demo_default()
                else:
                    echo.info(
                        f"Database '{db_name}' does not exist; "
                        "Odoo will create and initialize it."
                    )
                    self.apply_demo_default()
            elif not self.dry_run:
                # Only an existing database counts as "used"; a missing one is
                # recorded on a later run, once Odoo has created it.
                set_last_db(self.base, db_name)
        return db_name

    def apply_demo_default(self):
        """Prefer demo data for a database Odoo is about to create.

        Dev-mode runs get demo data on the freshly initialised database —
        far more useful for development. Odoo 19 needs the new
        ``--with-demo`` flag (demo is off by default since 19.0); older
        versions load it anyway, so only the opt-out hint matters there.
        Skipped for subcommands, runs without dev mode, explicit
        ``--with-demo``/``--without-demo`` arguments, and projects that
        record no Odoo version (the flag name differs by version).
        """
        if self.has_subcommand or not self.dev:
            return
        if has_arg(self.extra_args, "--with-demo") or has_arg(
            self.extra_args, "--without-demo"
        ):
            return
        major = version_major(get_project_config(self.base, "init", "version"))
        if major is None:
            return
        if major >= 19:
            self.extra_args = (*self.extra_args, "--with-demo")
        echo.info(
            "New database will be initialized with demo data (dev mode is on). "
            "Pass --without-demo to skip it."
        )

    def resolve_executable(self):
        """Return the Odoo executable name for the active runtime."""
        if self.runtime.host_executable:
            return (
                self.diagnostics.info.get(self.runtime.name, {}).get("odoo_executable")
                or "odoo-bin"
            )
        return "odoo"

    def build_env_spec(self):
        """Assemble the ``EnvSpec`` passed to ``runtime.env()``.

        Subcommands (e.g. shell, neutralize) do not need dbfilter.
        """
        no_db_filter = self.no_db_filter or self.has_subcommand
        conf_path, env_vars, resolved_db = prepare_env_context(
            self.base,
            self.runtime,
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
        command = self.runtime.odoo_command(self.base) or [self.executable]
        return EnvSpec(
            argv=[*command, *self.extra_args],
            env=env_vars,
            db_name=resolved_db,
            config_path=str(conf_path) if conf_path else None,
        )

    def check_http_port(self):
        """Fail fast with a helpful hint when the HTTP port is taken.

        Otherwise Odoo starts up only to die with its plain "Address
        already in use" — ``osh stop --list`` identifies the holder, so
        pointing there saves the hunt. Only applies to runtimes that run
        Odoo on the host (a container runtime publishes the port through
        its stack, where a held port means a different problem), to
        server runs, and when no explicit ``--config`` takes over. Runs
        that never bind HTTP are skipped.
        """
        if self.dry_run or self.has_subcommand or not self.runtime.host_executable:
            return
        if has_arg(self.extra_args, "--config", short="-c") or any(
            arg in self.extra_args for arg in _NO_SERVER_ARGS
        ):
            return
        port = self._resolved_http_port()
        if port and _port_in_use(port):
            raise click.ClickException(
                f"Port {port} is already in use.\n"
                "Run 'osh stop --list' to see which process holds it, "
                "or pass --http-port=<port> to use another one."
            )

    def _resolved_http_port(self):
        """Return the port Odoo will bind, or None when HTTP is off."""
        value = odoo_http_port(self.extra_args)
        if value is not None:
            try:
                port = int(str(value).strip())
            except ValueError:
                return None
            return port or None
        return get_odoo_port(self.base) or None

    def pre_env(self):
        """Run after the EnvSpec is assembled, before ``runtime.env()``.

        Extension point — extending subclasses override this; the parsed
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
        """Execute the assembled command through the runtime."""
        wait = self.wait_for_exit
        if wait is None:
            wait = _env_flag("OSH_WAIT")
        self.runtime.env(
            self.ctx, self.base, self.env_spec, dry_run=self.dry_run, wait=wait
        )


odoo = handler_command("odoo", OdooRun)


_NO_SERVER_ARGS = ("--version", "--help", "-h", "--stop-after-init", "--no-http")


def _port_in_use(port, host="127.0.0.1"):
    """Return True when *host:port* already accepts a connection."""
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _env_flag(name):
    """Return True when environment variable *name* holds a truthy value."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _with_dev_default(base, extra_args, *, no_dev):
    """Append the configured ``--dev`` default unless the user passed one.

    Returns ``(extra_args, dev)`` — *dev* is the effective dev-mode value
    for the run, or None when dev mode is off.
    """
    found, dev = _dev_arg(extra_args)
    if found:
        return extra_args, dev
    dev = _resolve_dev_default(base, no_dev=no_dev)
    if dev is None:
        return extra_args, None
    return (*extra_args, f"--dev={dev}"), dev


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


def _dev_arg(extra_args):
    """Return ``(found, value)`` for the last ``--dev`` in *extra_args*.

    A bare ``--dev`` means ``all``; an off-word value resolves to None.
    """
    for arg in reversed(tuple(extra_args)):
        if arg == "--dev":
            return True, "all"
        if arg.startswith("--dev="):
            value = arg.split("=", 1)[1]
            off = value.strip().lower() in ("off", "none", "false", "0")
            return True, None if off else value
    return False, None
