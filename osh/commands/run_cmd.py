"""`osh run` command implementation."""

import click

from ..backends import RunSpec
from ..commons import _has_arg, find_project_root, resolve_config_file
from ..db import resolve_run_target, set_project_config
from ..diagnostics import collect_diagnostics
from ..echo import get_echo
from ..plugin_loader import load_backends


class RunCommand(click.Command):
    """Click command that appends a Targets section to `osh run --help`."""

    def format_help_text(self, ctx, formatter):
        """Write the docstring followed by the list of available backends."""
        super().format_help_text(ctx, formatter)
        _format_run_targets(formatter)


def _format_run_targets(formatter):
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
    name="run", cls=RunCommand, context_settings=dict(ignore_unknown_options=True)
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the assembled command without executing it.",
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Print extra details about the generated command.",
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
    "--edition",
    default=None,
    help="Odoo edition for addons path (ce/ee/sh).",
)
@click.option(
    "--no-db-filter",
    is_flag=True,
    hidden=True,
    help="Do not inject --db-filter (for odoo command).",
)
@click.option(
    "--skip-config",
    is_flag=True,
    hidden=True,
    help="Skip config file (for odoo subcommands).",
)
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def run(
    ctx,
    dry_run,
    verbose,
    backend_name,
    compose_file,
    edition,
    no_db_filter,
    skip_config,
    extra_args,
):  # noqa: D401
    """Run the project's Odoo executable.

    Extra arguments are passed through to odoo-bin.

    Automatic configuration:

    \b
      - Discovers --addons-path from project addon directories and passes it
        on the odoo-bin command line (local host paths or container paths for Docker).
      - Uses the config file in ``.osh/odoo.conf``. If the project root has an
        ``.odoorc`` file, it is copied to ``.osh/odoo.conf`` during init.
        The config file is hackable and automatically generated.
      - Remembers the database name per git branch (including explicit ``-d``
        / ``--database`` values for later runs).
      - Passes ``-d`` and ``--db-filter`` on the command line (unless --no-db-filter).

    Examples:

    \b
      osh run
      osh run -- --http-port=8080 --workers=0
      osh run --dry-run
      osh run --verbose
      osh run --target docker --compose-file devel.yaml
    """

    base = find_project_root(required=True)

    echo = get_echo(ctx, base, verbose_override=verbose)

    backend_name = resolve_run_target(base, backend_name, ctx)
    set_project_config(base, "run", "target", backend_name)

    backends = load_backends()
    backend_cls = backends.get(backend_name)
    if backend_cls is None:
        raise click.ClickException(f"Unknown run target: {backend_name}")
    backend = backend_cls()

    diagnostics = collect_diagnostics(
        base,
        backend,
        ctx,
        target=backend_name,
        phase="run",
        compose_file=compose_file,
        edition=edition,
        sections=backend.diagnose_sections_for_phase("run"),
    )
    for warning in diagnostics.warnings:
        echo.warning(warning)
    if diagnostics.errors:
        raise click.ClickException("\n".join(diagnostics.errors))

    explicit_db = _parse_explicit_db(extra_args)
    db_name = explicit_db or diagnostics.info.get("Project", {}).get("dbname")
    if db_name and not dry_run:
        branch = diagnostics.info.get("Project", {}).get("git_branch", "default")
        set_project_config(base, "db", values={branch: db_name, "last": db_name})

    db_args = []
    if db_name:
        echo.info(f"Using database: {db_name}")
        if not explicit_db:
            db_args.extend(["-d", db_name])
        if not no_db_filter and not _has_arg(extra_args, "--db-filter"):
            db_args.extend(["--db-filter", f"^{db_name}$"])

    if backend_name == "local":
        exe = diagnostics.info.get("local", {}).get("odoo_executable")
        config_path = (
            None
            if skip_config
            else resolve_config_file(base, extra_args, for_run=not dry_run)
        )
        if config_path:
            echo.info(f"Using config: {config_path}")
        executable = exe if exe else "odoo-bin"
    else:
        executable = "odoo-bin"
        config_path = None

    argv = [executable]
    if config_path:
        argv.append(f"--config={config_path}")
    argv.extend(db_args)
    argv.extend(extra_args)

    run_spec = RunSpec(
        argv=argv,
        executable=executable,
        db_name=db_name,
        config_path=config_path,
        extra_args=list(extra_args),
    )
    backend.run(ctx, base, run_spec, dry_run=dry_run, verbose=verbose, edition=edition)


def _parse_explicit_db(extra_args):
    """Return the database name explicitly passed via -d/--database, if any."""
    for i, arg in enumerate(extra_args):
        if arg in ("-d", "--database"):
            value = extra_args[i + 1] if i + 1 < len(extra_args) else ""
            return value if value and not value.startswith("-") else None
        if arg.startswith("-d") and len(arg) > 2:
            return arg[2:]
        if arg.startswith("--database="):
            return arg.split("=", 1)[1]
    return None
