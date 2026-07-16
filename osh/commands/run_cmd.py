"""`osh run` command implementation."""

from __future__ import annotations

import os

import click

from ..db import _resolve_db_name
from ..utils import _build_addons_paths, _find_odoo_executable, _find_project_root


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
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def run(
    ctx: click.Context,
    dry_run: bool,
    verbose: bool,
    extra_args: tuple[str, ...],
) -> None:  # noqa: D401
    """Run the project's Odoo executable.

    Extra arguments are passed through to odoo-bin.

    Automatic configuration:

    \b
      - Discovers --addons-path from project addon directories and passes it
        on the odoo-bin command line.
      - If no explicit --config/-c is provided, creates ``.osh/odoo.conf`` and
        passes ``--config .osh/odoo.conf --save`` so Odoo persists the computed
        configuration for later manual use.
      - Remembers the database name per git branch.
      - Passes ``-d`` and ``--db-filter`` on the command line.

    Examples:

    \b
      osh run
      osh run -- --http-port=8080 --workers=0
      osh run --dry-run
      osh run --verbose
    """

    base = _find_project_root(required=True)
    exe = _find_odoo_executable(base, required=True)

    # Determine computed configuration unless the user supplied an explicit config.
    has_explicit_config = any(
        arg.startswith("--config") or arg.startswith("-c") for arg in extra_args
    )

    if not any(arg.startswith("--addons-path") for arg in extra_args):
        addons_paths = _build_addons_paths(base, include_themes=True)
    else:
        addons_paths = []

    db_name = None
    if not any(
        arg.startswith("-d") or arg.startswith("--database") for arg in extra_args
    ):
        db_name = _resolve_db_name(base, verbose)

    # Build the computed addons_path and database arguments. addons_path is
    # always passed on the command line so manual edits to .osh/odoo.conf are
    # not required. Database options are kept on the command line.
    addons_path_args: list[str] = []
    if addons_paths:
        addons_path_str = ",".join(str(p) for p in addons_paths)
        if verbose:
            click.echo(f"Using addons path: {addons_path_str}", err=True)
        addons_path_args.extend(["--addons-path", addons_path_str])

    db_args: list[str] = []
    if db_name:
        if verbose:
            click.echo(f"Using database: {db_name}", err=True)
        db_args.extend(["-d", db_name])
        if not any(arg.startswith("--db-filter") for arg in extra_args):
            db_args.extend(["--db-filter", f"^{db_name}$"])

    args: list[str] = [exe]
    args.extend(addons_path_args)
    args.extend(db_args)
    args.extend(extra_args)

    # When no explicit --config is provided, ensure .osh/odoo.conf exists and
    # pass --config/--save so Odoo persists the computed configuration. This lets
    # users reuse the file for manual odoo-bin invocations or hand-edited params.
    odoo_conf = base / ".osh" / "odoo.conf"
    if not has_explicit_config:
        odoo_conf.parent.mkdir(parents=True, exist_ok=True)
        if not dry_run and not odoo_conf.exists():
            odoo_conf.touch()
        args.extend(["--config", str(odoo_conf), "--save"])

    if dry_run:
        click.echo(f"Would run: {' '.join(args)}", err=True)
        return

    if verbose:
        click.echo(f"Running: {' '.join(args)}", err=True)
    else:
        click.echo(f"Running {' '.join(args)}", err=True)

    try:
        os.execvp(exe, args)  # replace current process
    except Exception as exc:  # pragma: no cover
        raise click.ClickException(str(exc))
