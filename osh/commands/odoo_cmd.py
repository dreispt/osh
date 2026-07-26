"""`osh odoo` command implementation.

``osh odoo`` runs the project's Odoo executable inside the active target
environment (local virtualenv or Docker container). It is the main entry point
for executing Odoo, including subcommands such as ``shell``, ``neutralize`` and
``scaffold``.

Environment preparation – addons path, database name and dbfilter – is handled
by ``osh env`` via the dynamic config in ``.osh/cache/env``.
"""

import click

from .. import echo
from ..backends import EnvSpec
from ..common import find_project_root
from ..db import resolve_run_target, set_project_config
from ..utils.plugin_loader import load_backends
from .env_cmd import _parse_explicit_db, prepare_env_context
from .helpers import collect_diagnostics


class OdooCommand(click.Command):
    """Click command that appends a Targets section to `osh odoo --help`."""

    def format_help_text(self, ctx, formatter):
        """Write the docstring followed by the list of available backends."""
        super().format_help_text(ctx, formatter)
        _format_odoo_targets(formatter)


def _format_odoo_targets(formatter):
    """Write a Targets section listing each backend name and description."""
    backends = load_backends()
    if not backends:
        return
    records = [
        (name, getattr(backends[name], "description", "") or "")
        for name in sorted(backends)
    ]
    with formatter.section("Targets"):
        formatter.write_dl(records)


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
    help="Execution target: local virtualenv or a plugin backend.",
)
@click.option(
    "--compose-file",
    default=None,
    help="Docker Compose file to use (e.g. devel.yaml for Doodba).",
)
@click.option(
    "--no-db-filter",
    is_flag=True,
    hidden=True,
    help="Do not inject dbfilter into the generated config.",
)
@click.option(
    "--skip-config",
    is_flag=True,
    hidden=True,
    help="Skip generating the dynamic config file.",
)
@click.option(
    "--wait",
    "wait_for_exit",
    is_flag=True,
    hidden=True,
    help="Wait for the command to finish instead of exec/replacing the process.",
)
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def odoo(
    ctx,
    dry_run,
    backend_name,
    compose_file,
    no_db_filter,
    skip_config,
    wait_for_exit,
    extra_args,
):  # noqa: D401
    """Run the project's Odoo executable.

    Extra arguments are passed through to odoo-bin. Odoo subcommands such as
    ``shell``, ``neutralize`` or ``scaffold`` are supported.

    Environment preparation – addons path, database name and dbfilter – is handled
    by ``osh env`` through the dynamic config in ``.osh/cache/env``.

    Examples:

    \b
      osh odoo
      osh odoo -- --http-port=8080 --workers=0
      osh odoo shell
      osh odoo neutralize -d mydb
      osh odoo --target docker --compose-file devel.yaml
    """
    base = find_project_root(required=True)

    backend_name = resolve_run_target(base, backend_name, ctx)
    set_project_config(base, "run", "target", backend_name)

    backends = load_backends()
    backend_cls = backends.get(backend_name)
    if backend_cls is None:
        raise click.ClickException(f"Unknown target: {backend_name}")
    backend = backend_cls()

    diagnostics = collect_diagnostics(
        base,
        backend,
        ctx,
        target=backend_name,
        phase="run",
        compose_file=compose_file,
        sections=backend.diagnose_sections_for_phase("run"),
    )
    for warning_msg in diagnostics.warnings:
        echo.warning(warning_msg)
    if diagnostics.errors:
        raise click.ClickException("\n".join(diagnostics.errors))

    explicit_db = _parse_explicit_db(extra_args)
    db_name = explicit_db or diagnostics.info.get("Project", {}).get("dbname")
    if db_name and not dry_run:
        branch = diagnostics.info.get("Project", {}).get("git_branch", "default")
        set_project_config(base, "db", values={branch: db_name, "last": db_name})

    # Subcommands (e.g. shell, neutralize) do not need dbfilter.
    has_subcommand = extra_args and not extra_args[0].startswith("-")
    if has_subcommand:
        no_db_filter = True

    if backend_name == "local":
        exe = diagnostics.info.get("local", {}).get("odoo_executable")
        executable = exe if exe else "odoo-bin"
    else:
        executable = "odoo-bin"

    conf_path, env_vars, resolved_db = prepare_env_context(
        base,
        backend_name,
        db_name=db_name,
        no_db_filter=no_db_filter,
        skip_config=skip_config,
        extra_args=extra_args,
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
    backend.env(ctx, base, env_spec, dry_run=dry_run, wait=wait_for_exit)
