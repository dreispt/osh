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
from ..cli_utils import handler_command, long_flag, merge_options, param_values
from ..common import find_project_root
from ..db import resolve_runtime
from ..handlers import CommandHandler
from ..runtimes import Runtime
from ..utils.plugin_loader import load_runtimes


class Stop(CommandHandler):
    """Stop resources that Osh runtimes left running.

    Without arguments, delegates teardown to the project's active runtime:
    the ``host``/``venv`` runtimes terminate a host Odoo process on the
    project's HTTP port, ``docker`` runs ``docker compose down``.

    NAME stops another project's resources: a path to a project directory
    uses that project's recorded runtime; a Compose project name (as shown
    by ``osh stop --all``) downs the matching Docker stack.

    ``--all`` stops every Osh-managed runtime resource: all Osh Compose
    stacks plus host Odoo processes started under an ``.osh`` environment.

    Examples:

    \b
      osh stop                # stop this project's runtime resources
      osh stop other-project  # stop another project's stack by name
      osh stop --all          # stop every Osh-managed runtime resource
    """

    _cli_name = "stop"

    name = None
    stop_all = False

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
    def run(self):
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
        runtime.stop(self.ctx, base, **param_values(self, runtime.get_stop_options()))

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
            runtime_cls().stop_all(self.ctx)

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
        for runtime_cls in load_runtimes().values():
            if runtime_cls.name in selected:
                continue
            for param in runtime_cls.get_stop_options():
                if param_source(param.name) != click.core.ParameterSource.COMMANDLINE:
                    continue
                hint = (
                    "does not apply to --all"
                    if not selected
                    else f"applies to the '{runtime_cls.name}' runtime"
                )
                echo.warning(f"{long_flag(param)} {hint}; ignored.")


stop = handler_command("stop", Stop)
