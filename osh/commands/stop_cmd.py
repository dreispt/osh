"""`osh stop` command implementation.

``osh stop`` stops whatever the project's active runtime left running — a
host Odoo process on the project's HTTP port for ``host``/``venv``, a
``docker compose down`` for ``docker``. ``osh stop <name>`` stops another
project's resources without cd'ing there, and ``osh stop --all`` stops
every Osh-managed runtime resource on the machine.
"""

from pathlib import Path

import click

from .. import echo
from ..cli_utils import (
    emit_json,
    handler_command,
    long_flag,
    merge_options,
    param_values,
)
from ..common import env_root, find_project_root
from ..db import resolve_runtime
from ..handlers import CommandHandler
from ..runtimes import Runtime
from ..utils.plugin_loader import load_runtimes


class Stop(CommandHandler):
    """Stop resources that Osh runtimes left running.

    Without arguments, delegates teardown to the project's active runtime:
    the ``host``/``venv`` runtimes terminate a host Odoo process on the
    project's HTTP port — ``-p/--http-port`` targets a different port, for
    servers started with ``osh odoo -p`` — ``docker`` runs ``docker
    compose down``.

    NAME stops another project's resources: a path to a project directory
    uses that project's recorded runtime; a Compose project name (as shown
    by ``osh stop --all``) downs the matching Docker stack.

    ``--all`` stops every Osh-managed runtime resource: all Osh Compose
    stacks plus host Odoo processes started under an ``.osh`` environment.

    ``--list`` reports what would be stopped without stopping anything:
    who holds the project's Odoo port — including holders the active
    runtime cannot stop, like a Docker container publishing it — and every
    resource ``--all`` would tear down.

    Examples:

    \b
      osh stop                # stop this project's runtime resources
      osh stop -p 8169        # stop the Odoo holding a non-default port
      osh stop --list         # report port holders and would-be targets
      osh stop other-project  # stop another project's stack by name
      osh stop --all          # stop every Osh-managed runtime resource
      osh stop --all --list   # list every Osh-managed runtime resource
    """

    _cli_name = "stop"

    name = None
    stop_all = False
    list_only = False
    as_json = False

    @classmethod
    def get_options(cls):
        """``osh stop`` params plus every runtime's stop options."""
        return merge_options(
            super().get_options(),
            (
                param
                for runtime_cls in load_runtimes().values()
                for param in runtime_cls.get_stop_options()
            ),
        )

    @click.argument("name", required=False)
    @click.option(
        "--all",
        "stop_all",
        is_flag=True,
        help="Stop every Osh-managed runtime resource, not just this "
        "project's; lists the stacks and processes found.",
    )
    @click.option(
        "--list",
        "list_only",
        is_flag=True,
        help="Report what would be stopped — holders of the project's "
        "Odoo port and the resources stop would tear down — without "
        "stopping anything.",
    )
    @click.option(
        "--json",
        "as_json",
        is_flag=True,
        help="With --list, print the report as JSON for machine "
        "consumption. Only supported for this project's listing.",
    )
    def run(self):
        if self.as_json and not self.list_only:
            raise click.UsageError("--json requires --list.")
        if self.as_json and (self.stop_all or self.name is not None):
            raise click.UsageError(
                "--json is only supported for 'osh stop --list' "
                "without NAME or --all."
            )
        if self.stop_all:
            if self.name:
                raise click.UsageError("NAME and --all are mutually exclusive.")
            self._warn_foreign_stop_options(set())
            self._stop_all()
            return
        if self.name is not None:
            self._stop_named()
            return
        base = find_project_root(required=True)
        self._stop_project(base)

    def _stop_project(self, base):
        """Delegate teardown to the active runtime of project *base*."""
        runtime = resolve_runtime(base)
        self._warn_foreign_stop_options({runtime.name})
        options = param_values(self, runtime.get_stop_options())
        if self.list_only:
            if self.as_json:
                self._list_project_json(runtime, base, options)
                return
            runtime.stop(self.ctx, base, dry_run=True, **options)
            port = options.get("http_port") or runtime.odoo_port(base)
            self._report_foreign_port_holders(runtime, base, port)
            return
        runtime.stop(self.ctx, base, **options)

    def _list_project_json(self, runtime, base, options):
        """Emit the structured ``osh stop --list --json`` report."""
        port = options.get("http_port") or runtime.odoo_port(base)
        emit_json(
            {
                "runtime": runtime.name,
                "project": str(base),
                "env_root": str(env_root(base)),
                "port": port,
                "report": runtime.stop_report(self.ctx, base, **options),
                "port_holders": self._port_holder_entries(base, port),
            }
        )

    def _port_holder_entries(self, base, port):
        """Structured port holders across every runtime, deduplicated.

        Unlike the text ``--list`` — where the active runtime's own
        ``stop(dry_run=True)`` already reported its holders — the JSON
        report must include every runtime's view, so nothing seeds the
        dedup set but the no-op base implementation.
        """
        if port is None:
            return []
        seen = {Runtime.port_holder_entries}
        entries = []
        for runtime_cls in load_runtimes().values():
            impl = runtime_cls.port_holder_entries
            if impl in seen:
                continue
            seen.add(impl)
            entries.extend(runtime_cls().port_holder_entries(self.ctx, port, base))
        return entries

    def _report_foreign_port_holders(self, runtime, base, port):
        """Report holders of *port* the active runtime can't see.

        The runtime's own ``stop(dry_run=True)`` covers the holders it can
        act on; this adds what other runtimes see — e.g. a Docker stack
        publishing the port of a host-runtime project — so ``--list``
        gives the complete picture of what blocks the port. Holder
        implementations are deduplicated like ``--all``'s teardown.
        """
        if port is None:
            return
        # The active runtime's own view is skipped — its
        # ``stop(dry_run=True)`` already reported it. Runtimes implement
        # ``port_holder_entries`` (structured) or the legacy
        # ``port_holders`` text hook; rendered lines always come from
        # ``port_holders``, whose base implementation formats the
        # entries. Implementations deduplicate by function so subclasses
        # sharing one (``venv`` inherits ``host``'s) report once.
        seen_entries = {type(runtime).port_holder_entries}
        seen_holders = {type(runtime).port_holders}
        lines = []
        for runtime_cls in load_runtimes().values():
            entries_impl = runtime_cls.port_holder_entries
            holders_impl = runtime_cls.port_holders
            if entries_impl not in seen_entries:
                seen_entries.add(entries_impl)
            elif holders_impl not in seen_holders:
                seen_holders.add(holders_impl)
            else:
                continue
            lines.extend(runtime_cls().port_holders(self.ctx, port, base))
        if lines:
            echo.info(f"Port {port} is also held by:")
            for line in lines:
                echo.info(f"  {line}")

    def _stop_named(self):
        """Stop another project's resources, by path or by stack name."""
        target = Path(self.name).expanduser()
        if target.is_dir() and (target / ".osh").is_dir():
            self._stop_project(target.resolve())
            return
        stoppers = self._named_stoppers()
        self._warn_foreign_stop_options({cls.name for cls in stoppers})
        error = None
        for runtime_cls in stoppers:
            try:
                runtime = runtime_cls()
                runtime.stop_by_name(
                    self.ctx,
                    self.name,
                    dry_run=self.list_only,
                    **param_values(self, runtime.get_stop_options()),
                )
                return
            except click.ClickException as exc:
                error = exc
        if error is not None:
            raise error
        raise click.ClickException(
            f"No Osh-managed runtime resources found for '{self.name}'. "
            "See 'osh stop --all' for what is running."
        )

    def _stop_all(self):
        """Stop every Osh-managed resource of every registered runtime.

        Runtimes sharing a teardown — ``venv`` inherits the host's process
        sweep — run it once: implementations are deduplicated by function.
        """
        seen = set()
        for runtime_cls in load_runtimes().values():
            stop_all = runtime_cls.stop_all
            if stop_all in seen or stop_all is Runtime.stop_all:
                continue
            seen.add(stop_all)
            runtime_cls().stop_all(self.ctx, dry_run=self.list_only)

    def _named_stoppers(self):
        """Return the runtime classes that override ``stop_by_name``."""
        return [
            cls
            for cls in load_runtimes().values()
            if cls.stop_by_name is not Runtime.stop_by_name
        ]

    def _warn_foreign_stop_options(self, selected):
        """Warn about stop options owned by runtimes other than *selected*.

        *selected* is the set of runtime names the options are forwarded
        to — empty for ``--all``, which accepts no runtime options.
        """
        param_source = getattr(self.ctx, "get_parameter_source", None)
        if param_source is None:
            return
        runtimes = load_runtimes().values()
        # Options the selected runtime accepts — including inherited ones,
        # like ``venv`` sharing the host runtime's ``-p`` — must not warn.
        accepted = {
            param.name
            for runtime_cls in runtimes
            if runtime_cls.name in selected
            for param in runtime_cls.get_stop_options()
        }
        for runtime_cls in runtimes:
            if runtime_cls.name in selected:
                continue
            for param in runtime_cls.get_stop_options():
                if param.name in accepted:
                    continue
                if param_source(param.name) != click.core.ParameterSource.COMMANDLINE:
                    continue
                hint = (
                    "does not apply to --all"
                    if not selected
                    else f"applies to the '{runtime_cls.name}' runtime"
                )
                echo.warning(f"{long_flag(param)} {hint}; ignored.")


stop = handler_command("stop", Stop)
