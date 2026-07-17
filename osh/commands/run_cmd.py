"""`osh run` command implementation."""

from __future__ import annotations

import os
from pathlib import Path

import click

from ..backends import RunBackend
from ..db import _record_run_target, _resolve_db_name, _resolve_run_target
from ..plugin_loader import load_backends
from ..utils import _build_addons_paths, _find_odoo_executable, _find_project_root


class LocalRunBackend(RunBackend):
    """Default ``osh run`` backend: execute odoo-bin on the host."""

    name = "local"
    label = "Local virtualenv"

    def run(
        self,
        ctx: click.Context,
        base: Path,
        args: list[str],
        *,
        dry_run: bool,
        verbose: bool,
    ) -> None:
        """Replace the current process with the assembled odoo-bin command."""
        if dry_run:
            click.echo(f"Would run: {' '.join(args)}", err=True)
            return

        if verbose:
            click.echo(f"Running: {' '.join(args)}", err=True)
        else:
            click.echo(f"Running {' '.join(args)}", err=True)

        try:
            os.execvp(args[0], args)
        except Exception as exc:  # pragma: no cover
            raise click.ClickException(str(exc))


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
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def run(
    ctx: click.Context,
    dry_run: bool,
    verbose: bool,
    backend_name: str,
    compose_file: str | None,
    extra_args: tuple[str, ...],
) -> None:  # noqa: D401
    """Run the project's Odoo executable.

    Extra arguments are passed through to odoo-bin.

    Automatic configuration:

    \b
      - Discovers --addons-path from project addon directories and passes it
        on the odoo-bin command line (local target only).
      - If no explicit --config/-c is provided, creates ``.osh/odoo.conf`` and
        passes ``--config .osh/odoo.conf --save`` so Odoo persists the computed
        configuration for later manual use (local target only).
      - Remembers the database name per git branch.
      - Passes ``-d`` and ``--db-filter`` on the command line.

    Examples:

    \b
      osh run
      osh run -- --http-port=8080 --workers=0
      osh run --dry-run
      osh run --verbose
      osh run --target docker --compose-file devel.yaml
    """

    base = _find_project_root(required=True)

    backend_name = _resolve_run_target(base, backend_name, ctx)
    _record_run_target(base, backend_name)

    run_backends = load_backends("run")
    run_backends.setdefault("local", LocalRunBackend)
    backend_cls = run_backends.get(backend_name)
    if backend_cls is None:
        raise click.ClickException(f"Unknown run target: {backend_name}")
    backend = backend_cls()

    explicit_db: str | None = None
    for i, arg in enumerate(extra_args):
        if arg in ("-d", "--database"):
            explicit_db = extra_args[i + 1] if i + 1 < len(extra_args) else None
        elif arg.startswith("-d"):
            explicit_db = arg[2:]
        elif arg.startswith("--database="):
            explicit_db = arg.split("=", 1)[1]

    db_name = explicit_db or _resolve_db_name(base, verbose)

    db_args: list[str] = []
    if db_name:
        if verbose:
            click.echo(f"Using database: {db_name}", err=True)
        if not explicit_db:
            db_args.extend(["-d", db_name])
        if not any(arg.startswith("--db-filter") for arg in extra_args):
            db_args.extend(["--db-filter", f"^{db_name}$"])

    if backend_name == "local":
        exe = _find_odoo_executable(base, required=True)

        has_explicit_config = any(
            arg.startswith("--config") or arg.startswith("-c") for arg in extra_args
        )

        if not any(arg.startswith("--addons-path") for arg in extra_args):
            addons_paths = _build_addons_paths(base, include_themes=True)
        else:
            addons_paths = []

        addons_path_args: list[str] = []
        if addons_paths:
            addons_path_str = ",".join(str(p) for p in addons_paths)
            if verbose:
                click.echo(f"Using addons path: {addons_path_str}", err=True)
            addons_path_args.extend(["--addons-path", addons_path_str])

        odoo_conf = base / ".osh" / "odoo.conf"
        if not has_explicit_config:
            odoo_conf.parent.mkdir(parents=True, exist_ok=True)
            if not dry_run and not odoo_conf.exists():
                odoo_conf.touch()

        args: list[str] = [exe]
        args.extend(addons_path_args)
        args.extend(db_args)
        args.extend(extra_args)
        if not has_explicit_config:
            args.extend(["--config", str(odoo_conf), "--save"])
    else:
        # Non-local backends treat args[0] as a placeholder and use args[1:]
        # as the Odoo command-line arguments.
        args = ["odoo"]
        args.extend(db_args)
        args.extend(extra_args)

    backend.run(ctx, base, args, dry_run=dry_run, verbose=verbose)
