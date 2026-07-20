"""`osh run` command implementation."""

import click

from ..backends import RunSpec
from ..commons import find_project_root
from ..db import resolve_run_target, set_project_config
from ..diagnostics import collect_diagnostics
from ..echo import get_echo
from ..plugin_loader import load_backends


@click.command(name="run", context_settings=dict(ignore_unknown_options=True))
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
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def run(
    ctx,
    dry_run,
    verbose,
    backend_name,
    compose_file,
    edition,
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
      - Passes ``-d`` and ``--db-filter`` on the command line.

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
        echo.assumptions(f"Using database: {db_name}")
        if not explicit_db:
            db_args.extend(["-d", db_name])
        if not _has_arg(extra_args, "--db-filter"):
            db_args.extend(["--db-filter", f"^{db_name}$"])

    if backend_name == "local":
        exe = diagnostics.info.get("local", {}).get("odoo_executable")

        has_explicit_config = _has_arg(extra_args, "--config", short="-c")

        odoo_conf = base / ".osh" / "odoo.conf"
        if not has_explicit_config:
            odoo_conf.parent.mkdir(parents=True, exist_ok=True)
            if not dry_run and not odoo_conf.exists():
                odoo_conf.touch()
                echo.details(f"Created config file: {odoo_conf}")

        executable = exe
        config_path = None if has_explicit_config else str(odoo_conf)
    else:
        executable = "odoo"
        config_path = None

    argv = [executable]
    argv.extend(db_args)
    argv.extend(extra_args)
    if config_path:
        argv.extend(["--config", config_path, "--save"])

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


def _has_arg(extra_args, long, short=None):
    """Return True if *extra_args* contains the given long (and optional short) option."""
    for arg in extra_args:
        if arg == long or arg.startswith(f"{long}="):
            return True
        if short and (arg == short or arg.startswith(short)):
            return True
    return False
